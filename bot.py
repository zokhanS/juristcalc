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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.exceptions import TelegramBadRequest
from supadns import create_smart_client
from platega_service import create_platega_payment

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN в файле .env")


def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_smart_client(url, key)


# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище ID последнего сообщения меню для каждого чата (chat_id -> message_id)
last_menu_messages: dict[int, int] = {}

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://217-199-253-99.sslip.io").strip()
if "github.com" in WEBAPP_URL:
    WEBAPP_URL = "https://217-199-253-99.sslip.io"

SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME", "").replace("@", "").strip()
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
TELEGRAPH_TERMS_URL = "https://telegra.ph/Polzovatelskoe-soglashenie--Politika-konfidencialnosti-09-15"
TELEGRAPH_POLICY_URL = "https://telegra.ph/Politika-konfidencialnosti-09-16-70"


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
    Формирует инлайн-клавиатуру главного меню:
    Ряд 1: ⚖️ Открыть Калькулятор
    Ряд 2: 💳 Оплатить подписку
    Ряд 3: 👤 Профиль, 📄 Условия, 💬 Поддержка
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


def get_profile_keyboard() -> InlineKeyboardMarkup:
    """
    Формирует инлайн-клавиатуру меню профиля:
    Ряд 1: 💳 Оплатить подписку (299 ₽)
    Ряд 2: ⚖️ Открыть Калькулятор
    Ряд 3: « Назад в меню
    """
    return InlineKeyboardMarkup(
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
            ],
            [
                InlineKeyboardButton(
                    text="« Назад в меню",
                    callback_data="back_to_menu"
                )
            ]
        ]
    )


def get_terms_keyboard() -> InlineKeyboardMarkup:
    """
    Формирует инлайн-клавиатуру меню условий:
    Ряд 1: 📄 Пользовательское соглашение
    Ряд 2: 🔒 Политика конфиденциальности
    Ряд 3: « Назад в меню
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 Пользовательское соглашение",
                    url=TELEGRAPH_TERMS_URL
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔒 Политика конфиденциальности",
                    url=TELEGRAPH_POLICY_URL
                )
            ],
            [
                InlineKeyboardButton(
                    text="« Назад в меню",
                    callback_data="back_to_menu"
                )
            ]
        ]
    )


async def send_platega_payment(chat_id: int, user_id: int) -> None:
    """
    Генерирует ссылку на оплату через Platega.io и отправляет инлайн-сообщение пользователю.
    """
    try:
        payment_url = await create_platega_payment(telegram_id=user_id, amount=299.0, days=30)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате 299 ₽", url=payment_url)],
            [InlineKeyboardButton(text="« Назад в меню", callback_data="back_to_menu")]
        ])
        sent_msg = await bot.send_message(
            chat_id=chat_id,
            text=(
                "💳 <b>Оформление подписки на «Юридический помощник»</b>\n\n"
                "• <b>Тариф:</b> Полный доступ на 30 дней\n"
                "• <b>Стоимость:</b> 299 ₽\n"
                "• <b>Способы оплаты:</b> Банковские карты (МИР, Visa, Mastercard), СБП\n\n"
                "После подтверждения оплаты все разделы калькулятора откроются автоматически."
            ),
            reply_markup=kb,
            parse_mode="HTML"
        )
        if sent_msg and hasattr(sent_msg, "message_id"):
            last_menu_messages[chat_id] = sent_msg.message_id
    except Exception as e:
        logger.exception(f"Ошибка при формировании счета Platega для user_id={user_id}: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text="⚠️ Не удалось сформировать ссылку на оплату. Пожалуйста, попробуйте позже или обратитесь в поддержку.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад в меню", callback_data="back_to_menu")]
            ])
        )


async def get_start_text(telegram_id: int, first_name: str) -> str:
    """
    Формирует текст главного меню со статусом доступа.
    Для нового пользователя начисляет 5 дней бесплатного пробного периода в Supabase.
    """
    first_name = html.escape(first_name or "Пользователь")
    now_utc = datetime.now(timezone.utc)
    trial_until = now_utc + timedelta(days=5)

    supabase = get_supabase()

    if not supabase:
        logger.warning(
            "Supabase клиент не инициализирован (проверьте SUPABASE_URL и SUPABASE_KEY в .env). "
            f"Статус для telegram_id={telegram_id} не может быть проверен."
        )
        return (
            f"Привет, {first_name}! 👋\n\n"
            "Добро пожаловать в «Юридический помощник».\n\n"
            "Для работы нажмите кнопку ниже."
        )

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
                "trial_used": False
            }
            logger.info(f"Вставка новой записи триала в Supabase: {insert_payload}")
            supabase.table("subscriptions").insert(insert_payload).execute()

            return (
                f"Привет, {first_name}! 👋\n\n"
                "Добро пожаловать в «Юридический помощник».\n\n"
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
                return (
                    f"С возвращением, {first_name}! 👋\n\n"
                    f"✅ Ваша подписка активна до: <b>{date_str}</b> (UTC).\n\n"
                    "Нажмите кнопку ниже для перехода в калькулятор."
                )
            else:
                return (
                    f"С возвращением, {first_name}! 👋\n\n"
                    "❌ Срок действия вашего доступа истек.\n\n"
                    "Чтобы продолжить пользоваться калькулятором, оформите подписку на 30 дней за 299 ₽."
                )

    except Exception as e:
        logger.exception(f"Ошибка при работе с таблицей subscriptions в Supabase для telegram_id={telegram_id}: {e}")
        return (
            f"Привет, {first_name}! 👋\n\n"
            "Добро пожаловать в «Юридический помощник».\n\n"
            "Для работы нажмите кнопку ниже."
        )


@dp.message(CommandStart())
async def command_start_handler(message: types.Message, command: CommandObject) -> None:
    """
    Обработчик команды /start:
    1. Удаляет само сообщение команды /start пользователя (чистый чат).
    2. Если бот ранее отправлял меню в этот чат — удаляет предыдущее сообщение.
    3. При диплинке 'buy' (/start buy) отправляет инвойс на оплату.
    4. Отправляет актуальное главное меню и сохраняет ID отправленного сообщения.
    """
    # 1. Удаление команды /start от пользователя
    try:
        await message.delete()
    except Exception:
        pass

    if command and command.args == "buy":
        await send_platega_payment(message.chat.id, message.from_user.id)
        return

    # 2. Удаление предыдущего сообщения меню бота
    last_id = last_menu_messages.get(message.chat.id)
    if last_id:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=last_id)
        except Exception:
            pass

    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or "Пользователь"
    welcome_text = await get_start_text(telegram_id, first_name)
    keyboard = get_start_keyboard()

    sent_msg = await message.answer(welcome_text, reply_markup=keyboard, parse_mode="HTML")
    if sent_msg and hasattr(sent_msg, "message_id"):
        last_menu_messages[message.chat.id] = sent_msg.message_id


async def get_profile_text(telegram_id: int, first_name: str) -> str:
    """
    Формирует текст профиля пользователя с актуальным статусом подписки.
    """
    first_name = html.escape(first_name or "Пользователь")
    now_utc = datetime.now(timezone.utc)
    supabase = get_supabase()

    status_badge = "🔴 <b>Не активна</b>"
    expires_str = "—"

    if supabase:
        try:
            response = supabase.table("subscriptions").select("subscription_until, trial_used").eq("telegram_id", telegram_id).execute()
            data = response.data
            if data and data[0].get("subscription_until"):
                row = data[0]
                sub_str = str(row.get("subscription_until")).replace("Z", "+00:00").replace(" ", "T")
                sub_until = datetime.fromisoformat(sub_str)
                if sub_until.tzinfo is None:
                    sub_until = sub_until.replace(tzinfo=timezone.utc)
                trial_used = bool(row.get("trial_used", False))
                is_active = sub_until > now_utc
                expires_str = f"{sub_until.strftime('%d.%m.%Y %H:%M')} (UTC)"
                if is_active:
                    if trial_used:
                        status_badge = "🟢 <b>Подписка активна</b>"
                    else:
                        status_badge = "🟡 <b>Пробный период (5 дней)</b>"
                else:
                    status_badge = "🔴 <b>Не активна</b>"
            else:
                status_badge = "🔴 <b>Не активна</b>"
        except Exception as e:
            logger.error(f"Ошибка получения профиля из Supabase: {e}")
            status_badge = "⚠️ <b>Ошибка проверки</b>"

    return (
        f"👤 <b>Профиль пользователя</b>\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{telegram_id}</code>\n"
        f"👤 <b>Имя:</b> {first_name}\n"
        f"📊 <b>Статус доступа:</b> {status_badge}\n"
        f"⏳ <b>Действует до:</b> {expires_str}"
    )


@dp.message(Command("profile"))
async def command_profile_handler(message: types.Message) -> None:
    """
    Обработчик команды /profile
    """
    try:
        await message.delete()
    except Exception:
        pass
    telegram_id = message.from_user.id
    first_name = message.from_user.first_name or "Пользователь"
    profile_text = await get_profile_text(telegram_id, first_name)
    sent_msg = await message.answer(
        profile_text,
        reply_markup=get_profile_keyboard(),
        parse_mode="HTML"
    )
    if sent_msg and hasattr(sent_msg, "message_id"):
        last_menu_messages[message.chat.id] = sent_msg.message_id


@dp.callback_query(F.data == "bot_profile")
async def process_profile_callback(callback_query: types.CallbackQuery) -> None:
    """
    Обработчик кнопки «👤 Профиль»
    Запрашивает статус подписки из Supabase и редактирует текущее сообщение (In-place Navigation).
    """
    await callback_query.answer()
    telegram_id = callback_query.from_user.id
    first_name = callback_query.from_user.first_name or "Пользователь"
    profile_text = await get_profile_text(telegram_id, first_name)

    if callback_query.message and hasattr(callback_query.message, "chat"):
        last_menu_messages[callback_query.message.chat.id] = callback_query.message.message_id

    try:
        await callback_query.message.edit_text(
            profile_text,
            reply_markup=get_profile_keyboard(),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data == "bot_terms")
async def process_terms_callback(callback_query: types.CallbackQuery) -> None:
    """
    Обработчик кнопки «📄 Условия»:
    Выводит всплывающее уведомление и предлагает открыть документ в Telegraph с Instant View.
    """
    # Всплывающее уведомление в интерфейсе Telegram
    await callback_query.answer("Вы открыли окно с политика/условия")

    terms_nav_text = (
        "📄 <b>Политика конфиденциальности и Пользовательское соглашение</b>\n"
        "<i>Редакция от 15.09.2026 г.</i>\n\n"
        "Ознакомиться с официальными текстами документов сервиса «Юридический помощник» "
        "вы можете по ссылкам ниже в формате Instant View:\n\n"
        "• <b>Пользовательское соглашение</b> — условия предоставления услуг, правила подписки и возврата.\n"
        "• <b>Политика конфиденциальности</b> — порядок сбора, обработки и защиты технических данных."
    )

    if callback_query.message and hasattr(callback_query.message, "chat"):
        last_menu_messages[callback_query.message.chat.id] = callback_query.message.message_id

    try:
        await callback_query.message.edit_text(
            terms_nav_text,
            reply_markup=get_terms_keyboard(),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass



@dp.callback_query(F.data == "back_to_menu")
async def process_back_to_menu_callback(callback_query: types.CallbackQuery) -> None:
    """
    Обработчик кнопки «« Назад в меню»
    Редактирует текущее сообщение обратно в главное меню (In-place Navigation).
    """
    await callback_query.answer()
    telegram_id = callback_query.from_user.id
    first_name = callback_query.from_user.first_name or "Пользователь"
    menu_text = await get_start_text(telegram_id, first_name)

    if callback_query.message and hasattr(callback_query.message, "chat"):
        last_menu_messages[callback_query.message.chat.id] = callback_query.message.message_id

    try:
        await callback_query.message.edit_text(
            menu_text,
            reply_markup=get_start_keyboard(),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass



@dp.message(Command("buy"))
async def command_buy_handler(message: types.Message) -> None:
    """
    Обработчик команды /buy для генерации ссылки на оплату через Platega.io.
    """
    await send_platega_payment(message.chat.id, message.from_user.id)


@dp.callback_query(F.data == "buy_subscription")
async def process_buy_callback(callback_query: types.CallbackQuery) -> None:
    """
    Обработчик нажатия на инлайн-кнопку «💳 Оплатить подписку».
    Генерирует ссылку через Platega.io и отправляет сообщение с кнопкой перехода к оплате.
    """
    await callback_query.answer()
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id if callback_query.message else user_id

    try:
        payment_url = await create_platega_payment(telegram_id=user_id, amount=299.0, days=30)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате 299 ₽", url=payment_url)],
            [InlineKeyboardButton(text="« Назад в меню", callback_data="back_to_menu")]
        ])
        payment_text = (
            "💳 <b>Оформление подписки на «Юридический помощник»</b>\n\n"
            "• <b>Тариф:</b> Полный доступ на 30 дней\n"
            "• <b>Стоимость:</b> 299 ₽\n"
            "• <b>Способы оплаты:</b> Банковские карты (МИР, Visa, Mastercard), СБП\n\n"
            "После подтверждения оплаты все разделы калькулятора откроются автоматически."
        )
        if callback_query.message:
            last_menu_messages[chat_id] = callback_query.message.message_id
            try:
                await callback_query.message.edit_text(
                    payment_text,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            except TelegramBadRequest:
                sent_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=payment_text,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
                if sent_msg and hasattr(sent_msg, "message_id"):
                    last_menu_messages[chat_id] = sent_msg.message_id
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=payment_text,
                reply_markup=kb,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.exception(f"Ошибка при создании счета Platega для user_id={user_id}: {e}")
        error_text = "⚠️ Не удалось сформировать ссылку на оплату. Пожалуйста, попробуйте позже или обратитесь в поддержку."
        err_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад в меню", callback_data="back_to_menu")]
        ])
        if callback_query.message:
            try:
                await callback_query.message.edit_text(error_text, reply_markup=err_kb)
            except TelegramBadRequest:
                await bot.send_message(chat_id=chat_id, text=error_text, reply_markup=err_kb)
        else:
            await bot.send_message(chat_id=chat_id, text=error_text, reply_markup=err_kb)


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
