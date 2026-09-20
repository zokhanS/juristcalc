import os
import time
import logging
import httpx
import hmac
import hashlib
import base64
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

PLATEGA_MERCHANT_ID = os.getenv("PLATEGA_MERCHANT_ID", "")
PLATEGA_API_KEY = os.getenv("PLATEGA_API_KEY", "")
PLATEGA_SECRET_KEY = os.getenv("PLATEGA_SECRET_KEY") or PLATEGA_API_KEY
PLATEGA_API_URL = os.getenv("PLATEGA_API_URL", "https://api.platega.io").rstrip("/")


async def create_platega_payment(telegram_id: int, amount: float = 299.0, days: int = 30) -> str:
    """
    Создает транзакцию через Platega API и возвращает URL для оплаты.
    """
    merchant_id = os.getenv("PLATEGA_MERCHANT_ID", PLATEGA_MERCHANT_ID)
    api_key = os.getenv("PLATEGA_API_KEY", PLATEGA_API_KEY)
    api_url = os.getenv("PLATEGA_API_URL", PLATEGA_API_URL).rstrip("/")

    if not merchant_id or not api_key:
        raise ValueError("PLATEGA_MERCHANT_ID или PLATEGA_API_KEY не заданы в .env")

    order_id = f"sub_{telegram_id}_{int(time.time())}"
    
    headers = {
        "X-MerchantId": merchant_id,
        "X-Secret": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        "paymentMethod": "sbp",  # или оставить универсальным/по умолчанию
        "orderId": order_id,
        "amount": float(amount),
        "currency": "RUB",
        "description": f"Подписка на «Юридический помощник» ({days} дней)",
        "payload": str(telegram_id),
        "returnUrl": "https://t.me/JuristCalc_bot",
        "failedUrl": "https://t.me/JuristCalc_bot"
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        # Пробуем официальные эндпоинты транзакций Platega
        target_urls = [
            f"{api_url}/v1/transactions",
            f"{api_url}/transaction/process",
            f"{api_url}/v1/payment/create"
        ]
        
        last_error = None
        for url in target_urls:
            try:
                response = await client.post(url, json=payload, headers=headers)
                logger.info(f"Platega request to {url} returned status {response.status_code}")
                if response.status_code in (200, 201):
                    data = response.json()
                    # Извлекаем ссылку на оплату
                    payment_url = (
                        data.get("redirect") or 
                        data.get("url") or 
                        data.get("payment_url") or 
                        data.get("checkout_url") or
                        (data.get("data") and isinstance(data["data"], dict) and data["data"].get("url")) or
                        (data.get("data") and isinstance(data["data"], dict) and data["data"].get("redirect"))
                    )
                    if payment_url:
                        return payment_url
                else:
                    logger.warning(f"Failed response from {url}: {response.text}")
                    last_error = f"Status {response.status_code}: {response.text}"
            except Exception as e:
                logger.error(f"Error connecting to {url}: {e}")
                last_error = str(e)

        raise RuntimeError(f"Не удалось получить ссылку от Platega: {last_error}")


def verify_platega_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """
    Проверка подписи входящего Webhook.
    """
    if not signature_header:
        return False
    secret_str = os.getenv("PLATEGA_SECRET_KEY") or os.getenv("PLATEGA_API_KEY") or PLATEGA_SECRET_KEY or PLATEGA_API_KEY or ""
    if not secret_str:
        return False
    secret = secret_str.encode("utf-8")
    expected_hex = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
    expected_b64 = base64.b64encode(hmac.new(secret, payload_bytes, hashlib.sha256).digest()).decode("utf-8")

    # Проверяем как прямое совпадение токена, так и HMAC хэш (hex и base64)
    return (
        hmac.compare_digest(signature_header.lower(), expected_hex.lower()) or 
        hmac.compare_digest(signature_header, expected_b64) or
        hmac.compare_digest(signature_header, secret_str)
    )
