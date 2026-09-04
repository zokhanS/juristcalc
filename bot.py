import os
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# 1. Загружаем переменные окружения в самом верху файла с переопределением
load_dotenv(override=True)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice
from supabase import create_client, Client

BOT_TOKEN = os.getenv("BOT_TOKEN")
PAYMASTER_TOKEN = os.getenv("PAYMASTER_PROVIDER_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN в файле .env")

if not PAYMASTER_TOKEN:
    raise ValueError("Не задан PAYMASTER_PROVIDER_TOKEN в файле .env")


def get_supabase() -> Client:
    """
    Динамически создает и возвращает клиент Supabase, 
    используя актуальные переменные окружения из .env при каждом вызове.
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Чтение URL из переменных окружения
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://217-199-253-99.sslip.io")
PAYMENT_URL = os.getenv("PAYMENT_URL", "https://t.me/JuristCalc_bot?start=buy")


async def send_subscription_invoice(chat_id: int):
    """
    Отправляет Telegram-инвойс для оплаты подписки через Paymaster.
    """
    await bot.send_invoice(
        chat_id=chat_id,
        title="Подписка на Судебный Помощник PRO",
        description="Полный доступ ко всем калькуляторам и функциям на 30 дней",
        payload="sub_30_days",
        provider_token=PAYMASTER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Подписка на 30 дней", amount=29900)],
        start_parameter="sub_30_days"
    )


@dp.message(CommandStart())
async def command_start_handler(message: types.Message, command: CommandObject) -> None:
    """
    Обработчик команды /start
    Если передан диплинк 'buy' (/start buy), автоматический запуск инвойса на оплату.
    Для нового пользователя начисляет 5 дней бесплатного пробного периода в Supabase.
    """
    if command.args == "buy":
        await send_subscription_invoice(message.chat.id)
        return

    # Начисление 5-дневного триала новому пользователю в Supabase
    telegram_id = message.from_user.id
    now_utc = datetime.now(timezone.utc)
    trial_until = now_utc + timedelta(days=5)

    supabase = get_supabase()
    if not supabase:
        logger.warning(
            "Supabase клиент не инициализирован (проверьте SUPABASE_URL и SUPABASE_KEY в .env). "
            f"Триал для telegram_id={telegram_id} не может быть проверен/начислен."
        )
        print(f"[WARNING] Supabase клиент не инициализирован для telegram_id={telegram_id}")
    else:
        try:
            logger.info(f"Проверка существующей подписки в Supabase для telegram_id={telegram_id}...")
            print(f"[INFO] Проверка подписки в Supabase для telegram_id={telegram_id}...")
            response = supabase.table("subscriptions").select("subscription_until, trial_used").eq("telegram_id", telegram_id).execute()
            data = response.data
            logger.info(f"Данные из Supabase для telegram_id={telegram_id}: {data}")
            print(f"[INFO] Ответ Supabase для telegram_id={telegram_id}: {data}")

            if not data:
                # Новая регистрация: записи нет, создаем 5-дневный бесплатный триал
                insert_payload = {
                    "telegram_id": telegram_id,
                    "subscription_until": trial_until.isoformat(),
                    "trial_used": True
                }
                logger.info(f"Вставка новой записи триала в Supabase: {insert_payload}")
                print(f"[INFO] Выполняется вставка триала в Supabase: {insert_payload}")
                insert_result = supabase.table("subscriptions").insert(insert_payload).execute()
                logger.info(f"Успешно создана запись триала в Supabase: {insert_result.data}")
                print(f"[SUCCESS] Успешно создана запись триала в Supabase: {insert_result.data}")
            else:
                logger.info(
                    f"Пользователь telegram_id={telegram_id} уже присутствует в базе подписок. "
                    f"Текущие данные: {data[0]}. Повторный триал не начисляется."
                )
                print(f"[INFO] Пользователь telegram_id={telegram_id} уже есть в базе, повторный триал не требуется.")
        except Exception as e:
            logger.exception(f"Ошибка при работе с таблицей subscriptions в Supabase для telegram_id={telegram_id}: {e}")
            print(f"[ERROR] Ошибка Supabase при обработке /start для telegram_id={telegram_id}: {e}")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚖️ Открыть Калькулятор", 
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Оплатить подписку", 
                    callback_data="buy_subscription"
                )
            ]
        ]
    )

    welcome_text = (
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        "Добро пожаловать в Судебный & Исполнительный Помощник PRO.\n\n"
        "🎁 Вам начислен бесплатный пробный доступ на 5 дней ко всем функциям калькулятора!\n\n"
        "Для работы с калькулятором нажми кнопку ниже. Если у тебя еще нет подписки, "
        "ты можешь оформить её, нажав на кнопку «💳 Оплатить подписку» или отправив команду /buy."
    )

    await message.answer(welcome_text, reply_markup=keyboard)


@dp.message(Command("buy"))
async def command_buy_handler(message: types.Message) -> None:
    """
    Обработчик команды /buy для вызова инвойса оплаты.
    """
    await send_subscription_invoice(message.chat.id)


@dp.callback_query(F.data == "buy_subscription")
async def process_buy_callback(callback_query: types.CallbackQuery) -> None:
    """
    Обработчик нажатия на инлайн-кнопку «💳 Оплатить подписку».
    """
    await callback_query.answer()
    await send_subscription_invoice(callback_query.message.chat.id)


@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: types.PreCheckoutQuery) -> None:
    """
    Подтверждение готовности принять платеж от пользователя.
    """
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message) -> None:
    """
    Обработчик успешной оплаты подписки.
    Обновляет или создает запись о подписке в Supabase на +30 дней.
    Сохраняет флаг trial_used: True.
    """
    telegram_id = message.from_user.id
    now_utc = datetime.now(timezone.utc)
    added_timedelta = timedelta(days=30)
    
    supabase = get_supabase()
    if not supabase:
        logging.error("Supabase клиент не инициализирован (отсутствуют SUPABASE_URL или SUPABASE_KEY).")
        await message.answer("⚠️ Оплата прошла, но произошла ошибка при активации подписки. Обратитесь в поддержку.")
        return

    try:
        response = supabase.table("subscriptions").select("subscription_until, trial_used").eq("telegram_id", telegram_id).execute()
        data = response.data

        if data and data[0].get("subscription_until"):
            sub_until_str = data[0].get("subscription_until")
            current_sub_until = datetime.fromisoformat(sub_until_str.replace("Z", "+00:00"))
            if current_sub_until > now_utc:
                new_sub_until = current_sub_until + added_timedelta
            else:
                new_sub_until = now_utc + added_timedelta
        else:
            new_sub_until = now_utc + added_timedelta

        sub_until_iso = new_sub_until.isoformat()

        if data:
            supabase.table("subscriptions").update({
                "subscription_until": sub_until_iso,
                "trial_used": True
            }).eq("telegram_id", telegram_id).execute()
        else:
            supabase.table("subscriptions").insert({
                "telegram_id": telegram_id,
                "subscription_until": sub_until_iso,
                "trial_used": True
            }).execute()

        await message.answer("🎉 Оплата прошла успешно! Все вкладки калькулятора разблокированы.")
    except Exception as e:
        logging.exception("Ошибка при активации подписки в Supabase: %s", e)
        await message.answer("⚠️ Оплата прошла, но произошла ошибка при активации подписки. Обратитесь в поддержку.")


async def main() -> None:
    """
    Точка входа: запуск поллинга
    """
    print("Бот запущен...")
    # Удаляем вебхуки и пропускаем старые обновления, если они были
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен.")
