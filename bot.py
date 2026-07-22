import os
import asyncio
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN в файле .env")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Замените эти ссылки на ваши реальные
WEBAPP_URL = "здесь должен быть URL вашего WebApp"
PAYMENT_URL = "здесь должен быть URL оплаты Tome.ru"

@dp.message(CommandStart())
async def command_start_handler(message: types.Message) -> None:
    """
    Обработчик команды /start
    Отправляет приветственное сообщение и клавиатуру с кнопками WebApp и оплаты.
    """
    # Создаем инлайн клавиатуру
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
                    url=PAYMENT_URL
                )
            ]
        ]
    )

    welcome_text = (
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        "Добро пожаловать в Судебный & Исполнительный Помощник PRO.\n\n"
        "Для работы с калькулятором нажми кнопку ниже. Если у тебя еще нет подписки, "
        "ты можешь оформить её, нажав на соответствующую кнопку."
    )

    await message.answer(welcome_text, reply_markup=keyboard)


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
