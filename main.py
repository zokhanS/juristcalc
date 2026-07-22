import os
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from supabase import create_client, Client

# Загружаем переменные окружения из .env файла
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TOME_SECRET_KEY = os.getenv("TOME_SECRET_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, TOME_SECRET_KEY]):
    raise ValueError("Не все обязательные переменные окружения заданы. Проверьте ваш .env файл.")

# Инициализация Supabase клиента (используем service_role ключ)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Инициализация приложения FastAPI
app = FastAPI(title="Telegram WebApp Backend API")

# Настройка CORS для этапа разработки
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем запросы с любых доменов
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/check-subscription/{telegram_id}")
async def check_subscription(telegram_id: int):
    """
    Проверяет статус подписки пользователя по его telegram_id.
    """
    try:
        response = supabase.table("subscriptions").select("subscription_until").eq("telegram_id", telegram_id).execute()
        data = response.data
        
        # Если пользователь не найден в БД
        if not data:
            return {"active": False, "expires_at": None}
            
        sub_until_str = data[0].get("subscription_until")
        
        # Если поле subscription_until пустое
        if not sub_until_str:
            return {"active": False, "expires_at": None}
            
        # Парсим дату (Supabase возвращает ISO строку)
        # Заменяем 'Z' на '+00:00' для корректной работы fromisoformat
        sub_until = datetime.fromisoformat(sub_until_str.replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        
        # Сравниваем дату окончания с текущим временем
        is_active = sub_until > now_utc
        
        return {
            "active": is_active,
            "expires_at": sub_until.strftime("%Y-%m-%d %H:%M")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/webhook/tome")
async def tome_webhook(request: Request, x_tome_signature: str = Header(None)):
    """
    Вебхук для получения оповещений об успешной оплате от Tome.ru.
    """
    raw_body = await request.body()
    
    # 1. Безопасная проверка сигнатуры / хэша
    # Предполагается, что Tome.ru отправляет HMAC-SHA256 подпись тела запроса 
    # в заголовке X-Tome-Signature. (Вам нужно будет свериться с их документацией
    # и при необходимости адаптировать этот блок).
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
        telegram_id = payload.get("telegram_id")
        days = payload.get("days", 30)  # Период подписки в днях
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
        
    if not telegram_id:
         raise HTTPException(status_code=400, detail="telegram_id is required in payload")

    now_utc = datetime.now(timezone.utc)
    added_timedelta = timedelta(days=days)

    try:
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
                "subscription_until": new_sub_until.isoformat()
            }).eq("telegram_id", telegram_id).execute()
            
        else:
            # Пользователя нет, создаем новую запись
            new_sub_until = now_utc + added_timedelta
            supabase.table("subscriptions").insert({
                "telegram_id": telegram_id,
                "subscription_until": new_sub_until.isoformat()
            }).execute()
            
        # Возвращаем статус 200 OK как ожидает большинство платежных систем
        return {"status": "ok"}
        
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))
