import os
import hmac
import hashlib
import json
import urllib.parse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# 1. Загружаем переменные окружения на самом верху до импорта bot.py
load_dotenv(override=True)

from fastapi import FastAPI, HTTPException, Request, Header, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from supadns import create_smart_client

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from bot import bot, dp  # Импортируем бота из bot.py

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TOME_SECRET_KEY = os.getenv("TOME_SECRET_KEY")
PAYMASTER_PROVIDER_TOKEN = os.getenv("PAYMASTER_PROVIDER_TOKEN")
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not all([SUPABASE_URL, SUPABASE_KEY, TOME_SECRET_KEY, PAYMASTER_PROVIDER_TOKEN, BOT_TOKEN]):
    raise ValueError("Не все обязательные переменные окружения заданы. Проверьте ваш .env файл.")


def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_smart_client(url, key)


def verify_telegram_init_data(init_data: str) -> dict | None:
    """
    Проверяет подлинность Telegram WebApp initData через HMAC-SHA256 и защиту от replay-атак (auth_date).
    
    1. Извлекает и отделяет параметр hash.
    2. Сортирует оставшиеся параметры по алфавиту и собирает строку проверки (data_check_string).
    3. Генерирует секретный ключ: hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest().
    4. Вычисляет HMAC-хэш и сравнивает его с полученным hash через hmac.compare_digest.
    5. Проверяет auth_date на давность (не более 86400 секунд / 24 часов).
    6. В случае успеха парсит JSON из поля user и возвращает словарь с проверенными данными.
    """
    if not init_data or not BOT_TOKEN:
        return None
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed_data.pop("hash", None)
        if not received_hash:
            return None

        # Сортировка оставшихся параметров по алфавиту в формате key=value через перевод строки \n
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed_data.items(), key=lambda x: x[0])
        )

        # Вычисление секретного ключа от BOT_TOKEN
        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode("utf-8"),
            hashlib.sha256
        ).digest()

        # Вычисление HMAC-SHA256 хэша
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        # Безопасное сравнение хэшей для защиты от timing attacks
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        # Защита от Replay-атаки: проверка времени создания auth_date (максимум 24 часа / 86400 сек)
        auth_date_raw = parsed_data.get("auth_date")
        if not auth_date_raw:
            return None

        try:
            auth_date = int(auth_date_raw)
        except (ValueError, TypeError):
            return None

        current_timestamp = int(datetime.now(timezone.utc).timestamp())
        if current_timestamp - auth_date > 86400 or auth_date > current_timestamp + 1800:
            return None

        # Извлечение и парсинг данных пользователя
        if "user" in parsed_data:
            user_raw = parsed_data["user"]
            if isinstance(user_raw, str):
                return json.loads(user_raw)
            return user_raw

        return parsed_data
    except Exception as e:
        print(f"Ошибка валидации Telegram initData: {e}")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Жизненный цикл FastAPI. Запускает бота при старте сервера и останавливает при выключении.
    """
    print("🚀 Запуск Telegram-бота в фоне...")
    # Удаляем вебхуки и пропускаем старые апдейты
    await bot.delete_webhook(drop_pending_updates=True)
    # Запускаем поллинг как фоновую задачу
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    yield  # В этот момент FastAPI обрабатывает запросы
    
    print("🛑 Остановка Telegram-бота...")
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass


# Инициализация Rate Limiter (SlowAPI) по IP-адресу
limiter = Limiter(key_func=get_remote_address)

# Инициализация приложения FastAPI с lifespan
app = FastAPI(title="Telegram WebApp Backend API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем запросы с любых доменов
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/check-subscription")
@limiter.limit("60/minute")
async def check_subscription(
    request: Request,
    response: Response,
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data")
):
    """
    Безопасная проверка статуса подписки пользователя.
    Принимает заголовок X-Telegram-Init-Data, валидирует подпись и извлекает проверенный telegram_id.
    Для новых пользователей автоматически активирует 5-дневный триал-период.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Отсутствует заголовок X-Telegram-Init-Data")

    user_data = verify_telegram_init_data(x_telegram_init_data)
    if not user_data or "id" not in user_data:
        raise HTTPException(status_code=401, detail="Недействительная подпись Telegram initData")

    telegram_id = user_data["id"]

    try:
        supabase = get_supabase()
        response = supabase.table("subscriptions").select("subscription_until, trial_used").eq("telegram_id", telegram_id).execute()
        data = response.data
        now_utc = datetime.now(timezone.utc)
        
        # Если записи о пользователе нет в БД (первый вход)
        if not data:
            trial_until = now_utc + timedelta(days=5)
            supabase.table("subscriptions").insert({
                "telegram_id": telegram_id,
                "subscription_until": trial_until.isoformat(),
                "trial_used": True
            }).execute()
            return {
                "active": True,
                "is_trial": True,
                "expires_at": trial_until.strftime("%Y-%m-%d %H:%M")
            }
            
        record = data[0]
        sub_until_str = record.get("subscription_until")
        trial_used = bool(record.get("trial_used", False))
        
        # Если поле subscription_until пустое
        if not sub_until_str:
            return {"active": False, "is_trial": False, "expires_at": None}
            
        # Парсим дату (Supabase возвращает ISO строку)
        # Заменяем 'Z' на '+00:00' для корректной работы fromisoformat
        sub_until = datetime.fromisoformat(sub_until_str.replace("Z", "+00:00"))
        
        # Сравниваем дату окончания с текущим временем
        if sub_until > now_utc:
            return {
                "active": True,
                "is_trial": trial_used,
                "expires_at": sub_until.strftime("%Y-%m-%d %H:%M")
            }
        else:
            return {
                "active": False,
                "is_trial": False,
                "expires_at": sub_until.strftime("%Y-%m-%d %H:%M")
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/webhook/tome")
@limiter.limit("30/minute")
async def tome_webhook(request: Request, x_tome_signature: str = Header(None)):
    """
    Вебхук для получения оповещений об успешной оплате от Tome.ru.
    """
    raw_body = await request.body()
    
    # 1. Безопасная проверка сигнатуры / хэша
    if not x_tome_signature:
        raise HTTPException(status_code=400, detail="Missing signature header")
        
    expected_signature = hmac.new(
        TOME_SECRET_KEY.encode('utf-8'),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    
    # Безопасное сравнение строк (защита от Timing Attacks)
    if not hmac.compare_digest(expected_signature, x_tome_signature):
        raise HTTPException(status_code=403, detail="Invalid signature")
    
    # 2. Извлечение данных из тела запроса
    try:
        payload = await request.json()
        raw_telegram_id = payload.get("telegram_id")
        days = payload.get("days", 30)  # Период подписки в днях
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
        
    if raw_telegram_id is None:
        raise HTTPException(status_code=400, detail="telegram_id is required in payload")

    try:
        telegram_id = int(raw_telegram_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid telegram_id: must be an integer")

    try:
        days = int(days)
    except (ValueError, TypeError):
        days = 30

    now_utc = datetime.now(timezone.utc)
    added_timedelta = timedelta(days=days)

    try:
        supabase = get_supabase()
        # Ищем пользователя в БД
        response = supabase.table("subscriptions").select("subscription_until").eq("telegram_id", telegram_id).execute()
        data = response.data

        if data:
            # Пользователь найден
            sub_until_str = data[0].get("subscription_until")
            if sub_until_str:
                current_sub_until = datetime.fromisoformat(sub_until_str.replace("Z", "+00:00"))
                
                # Если подписка активна, прибавляем дни к дате окончания.
                # Иначе прибавляем дни к текущему времени.
                if current_sub_until > now_utc:
                    new_sub_until = current_sub_until + added_timedelta
                else:
                    new_sub_until = now_utc + added_timedelta
            else:
                new_sub_until = now_utc + added_timedelta
                
            # Обновляем существующую запись
            supabase.table("subscriptions").update({
                "subscription_until": new_sub_until.isoformat(),
                "trial_used": True
            }).eq("telegram_id", telegram_id).execute()
            
        else:
            # Пользователя нет, создаем новую запись
            new_sub_until = now_utc + added_timedelta
            supabase.table("subscriptions").insert({
                "telegram_id": telegram_id,
                "subscription_until": new_sub_until.isoformat(),
                "trial_used": True
            }).execute()
            
        # Возвращаем статус 200 OK как ожидает большинство платежных систем
        return {"status": "ok"}
        
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
@app.get("/index.html")
async def serve_index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))
