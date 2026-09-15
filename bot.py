import os
import html
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
from supadns import create_smart_client

BOT_TOKEN = os.getenv("BOT_TOKEN")
PAYMASTER_TOKEN = os.getenv("PAYMASTER_PROVIDER_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN в файле .env")

if not PAYMASTER_TOKEN:
    raise ValueError("Не задан PAYMASTER_PROVIDER_TOKEN в файле .env")


def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_smart_client(url, key)


# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Чтение переменных окружения
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://217-199-253-99.sslip.io")
PAYMENT_URL = os.getenv("PAYMENT_URL", "https://t.me/JuristCalc_bot?start=buy")
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME", "").replace("@", "").strip()
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "").strip()


def get_support_url() -> str:
    """
    Возвращает ссылку на бота поддержки или запасной контакт администратора.
    """
    if SUPPORT_BOT_USERNAME:
        return f"https://t.me/{SUPPORT_BOT_USERNAME}"
    if ADMIN_TELEGRAM_ID.isdigit():
        return f"tg://user?id={ADMIN_TELEGRAM_ID}"
    return "https://t.me/"


def get_start_keyboard() -> InlineKeyboardMarkup:
    """
    Формирует инлайн-клавиатуру стартового сообщения:
    Ряд 1: Открыть Калькулятор
    Ряд 2: Оплатить подписку
    Ряд 3: Профиль, Условия, Поддержка
    """
    return InlineKeyboardMarkup(
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
            ],
            [
                InlineKeyboardButton(
                    text="👤 Профиль",
                    callback_data="bot_profile"
                ),
                InlineKeyboardButton(
                    text="📄 Условия",
                    callback_data="bot_terms"
                ),
                InlineKeyboardButton(
                    text="💬 Поддержка",
                    url=get_support_url()
                )
            ]
        ]
    )


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
    Для зарегистрированного пользователя проверяет подписку и выводит актуальный статус.
    """
    if command.args == "buy":
        await send_subscription_invoice(message.chat.id)
        return

    telegram_id = message.from_user.id
    first_name = html.escape(message.from_user.first_name or "Пользователь")
    now_utc = datetime.now(timezone.utc)
    trial_until = now_utc + timedelta(days=5)

    keyboard = get_start_keyboard()
    supabase = get_supabase()

    if not supabase:
        logger.warning(
            "Supabase клиент не инициализирован (проверьте SUPABASE_URL и SUPABASE_KEY в .env). "
            f"Статус для telegram_id={telegram_id} не может быть проверен."
        )
        welcome_text = (
            f"Привет, {first_name}! 👋\n\n"
            "Добро пожаловать в Судебный & Исполнительный Помощник PRO.\n\n"
            "Для работы нажмите кнопку ниже."
        )
        await message.answer(welcome_text, reply_markup=keyboard, parse_mode="HTML")
        return

    try:
        logger.info(f"Проверка существующей подписки в Supabase для telegram_id={telegram_id}...")
        response = supabase.table("subscriptions").select("subscription_until, trial_used").eq("telegram_id", telegram_id).execute()
        data = response.data
        logger.info(f"Данные из Supabase для telegram_id={telegram_id}: {data}")

        if not data:
            # Сценарий 1 (Новый пользователь): начисление 5 дней бесплатного доступа
            insert_payload = {
                "telegram_id": telegram_id,
                "subscription_until": trial_until.isoformat(),
                "trial_used": True
            }
            logger.info(f"Вставка новой записи триала в Supabase: {insert_payload}")
            supabase.table("subscriptions").insert(insert_payload).execute()

            welcome_text = (
                f"Привет, {first_name}! 👋\n\n"
                "Добро пожаловать в Судебный & Исполнительный Помощник PRO.\n\n"
                "🎁 Вам начислен бесплатный пробный доступ на 5 дней ко всем функциям калькулятора!\n\n"
                "Для работы нажмите кнопку ниже."
            )
        else:
            # Сценарий 2 (Повторный запуск, запись уже есть в базе): не начислять триал заново
            logger.info(
                f"Пользователь telegram_id={telegram_id} уже присутствует в базе. "
                "Повторный триал не начисляется."
            )
            sub_until_str = data[0].get("subscription_until")
            sub_until = None
            if sub_until_str:
                try:
                    sub_until = datetime.fromisoformat(sub_until_str.replace("Z", "+00:00"))
                except Exception as parse_err:
                    logger.error(f"Ошибка парсинга даты subscription_until: {parse_err}")

            if sub_until and sub_until > now_utc:
                date_str = sub_until.strftime("%d.%m.%Y %H:%M")
                welcome_text = (
                    f"С возвращением, {first_name}! 👋\n\n"
                    f"✅ Ваша подписка активна до: <b>{date_str}</b> (UTC).\n\n"
                    "Нажмите кнопку ниже для перехода в калькулятор."
                )
            else:
                welcome_text = (
                    f"С возвращением, {first_name}! 👋\n\n"
                    "❌ Срок действия вашего доступа истек.\n\n"
                    "Чтобы продолжить пользоваться калькулятором, оформите подписку на 30 дней за 299 ₽."
                )

        await message.answer(welcome_text, reply_markup=keyboard, parse_mode="HTML")

    except Exception as e:
        logger.exception(f"Ошибка при работе с таблицей subscriptions в Supabase для telegram_id={telegram_id}: {e}")
        welcome_text = (
            f"Привет, {first_name}! 👋\n\n"
            "Добро пожаловать в Судебный & Исполнительный Помощник PRO.\n\n"
            "Для работы нажмите кнопку ниже."
        )
        await message.answer(welcome_text, reply_markup=keyboard, parse_mode="HTML")


@dp.callback_query(F.data == "bot_profile")
async def process_profile_callback(callback_query: types.CallbackQuery) -> None:
    """
    Обработчик кнопки «👤 Профиль»
    Запрашивает статус подписки из Supabase и отправляет данные пользователю.
    """
    await callback_query.answer()
    telegram_id = callback_query.from_user.id
    first_name = html.escape(callback_query.from_user.first_name or "Пользователь")
    now_utc = datetime.now(timezone.utc)
    supabase = get_supabase()

    status_text = "Не оформлена"
    expires_str = "—"

    if supabase:
        try:
            response = supabase.table("subscriptions").select("subscription_until, trial_used").eq("telegram_id", telegram_id).execute()
            data = response.data
            if data and data[0].get("subscription_until"):
                sub_str = data[0]["subscription_until"]
                sub_until = datetime.fromisoformat(sub_str.replace("Z", "+00:00"))
                is_trial = bool(data[0].get("trial_used", False))
                expires_str = f"{sub_until.strftime('%d.%m.%Y %H:%M')} (UTC)"
                if sub_until > now_utc:
                    status_text = "🟢 Активна (пробный период 5 дней)" if is_trial else "🟢 Активна"
                else:
                    status_text = "🔴 Срок действия истек"
            else:
                status_text = "🔴 Не оформлена"
        except Exception as e:
            logger.error(f"Ошибка получения профиля из Supabase: {e}")
            status_text = "⚠️ Ошибка проверки"

    profile_text = (
        f"👤 <b>Профиль пользователя</b>\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{telegram_id}</code>\n"
        f"👤 <b>Имя:</b> {first_name}\n"
        f"📊 <b>Статус доступа:</b> {status_text}\n"
        f"⏳ <b>Действует до:</b> {expires_str}"
    )

    profile_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Оплатить подписку (299 ₽)",
                    callback_data="buy_subscription"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚖️ Открыть Калькулятор",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )
            ]
        ]
    )
    await callback_query.message.answer(profile_text, parse_mode="HTML", reply_markup=profile_keyboard)


@dp.callback_query(F.data == "bot_terms")
async def process_terms_callback(callback_query: types.CallbackQuery) -> None:
    """
    Обработчик кнопки «📄 Условия»
    Отправляет текст политики/условий сервиса.
    """
    await callback_query.answer()
    support_contact = f"@{SUPPORT_BOT_USERNAME}" if SUPPORT_BOT_USERNAME else "службу поддержки"
    terms_text = (
        "📄 <b>Условия использования и политика сервиса</b>\n\n"
        "1. <b>Назначение сервиса:</b> «Судебный & Исполнительный Помощник PRO» предоставляет специализированные "
        "расчетные инструменты для судебных юристов, арбитражных управляющих и взыскателей.\n\n"
        "2. <b>Пробный доступ:</b> Каждому новому пользователю при первой регистрации единоразово "
        "начисляется бесплатный пробный доступ ко всем калькуляторам на 5 дней.\n\n"
        "3. <b>Платная подписка:</b> Стоимость продления доступа составляет 299 ₽ на 30 дней. "
        "Платежи обрабатываются безопасно через официальные провайдеры Telegram.\n\n"
        "4. <b>Характер расчетов:</b> Все вычисления носят информационно-справочный характер. "
        "Пользователь самостоятельно верифицирует данные с нормами процессуального права РФ перед подачей документов в суд или ФССП.\n\n"
        f"5. <b>Техническая поддержка:</b> При вопросах или сбоях обращайтесь в {support_contact}."
    )
    await callback_query.message.answer(terms_text, parse_mode="HTML")



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
