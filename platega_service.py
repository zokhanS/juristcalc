import os
import time
import json
import hmac
import base64
import hashlib
import logging
from typing import Optional
import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

PLATEGA_MERCHANT_ID = os.getenv("PLATEGA_MERCHANT_ID", "").strip()
PLATEGA_API_KEY = os.getenv("PLATEGA_API_KEY", "").strip()
PLATEGA_SECRET_KEY = os.getenv("PLATEGA_SECRET_KEY", "").strip()
PLATEGA_API_URL = os.getenv("PLATEGA_API_URL", "https://api.platega.io").strip().rstrip("/")


def get_platega_config():
    """
    Возвращает актуальную конфигурацию Platega из переменных окружения.
    """
    return {
        "merchant_id": os.getenv("PLATEGA_MERCHANT_ID", PLATEGA_MERCHANT_ID).strip(),
        "api_key": os.getenv("PLATEGA_API_KEY", PLATEGA_API_KEY).strip(),
        "secret_key": os.getenv("PLATEGA_SECRET_KEY", PLATEGA_SECRET_KEY).strip(),
        "api_url": os.getenv("PLATEGA_API_URL", PLATEGA_API_URL).strip().rstrip("/"),
    }


async def create_platega_payment(telegram_id: int, amount: float = 299.0, days: int = 30) -> str:
    """
    Формирует и отправляет запрос на создание счета через Platega API.
    Возвращает URL страницы оплаты (checkout_url / redirect).
    
    :param telegram_id: Telegram ID плательщика
    :param amount: Сумма платежа в рублях (по умолчанию 299.0)
    :param days: Количество дней подписки (по умолчанию 30)
    :return: URL страницы оплаты
    """
    config = get_platega_config()
    merchant_id = config["merchant_id"]
    api_key = config["api_key"]
    secret_key = config["secret_key"]
    api_url = config["api_url"]

    if not merchant_id or not api_key:
        error_msg = "Не заданы PLATEGA_MERCHANT_ID или PLATEGA_API_KEY в конфигурации."
        logger.error(error_msg)
        raise ValueError(error_msg)

    order_id = f"sub_{telegram_id}_{int(time.time())}"
    description = f"Подписка на сервис «Юридический помощник» ({days} дней)"
    return_url = "https://t.me/JuristCalc_bot"

    payload_data = {"telegram_id": int(telegram_id), "days": int(days)}

    # Формируем универсальный payload, соответствующий спецификации Platega
    request_payload = {
        "merchant_id": merchant_id,
        "amount": float(amount),
        "currency": "RUB",
        "order_id": order_id,
        "description": description,
        "custom_data": payload_data,
        "payload": json.dumps(payload_data, ensure_ascii=False),
        "paymentDetails": {
            "amount": float(amount),
            "currency": "RUB"
        },
        "return": return_url,
        "return_url": return_url,
        "failedUrl": return_url,
        "failed_url": return_url,
        "metadata": {
            "userId": str(telegram_id)
        }
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-MerchantId": merchant_id,
        "X-Secret": secret_key,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # Возможные эндпоинты создания платежа по документации Platega
    endpoints = [
        f"{api_url}/v1/payment/create",
        f"{api_url}/transaction/process",
        f"{api_url}/v2/transaction/process"
    ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        last_exception = None
        for endpoint in endpoints:
            try:
                logger.info(f"Отправка запроса на создание платежа Platega: {endpoint}")
                response = await client.post(endpoint, json=request_payload, headers=headers)
                
                if response.status_code in (200, 201):
                    data = response.json()
                    logger.info(f"Успешный ответ от Platega API: {data}")
                    
                    payment_url = (
                        data.get("redirect") or
                        data.get("payment_url") or
                        data.get("checkout_url") or
                        data.get("url") or
                        (data.get("data", {}).get("redirect") if isinstance(data.get("data"), dict) else None) or
                        (data.get("data", {}).get("payment_url") if isinstance(data.get("data"), dict) else None)
                    )

                    if payment_url:
                        return str(payment_url)
                    else:
                        raise ValueError(f"В ответе Platega отсутствует payment_url: {data}")
                
                elif response.status_code == 404:
                    # Пробуем следующий альтернативный эндпоинт
                    logger.warning(f"Эндпоинт {endpoint} вернул 404, пробуем альтернативный маршрут.")
                    continue
                else:
                    error_text = response.text
                    logger.error(f"Platega API вернул ошибку {response.status_code}: {error_text}")
                    raise RuntimeError(f"Platega API error {response.status_code}: {error_text}")

            except Exception as e:
                last_exception = e
                logger.exception(f"Исключение при обращении к {endpoint}: {e}")
                # Если это не 404 ошибка URL, не пытаемся бесконечно
                if not isinstance(e, httpx.HTTPStatusError) or e.response.status_code != 404:
                    break

        raise RuntimeError(f"Не удалось создать платеж в Platega: {last_exception}")


def verify_platega_signature(payload_bytes: bytes, signature_header: Optional[str]) -> bool:
    """
    Проверяет цифровую подпись вебхука от Platega.io:
    - HMAC-SHA256 от сырого тела запроса с использованием PLATEGA_SECRET_KEY
      (в формате hex или base64).
    - Прямое совпадение секретного ключа (заголовок X-Secret).
    Использует безопасное сравнение hmac.compare_digest.
    
    :param payload_bytes: Сырые байты тела запроса
    :param signature_header: Значение заголовка подписи (X-Signature или X-Secret)
    :return: True, если подпись подлинна, иначе False
    """
    if not signature_header or not payload_bytes:
        return False

    config = get_platega_config()
    secret_key = config["secret_key"]
    if not secret_key:
        logger.error("PLATEGA_SECRET_KEY не задан для проверки подписи.")
        return False

    cleaned_sig = signature_header.strip()

    # 1. Проверка прямого совпадения секрета (если Platega передает X-Secret)
    if hmac.compare_digest(cleaned_sig, secret_key):
        return True

    key_bytes = secret_key.encode("utf-8")

    # 2. Вычисление HMAC-SHA256 в hex
    calculated_hex = hmac.new(key_bytes, payload_bytes, hashlib.sha256).hexdigest()
    if hmac.compare_digest(calculated_hex.lower(), cleaned_sig.lower()):
        return True

    # 3. Вычисление HMAC-SHA256 в Base64
    calculated_b64 = base64.b64encode(hmac.new(key_bytes, payload_bytes, hashlib.sha256).digest()).decode("utf-8")
    if hmac.compare_digest(calculated_b64, cleaned_sig):
        return True

    logger.warning("Проверка подписи Platega не прошла: подпись не совпадает.")
    return False
