import os
import hmac
import hashlib
import json
import urllib.parse
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# 1. Загружаем переменные окружения на самом верху до импорта bot.py
load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Request, Header, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from supadns import create_smart_client

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from bot import bot, dp  # Импортируем бота из bot.py
from platega_service import create_platega_payment, verify_platega_signature

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not all([SUPABASE_URL, SUPABASE_KEY, BOT_TOKEN]):
    raise ValueError("Не все обязательные переменные окружения заданы. Проверьте ваш .env файл.")

# Набор обработанных идентификаторов платежей для предотвращения повторного начисления (идемпотентность)
PROCESSED_PAYMENTS: set[str] = set()


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


SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN")
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME", "").replace("@", "").strip()
RUN_SUPPORT_IN_MAIN = os.getenv("RUN_SUPPORT_IN_MAIN", "false").lower() in ("true", "1", "yes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Жизненный цикл FastAPI. Запускает основного бота и опционально бота техподдержки в фоне.
    По умолчанию RUN_SUPPORT_IN_MAIN=false, так как support_bot работает автономно через systemd.
    """
    print("🚀 Запуск Telegram-бота в фоне...")
    # Удаляем вебхуки и пропускаем старые апдейты
    await bot.delete_webhook(drop_pending_updates=True)
    # Запускаем поллинг как фоновую задачу
    polling_task = asyncio.create_task(dp.start_polling(bot))

    support_polling_task = None
    if SUPPORT_BOT_TOKEN and RUN_SUPPORT_IN_MAIN:
        try:
            from support_bot import support_bot, support_dp
            if support_bot:
                print("🚀 Запуск Telegram-бота техподдержки в фоне...")
                await support_bot.delete_webhook(drop_pending_updates=True)
                support_polling_task = asyncio.create_task(support_dp.start_polling(support_bot))
        except Exception as e:
            print(f"⚠️ Ошибка при запуске бота техподдержки в lifespan: {e}")
    else:
        print("ℹ️ Фоновый запуск бота поддержки в main.py отключен (RUN_SUPPORT_IN_MAIN=false, управляется через systemd).")

    yield  # В этот момент FastAPI обрабатывает запросы

    print("🛑 Остановка Telegram-бота...")
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass

    if support_polling_task:
        print("🛑 Остановка Telegram-бота техподдержки...")
        support_polling_task.cancel()
        try:
            await support_polling_task
        except asyncio.CancelledError:
            pass


# Инициализация Rate Limiter (SlowAPI) по IP-адресу
limiter = Limiter(key_func=get_remote_address)

# Инициализация приложения FastAPI с lifespan
app = FastAPI(
    title="Юридический помощник API",
    description="API бэкенда для Telegram Mini App и бота «Юридический помощник»",
    lifespan=lifespan
)
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
    Возвращает актуальный статус подписки и юзернейм бота техподдержки.
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
                "expires_at": trial_until.strftime("%Y-%m-%d %H:%M"),
                "support_bot_username": SUPPORT_BOT_USERNAME
            }
            
        record = data[0]
        sub_until_str = record.get("subscription_until")
        trial_used = bool(record.get("trial_used", False))
        
        # Если поле subscription_until пустое
        if not sub_until_str:
            return {
                "active": False,
                "is_trial": False,
                "expires_at": None,
                "support_bot_username": SUPPORT_BOT_USERNAME
            }
            
        # Парсим дату (Supabase возвращает ISO строку)
        # Заменяем 'Z' на '+00:00' для корректной работы fromisoformat
        sub_until = datetime.fromisoformat(sub_until_str.replace("Z", "+00:00"))
        
        # Сравниваем дату окончания с текущим временем
        if sub_until > now_utc:
            return {
                "active": True,
                "is_trial": trial_used,
                "expires_at": sub_until.strftime("%Y-%m-%d %H:%M"),
                "support_bot_username": SUPPORT_BOT_USERNAME
            }
        else:
            return {
                "active": False,
                "is_trial": False,
                "expires_at": sub_until.strftime("%Y-%m-%d %H:%M"),
                "support_bot_username": SUPPORT_BOT_USERNAME
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/webhook/platega")
@limiter.limit("60/minute")
async def platega_webhook(
    request: Request,
    x_signature: str = Header(None, alias="X-Signature"),
    x_secret: str = Header(None, alias="X-Secret")
):
    """
    Вебхук для приема уведомлений об оплате от платежного шлюза Platega.io.
    """
    raw_body = await request.body()
    sig = x_signature or x_secret or request.headers.get("X-Signature") or request.headers.get("X-Secret")

    # 1. Валидация цифровой подписи
    if not sig or not verify_platega_signature(raw_body, sig):
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 2. Парсинг JSON тела запроса
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 3. Проверка успешного статуса платежа
    status = str(payload.get("status", "")).upper()
    if status not in ("CONFIRMED", "SUCCESS"):
        return {"status": "ignored", "reason": f"Status '{status}' is not a successful payment state"}

    # 4. Проверка идемпотентности
    transaction_id = str(
        payload.get("id") or 
        payload.get("transactionId") or 
        payload.get("order_id") or 
        ""
    ).strip()
    if transaction_id and transaction_id in PROCESSED_PAYMENTS:
        return {"status": "ok", "message": "already processed"}

    # 5. Извлечение telegram_id и days
    telegram_id = None
    days = 30

    # Вариант А: custom_data
    custom_data = payload.get("custom_data")
    if isinstance(custom_data, dict):
        telegram_id = custom_data.get("telegram_id")
        days = custom_data.get("days", days)
    elif isinstance(custom_data, str):
        try:
            parsed_cd = json.loads(custom_data)
            if isinstance(parsed_cd, dict):
                telegram_id = parsed_cd.get("telegram_id")
                days = parsed_cd.get("days", days)
        except Exception:
            pass

    # Вариант Б: payload (вложенный json string или словарь)
    if telegram_id is None and "payload" in payload:
        raw_p = payload.get("payload")
        if isinstance(raw_p, dict):
            telegram_id = raw_p.get("telegram_id")
            days = raw_p.get("days", days)
        elif isinstance(raw_p, str):
            try:
                parsed_p = json.loads(raw_p)
                if isinstance(parsed_p, dict):
                    telegram_id = parsed_p.get("telegram_id")
                    days = parsed_p.get("days", days)
            except Exception:
                pass

    # Вариант В: metadata.userId
    if telegram_id is None and "metadata" in payload:
        meta = payload.get("metadata")
        if isinstance(meta, dict) and "userId" in meta:
            telegram_id = meta.get("userId")

    # Вариант Г: корневой ключ telegram_id
    if telegram_id is None and "telegram_id" in payload:
        telegram_id = payload.get("telegram_id")

    # Вариант Д: разбор из order_id ("sub_{telegram_id}_{timestamp}")
    if telegram_id is None and "order_id" in payload:
        parts = str(payload.get("order_id", "")).split("_")
        if len(parts) >= 3 and parts[0] == "sub" and parts[1].isdigit():
            telegram_id = int(parts[1])

    if telegram_id is None:
        raise HTTPException(status_code=400, detail="telegram_id is required in payload")

    try:
        telegram_id = int(telegram_id)
        days = int(days)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid telegram_id or days format")

    now_utc = datetime.now(timezone.utc)
    added_timedelta = timedelta(days=days)

    # 6. Продление подписки в Supabase
    try:
        supabase = get_supabase()
        if not supabase:
            raise RuntimeError("Supabase client is not initialized")

        response = supabase.table("subscriptions").select("subscription_until").eq("telegram_id", telegram_id).execute()
        data = response.data

        if data:
            sub_until_str = data[0].get("subscription_until")
            if sub_until_str:
                current_sub_until = datetime.fromisoformat(sub_until_str.replace("Z", "+00:00"))
                if current_sub_until > now_utc:
                    new_sub_until = current_sub_until + added_timedelta
                else:
                    new_sub_until = now_utc + added_timedelta
            else:
                new_sub_until = now_utc + added_timedelta

            supabase.table("subscriptions").update({
                "subscription_until": new_sub_until.isoformat(),
                "trial_used": True
            }).eq("telegram_id", telegram_id).execute()
        else:
            new_sub_until = now_utc + added_timedelta
            supabase.table("subscriptions").insert({
                "telegram_id": telegram_id,
                "subscription_until": new_sub_until.isoformat(),
                "trial_used": True
            }).execute()

        # 7. Отправка уведомления пользователю в Telegram
        try:
            await bot.send_message(
                chat_id=telegram_id,
                text="🎉 Оплата успешно получена! Ваша подписка на «Юридический помощник» активна на 30 дней."
            )
        except Exception as notify_err:
            print(f"⚠️ Не удалось отправить уведомление пользователю {telegram_id}: {notify_err}")

        # Фиксируем транзакцию для предотвращения повторной обработки
        if transaction_id:
            PROCESSED_PAYMENTS.add(transaction_id)

        # 8. Ответ 200 OK
        return {"status": "ok"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/create-payment")
@limiter.limit("30/minute")
async def create_payment_endpoint(
    request: Request,
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data")
):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Отсутствует заголовок авторизации")
        
    user_data = verify_telegram_init_data(x_telegram_init_data)
    if not user_data or "id" not in user_data:
        raise HTTPException(status_code=401, detail="Недействительная сессия")

    telegram_id = user_data["id"]
    try:
        payment_url = await create_platega_payment(telegram_id=telegram_id, amount=299.0)
        print(f"[API] [OK] Ссылка на оплату для user {telegram_id} успешно создана: {payment_url}", flush=True)
        return {"payment_url": payment_url}
    except Exception as e:
        print(f"[API] [ERROR] Ошибка формирования счета Platega для user {telegram_id}: {e}", flush=True)
        logger.exception(f"Ошибка формирования счета Platega для user {telegram_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Ошибка платежного шлюза: {str(e)}")



@app.get("/")
@app.get("/index.html")
async def serve_index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))
