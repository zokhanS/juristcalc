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
from fastapi.middleware.gzip import GZipMiddleware
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
allowed_origins = [
    "https://t.me",
    "https://web.telegram.org",
    os.getenv("WEBAPP_URL", "").rstrip("/")
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin for origin in allowed_origins if origin],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Сжатие ответов (GZip) для ускорения передачи контента на мобильные устройства
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.middleware("http")
async def add_cache_control_headers(request: Request, call_next):
    """
    Устанавливает заголовки Cache-Control для статических файлов и страниц:
    - Для корня / и index.html: public, max-age=300 (5 минут) для быстрой доставки обновлений
    - Для остальных статических файлов (.js, .css, .html): public, max-age=3600 (1 час)
    """
    response = await call_next(request)
    path = request.url.path.lower()
    if response.status_code < 400:
        if path == "/" or path == "/index.html" or path.endswith("/index.html"):
            response.headers["Cache-Control"] = "public, max-age=300"
        elif path.endswith((".js", ".css", ".html")):
            response.headers["Cache-Control"] = "public, max-age=3600"
    return response


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
        if not supabase:
            raise RuntimeError("Supabase client is not available")
        db_res = supabase.table("subscriptions").select("subscription_until, trial_used").eq("telegram_id", telegram_id).execute()
        data = db_res.data
        now_utc = datetime.now(timezone.utc)
        
        # Если записи о пользователе нет в БД (первый вход)
        if not data:
            trial_until = now_utc + timedelta(days=5)
            supabase.table("subscriptions").insert({
                "telegram_id": telegram_id,
                "subscription_until": trial_until.isoformat(),
                "trial_used": False
            }).execute()
            return {
                "active": True,
                "expires_at": trial_until.strftime("%Y-%m-%d %H:%M"),
                "trial_used": False,
                "status_text": "Пробный период",
                "plan_type": "trial",
                "support_bot_username": SUPPORT_BOT_USERNAME
            }
            
        record = data[0]
        sub_until_str = record.get("subscription_until")
        trial_used = bool(record.get("trial_used", False))
        
        # Если поле subscription_until пустое
        if not sub_until_str:
            return {
                "active": False,
                "expires_at": None,
                "trial_used": trial_used,
                "status_text": "Истекла",
                "plan_type": "none",
                "support_bot_username": SUPPORT_BOT_USERNAME
            }
            
        # Парсим дату (Supabase возвращает ISO строку)
        # Заменяем 'Z' на '+00:00' и пробелы на 'T' для корректной работы fromisoformat
        sub_str = str(sub_until_str).replace("Z", "+00:00").replace(" ", "T")
        sub_until = datetime.fromisoformat(sub_str)
        if sub_until.tzinfo is None:
            sub_until = sub_until.replace(tzinfo=timezone.utc)
        
        is_active = sub_until > now_utc
        return {
            "active": is_active,
            "expires_at": sub_until.strftime("%Y-%m-%d %H:%M"),
            "trial_used": trial_used,
            "status_text": "Активна" if (is_active and trial_used) else ("Пробный период" if is_active else "Истекла"),
            "plan_type": "paid" if (is_active and trial_used) else ("trial" if is_active else "none"),
            "support_bot_username": SUPPORT_BOT_USERNAME
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Ошибка проверки подписки: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


@app.post("/api/webhook/platega")
@limiter.limit("60/minute")
async def platega_webhook(request: Request):
    try:
        raw_body = await request.body()
        signature = (
            request.headers.get("X-Signature") or 
            request.headers.get("Signature") or 
            ""
        )
        secret_header = (
            request.headers.get("X-Secret") or 
            ""
        )

        try:
            data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            data = {}
        logger.info(f"[Platega Webhook] Получены данные: {data}, Headers: {dict(request.headers)}")

        # Если подпись передана внутри JSON
        if not signature and isinstance(data, dict) and "signature" in data:
            signature = str(data["signature"])

        # Валидация подписи (если не прошла — немедленный отказ 403)
        if not verify_platega_signature(raw_body, signature_header=signature, secret_header=secret_header):
            logger.warning("[Platega Webhook] Отклонен запрос с неверной подписью")
            raise HTTPException(status_code=403, detail="Invalid signature")

        data_nested = data.get("data") if isinstance(data.get("data"), dict) else {}
        custom_data_nested = data.get("custom_data") if isinstance(data.get("custom_data"), dict) else {}
        trans_nested = data.get("transaction") if isinstance(data.get("transaction"), dict) else {}
        payment_nested = data.get("payment") if isinstance(data.get("payment"), dict) else {}

        # 1. Каскадный поиск статуса транзакции
        raw_status = (
            data.get("status") or 
            data_nested.get("status") or 
            trans_nested.get("status") or 
            payment_nested.get("status") or 
            ""
        )
        status = str(raw_status).strip().upper()
        if status not in ("CONFIRMED", "SUCCESS", "PAID", "COMPLETED"):
            logger.info(f"[Platega Webhook] Пропуск статуса: {status}")
            return {"status": "ignored", "reason": f"Status {status} not actionable"}

        # Идентификатор транзакции
        transaction_id = str(
            data.get("id") or 
            data.get("transactionId") or 
            data_nested.get("id") or 
            data_nested.get("transactionId") or 
            trans_nested.get("id") or 
            payment_nested.get("id") or 
            ""
        ).strip()

        # Извлечение telegram_id со всеми возможными fallback
        telegram_id = None
        raw_payload = data.get("payload") or data_nested.get("payload")
        if raw_payload and str(raw_payload).isdigit():
            telegram_id = int(raw_payload)

        if not telegram_id:
            raw_user_id = data.get("telegram_id") or custom_data_nested.get("telegram_id") or data_nested.get("telegram_id")
            if raw_user_id and str(raw_user_id).isdigit():
                telegram_id = int(raw_user_id)

        if not telegram_id:
            order_id = str(data.get("orderId") or data.get("order_id") or data_nested.get("orderId") or data_nested.get("order_id") or "")
            if order_id.startswith("sub_"):
                parts = order_id.split("_")
                if len(parts) >= 2 and parts[1].isdigit():
                    telegram_id = int(parts[1])

        if not telegram_id:
            logger.error(f"[Platega Webhook] Не удалось извлечь telegram_id из вебхука: {data}")
            return {"status": "error", "message": "telegram_id not found"}

        supabase = get_supabase()
        now_utc = datetime.now(timezone.utc)

        # Защита от повторных начислений (Идемпотентность)
        if transaction_id:
            if transaction_id in PROCESSED_PAYMENTS:
                logger.info(f"[Platega Webhook] Транзакция {transaction_id} уже обработана (in-memory).")
                return {"status": "ok", "message": "Already processed"}

            if supabase:
                try:
                    pay_res = supabase.table("payments").select("id").eq("payment_id", transaction_id).execute()
                    if pay_res.data:
                        PROCESSED_PAYMENTS.add(transaction_id)
                        logger.info(f"[Platega Webhook] Транзакция {transaction_id} уже обработана (Supabase).")
                        return {"status": "ok", "message": "Already processed"}
                except Exception as pay_err:
                    logger.debug(f"[Platega Webhook] Проверка таблицы payments: {pay_err}")

        days = 30
        logger.info(f"[Platega Webhook] Начисление подписки на {days} дней для пользователя {telegram_id}")

        # Проверяем текущую подписку
        res = supabase.table("subscriptions").select("subscription_until").eq("telegram_id", telegram_id).execute()
        current_data = res.data

        # 2. Надежный парсинг даты подписки из Supabase
        if current_data and current_data[0].get("subscription_until"):
            sub_str = str(current_data[0]["subscription_until"]).replace("Z", "+00:00").replace(" ", "T")
            try:
                sub_until_dt = datetime.fromisoformat(sub_str)
                if sub_until_dt.tzinfo is None:
                    sub_until_dt = sub_until_dt.replace(tzinfo=timezone.utc)
                base_dt = max(sub_until_dt, now_utc)
            except Exception as e:
                logger.warning(f"Ошибка парсинга даты подписки '{sub_str}': {e}")
                base_dt = now_utc
        else:
            base_dt = now_utc

        new_sub_until = (base_dt + timedelta(days=days)).isoformat()

        # Обновляем или вставляем запись
        supabase.table("subscriptions").upsert({
            "telegram_id": telegram_id,
            "subscription_until": new_sub_until,
            "trial_used": True
        }).execute()

        # Фиксируем обработанный платеж для предотвращения дублей
        if transaction_id:
            PROCESSED_PAYMENTS.add(transaction_id)
            if supabase:
                try:
                    supabase.table("payments").insert({
                        "payment_id": transaction_id,
                        "telegram_id": telegram_id,
                        "amount": 299,
                        "created_at": now_utc.isoformat()
                    }).execute()
                except Exception as insert_err:
                    logger.debug(f"[Platega Webhook] Запись в таблицу payments пропущена: {insert_err}")

        # Отправляем сообщение в Telegram
        try:
            from bot import bot
            await bot.send_message(
                chat_id=telegram_id,
                text=(
                    "🎉 <b>Оплата успешно получена!</b>\n\n"
                    "Ваша подписка на сервис «Юридический помощник» продлена на <b>30 дней</b>.\n"
                    "Все калькуляторы и функции разблокированы. Приятной работы!"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление в TG пользователю {telegram_id}: {e}")

        return {"status": "ok"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Критическая ошибка обработки вебхука Platega: {e}")
        return {"status": "error", "detail": str(e)}


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
    except HTTPException:
        raise
    except Exception as e:
        print(f"[API] [ERROR] Ошибка формирования счета Platega для user {telegram_id}: {e}", flush=True)
        logger.exception(f"Ошибка формирования счета Platega для user {telegram_id}: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")



@app.get("/")
@app.get("/index.html")
async def serve_index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))
