import os
import time
import json
import logging
import httpx
import hmac
import hashlib
import base64
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

PLATEGA_MERCHANT_ID = os.getenv("PLATEGA_MERCHANT_ID", "").strip()
PLATEGA_API_KEY = os.getenv("PLATEGA_API_KEY", "").strip()
PLATEGA_SECRET_KEY = (os.getenv("PLATEGA_SECRET_KEY") or PLATEGA_API_KEY).strip()
PLATEGA_API_URL = os.getenv("PLATEGA_API_URL", "https://app.platega.io").rstrip("/")


async def create_platega_payment(telegram_id: int, amount: float = 299.0, days: int = 30) -> str:
    """
    Создает транзакцию через Platega API и возвращает URL для оплаты.
    """
    merchant_id = (os.getenv("PLATEGA_MERCHANT_ID") or PLATEGA_MERCHANT_ID).strip()
    api_key = (os.getenv("PLATEGA_API_KEY") or PLATEGA_API_KEY).strip()
    api_url = (os.getenv("PLATEGA_API_URL") or PLATEGA_API_URL).rstrip("/")
    if "api.platega.io" in api_url:
        api_url = api_url.replace("api.platega.io", "app.platega.io")

    if not merchant_id or not api_key:
        err_msg = "КРИТИЧЕСКАЯ ОШИБКА: PLATEGA_MERCHANT_ID или PLATEGA_API_KEY не заданы в .env файле!"
        print(f"[Platega] [ERROR] {err_msg}", flush=True)
        logger.error(err_msg)
        raise ValueError("Параметры Platega не настроены в .env")

    order_id = f"sub_{telegram_id}_{int(time.time())}"
    
    headers = {
        "X-MerchantId": merchant_id,
        "X-Secret": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # Универсальный payload: не указываем жестко paymentMethod, чтобы Platega дала пользователю все доступные способы оплаты (карты, СБП и др.)
    # Добавляем amount целым числом и paymentDetails для полной совместимости с v2 API Platega
    payload = {
        "orderId": order_id,
        "amount": int(amount),  # передаем целым числом 299
        "currency": "RUB",
        "paymentDetails": {
            "amount": int(amount),
            "currency": "RUB"
        },
        "description": f"Подписка на сервис «Юридический помощник» ({days} дн.)",
        "payload": str(telegram_id),
        "returnUrl": "https://t.me/JuristCalc_bot",
        "failedUrl": "https://t.me/JuristCalc_bot"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Проверяем основные эндпоинты Platega (v2/transaction/process является основным для универсальной оплаты)
        target_endpoints = [
            f"{api_url}/v2/transaction/process",
            f"{api_url}/transaction/process",
            f"{api_url}/v1/transactions",
            f"{api_url}/v1/payment/create"
        ]

        last_error = ""
        for url in target_endpoints:
            try:
                print(f"[Platega] Sending request to Platega: URL={url}, OrderID={order_id}", flush=True)
                logger.info(f"Отправка запроса в Platega: URL={url}, OrderID={order_id}")
                response = await client.post(url, json=payload, headers=headers)
                print(f"[Platega] HTTP Status: {response.status_code}, Response Body: {response.text}", flush=True)
                logger.info(f"Platega HTTP Status: {response.status_code}, Response Body: {response.text}")

                if response.status_code in (200, 201):
                    data = response.json()
                    payment_url = (
                        data.get("url") or 
                        data.get("redirect") or 
                        data.get("payment_url") or 
                        data.get("checkout_url") or
                        (data.get("data") and isinstance(data["data"], dict) and data["data"].get("url")) or
                        (data.get("data") and isinstance(data["data"], dict) and data["data"].get("redirect"))
                    )
                    if payment_url:
                        print(f"[Platega] Successfully received payment URL: {payment_url}", flush=True)
                        logger.info(f"Успешно получена ссылка на оплату: {payment_url}")
                        return payment_url
                    else:
                        print(f"[Platega] Status 200/201, but no payment URL key in response: {data}", flush=True)
                        logger.warning(f"Статус 200/201, но не найден ключ ссылки в ответе: {data}")
                else:
                    last_error = f"HTTP {response.status_code}: {response.text}"
            except Exception as e:
                print(f"[Platega] Exception during request to {url}: {e}", flush=True)
                logger.error(f"Исключение при запросе к {url}: {e}")
                last_error = str(e)

        raise RuntimeError(f"Platega API отклонил запрос: {last_error}")


def verify_platega_signature(
    payload_bytes: bytes | str, 
    signature_header: str, 
    payload_field: str | None = None
) -> bool:
    """
    Проверка подписи входящего Webhook от Platega.
    1. Если signature_header совпадает напрямую со значением PLATEGA_SECRET_KEY или PLATEGA_API_KEY — сразу True.
    2. При HMAC проверке убирает префиксы sha256= и пробелы, проверяет совпадение в HEX (любой регистр) и Base64.
    3. Проверяет хэш как от сырого тела (raw bytes), так и от строки payload.
    """
    if not signature_header:
        return False

    sig = str(signature_header.decode("utf-8") if isinstance(signature_header, bytes) else signature_header).strip()
    if not sig:
        return False

    secret_str = (
        os.getenv("PLATEGA_SECRET_KEY") or 
        os.getenv("PLATEGA_API_KEY") or 
        PLATEGA_SECRET_KEY or 
        PLATEGA_API_KEY or 
        ""
    ).strip()
    api_key_str = (
        os.getenv("PLATEGA_API_KEY") or 
        PLATEGA_API_KEY or 
        ""
    ).strip()

    candidates = [s for s in dict.fromkeys([secret_str, api_key_str]) if s]
    if not candidates:
        return False

    # 1. Прямое совпадение со значением секрета или API-ключа
    for s in candidates:
        if hmac.compare_digest(sig, s):
            return True

    # 2. Очистка от префикса sha256= и пробелов
    if sig.lower().startswith("sha256="):
        sig = sig[7:].strip()

    # 3. Подготовка вариантов данных для проверки хэша (сырое тело + строковый параметр payload)
    raw_bytes = payload_bytes.encode("utf-8") if isinstance(payload_bytes, str) else payload_bytes
    byte_candidates: list[bytes] = [raw_bytes]

    if payload_field:
        byte_candidates.append(str(payload_field).encode("utf-8"))

    try:
        raw_text = raw_bytes.decode("utf-8")
        parsed_json = json.loads(raw_text)
        if isinstance(parsed_json, dict):
            extracted = parsed_json.get("payload") or (parsed_json.get("data") if isinstance(parsed_json.get("data"), dict) else {}).get("payload")
            if extracted is not None:
                byte_candidates.append(str(extracted).encode("utf-8"))
    except Exception:
        pass

    # 4. Проверка HMAC для каждого секрета и каждого варианта тела
    for s in candidates:
        secret_bytes = s.encode("utf-8")
        for b_data in byte_candidates:
            expected_hex = hmac.new(secret_bytes, b_data, hashlib.sha256).hexdigest()
            expected_b64 = base64.b64encode(hmac.new(secret_bytes, b_data, hashlib.sha256).digest()).decode("utf-8")

            if (
                hmac.compare_digest(sig.lower(), expected_hex.lower()) or 
                hmac.compare_digest(sig, expected_b64)
            ):
                return True

    return False
