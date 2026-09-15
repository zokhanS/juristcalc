import os
import re
import html
import sqlite3
import logging
import asyncio
from typing import Optional
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

# 1. Загрузка переменных окружения
load_dotenv(override=True)

# 2. Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("support_bot")

SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN")
admin_id_val = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
ADMIN_TELEGRAM_ID: Optional[int] = int(admin_id_val) if admin_id_val.isdigit() else None


# Путь к локальной SQLite базе данных для хранения связок сообщений
DB_PATH = os.path.join(os.path.dirname(__file__), "support_bot.db")

# Кэш в памяти message_id -> user_id
message_to_user: dict[int, int] = {}


def init_db():
    """Инициализирует таблицу для маппинга пересланных сообщений администратора на user_id."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS support_messages (
                    admin_message_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    user_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при инициализации SQLite базы данных: {e}")


def save_message_mapping(admin_message_id: int, user_id: int, user_name: str = ""):
    """Сохраняет связку admin_message_id -> user_id в памяти и SQLite."""
    message_to_user[admin_message_id] = user_id
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO support_messages (admin_message_id, user_id, user_name) VALUES (?, ?, ?)",
                (admin_message_id, user_id, user_name)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при сохранении сообщения в SQLite: {e}")


def get_user_id_by_message(admin_message_id: int) -> Optional[int]:
    """Возвращает user_id по ID сообщения администратора."""
    if admin_message_id in message_to_user:
        return message_to_user[admin_message_id]
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "SELECT user_id FROM support_messages WHERE admin_message_id = ?",
                (admin_message_id,)
            )
            row = cursor.fetchone()
            if row:
                user_id = row[0]
                message_to_user[admin_message_id] = user_id
                return user_id
    except Exception as e:
        logger.error(f"Ошибка при чтении из SQLite: {e}")
    return None


def extract_user_id_from_text(text: Optional[str]) -> Optional[int]:
    """Запасной метод извлечения user_id из текста или подписи пересланного сообщения."""
    if not text:
        return None
    # 1. Поиск по точному шаблону: ID: <code>12345</code> или ID пользователя: <code>12345</code>
    match = re.search(r"ID(?:\s*пользователя)?:\s*<code>(\d+)</code>", text, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    # 2. Общий поиск по ID/user_id/🆔
    match = re.search(r"(?:user_id|ID пользователя|ID|🆔)\D*(\d{5,15})", text, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


# Инициализация бота и диспетчера
support_bot: Optional[Bot] = Bot(token=SUPPORT_BOT_TOKEN) if SUPPORT_BOT_TOKEN else None
support_dp = Dispatcher()

# Инициализируем БД при старте модуля
init_db()


# --- ОБРАБОТЧИКИ СООБЩЕНИЙ ---

@support_dp.message(CommandStart())
async def command_start_handler(message: types.Message):
    """Обработчик команды /start."""
    user_id = message.from_user.id
    if ADMIN_TELEGRAM_ID and user_id == ADMIN_TELEGRAM_ID:
        await message.answer(
            "👋 Здравствуйте! Вы авторизованы как администратор техподдержки.\n\n"
            "Когда пользователи пишут в этого бота, их обращения поступают сюда. "
            "Для ответа пользователю используйте функцию Telegram <b>«Ответить» (Reply)</b> на сообщение.",
            parse_mode="HTML"
        )
        return

    await message.answer(
        "Здравствуйте! Напишите ваш вопрос или опишите проблему. Администратор ответит вам в этом чате."
    )


@support_dp.message(F.reply_to_message)
async def admin_reply_handler(message: types.Message):
    """
    Обработчик ответов администратора через Reply на пересланные обращения пользователей.
    """
    if not ADMIN_TELEGRAM_ID or message.from_user.id != ADMIN_TELEGRAM_ID:
        # Если отвечает не администратор, передаем сообщение в обычный обработчик
        await user_message_handler(message)
        return

    replied_message = message.reply_to_message
    if not replied_message:
        return

    # 1. Пытаемся получить target_user_id из локальной БД / памяти
    target_user_id = get_user_id_by_message(replied_message.message_id)

    # 2. Если не найден в БД, ищем через регулярное выражение в тексте цитируемого сообщения
    if not target_user_id:
        target_user_id = extract_user_id_from_text(replied_message.text or replied_message.caption)

    if not target_user_id:
        await message.reply(
            "⚠️ Не удалось определить пользователя для этого сообщения. "
            "Убедитесь, что вы отвечаете на сообщение с карточкой обращения пользователя."
        )
        return

    # 3. Отправка ответа пользователю в чат бота
    try:
        if message.text:
            await support_bot.send_message(
                chat_id=target_user_id,
                text=f"👨‍💻 <b>Ответ службы поддержки:</b>\n\n{message.text}",
                parse_mode="HTML"
            )
        elif message.photo:
            caption_text = f"👨‍💻 <b>Ответ службы поддержки:</b>\n\n{message.caption}" if message.caption else "👨‍💻 <b>Ответ службы поддержки</b>"
            await support_bot.send_photo(
                chat_id=target_user_id,
                photo=message.photo[-1].file_id,
                caption=caption_text,
                parse_mode="HTML"
            )
        elif message.document:
            caption_text = f"👨‍💻 <b>Ответ службы поддержки:</b>\n\n{message.caption}" if message.caption else "👨‍💻 <b>Ответ службы поддержки</b>"
            await support_bot.send_document(
                chat_id=target_user_id,
                document=message.document.file_id,
                caption=caption_text,
                parse_mode="HTML"
            )
        else:
            # Для прочих типов медиа (аудио, голосовые, стикеры)
            await support_bot.send_message(
                chat_id=target_user_id,
                text="👨‍💻 <b>Ответ службы поддержки:</b>",
                parse_mode="HTML"
            )
            await support_bot.copy_message(
                chat_id=target_user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )

        await message.reply("✅ Ответ успешно доставлен пользователю.")
        logger.info(f"Ответ успешно отправлен пользователю ID={target_user_id} от администратора.")

    except TelegramForbiddenError:
        logger.warning(f"Пользователь ID={target_user_id} заблокировал бота поддержки.")
        await message.reply("❌ Пользователь заблокировал бота. Доставка ответа невозможна.")
    except TelegramAPIError as e:
        logger.error(f"Ошибка доставки ответа пользователю {target_user_id}: {e}")
        await message.reply(f"❌ Ошибка отправки: {e.message}")
    except Exception as e:
        logger.exception(f"Непредвиденная ошибка при отправке пользователю {target_user_id}: {e}")
        await message.reply(f"❌ Ошибка при отправке ответа: {e}")


@support_dp.message()
async def user_message_handler(message: types.Message):
    """
    Обработчик всех входящих сообщений пользователей:
    пересылает сообщение администратору с карточкой заявителя.
    """
    user = message.from_user
    user_id = user.id

    # Если администратор пишет сообщение без Reply и это не обращение
    if ADMIN_TELEGRAM_ID and user_id == ADMIN_TELEGRAM_ID:
        await message.answer(
            "💡 Чтобы ответить пользователю, используйте функцию Telegram «Ответить» (Reply) на карточку обращения."
        )
        return

    if not ADMIN_TELEGRAM_ID:
        logger.error("ADMIN_TELEGRAM_ID не задан в .env файле! Обращение не может быть переслано.")
        await message.answer("⚠️ Служба поддержки временно недоступна. Пожалуйста, попробуйте позже.")
        return

    # Формируем информацию о заявителе
    full_name = html.escape(user.full_name or "Не указано")
    username_str = f"@{user.username}" if user.username else "отсутствует"

    header_text = (
        "📩 <b>Новое обращение в техподдержку</b>\n\n"
        f"👤 <b>Заявитель:</b> {full_name}\n"
        f"🔗 <b>Username:</b> {username_str}\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
        "Для ответа используйте функцию Telegram «Ответить» (Reply) на это сообщение"
    )

    try:
        if message.text:
            full_msg_text = f"{header_text}\n\n💬 <b>Текст обращения:</b>\n{html.escape(message.text)}"
            admin_msg = await support_bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=full_msg_text,
                parse_mode="HTML"
            )
            save_message_mapping(admin_msg.message_id, user_id, full_name)

        elif message.photo:
            user_caption = f"\n\n💬 <b>Подпись:</b> {html.escape(message.caption)}" if message.caption else ""
            caption_text = (header_text + user_caption)[:1024]
            admin_msg = await support_bot.send_photo(
                chat_id=ADMIN_TELEGRAM_ID,
                photo=message.photo[-1].file_id,
                caption=caption_text,
                parse_mode="HTML"
            )
            save_message_mapping(admin_msg.message_id, user_id, full_name)

        elif message.document:
            user_caption = f"\n\n💬 <b>Подпись:</b> {html.escape(message.caption)}" if message.caption else ""
            caption_text = (header_text + user_caption)[:1024]
            admin_msg = await support_bot.send_document(
                chat_id=ADMIN_TELEGRAM_ID,
                document=message.document.file_id,
                caption=caption_text,
                parse_mode="HTML"
            )
            save_message_mapping(admin_msg.message_id, user_id, full_name)

        else:
            # Для прочих сообщений отправляем информационную карточку, а затем копию контента
            header_msg = await support_bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=header_text,
                parse_mode="HTML"
            )
            admin_msg = await support_bot.copy_message(
                chat_id=ADMIN_TELEGRAM_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_to_message_id=header_msg.message_id
            )
            save_message_mapping(header_msg.message_id, user_id, full_name)
            save_message_mapping(admin_msg.message_id, user_id, full_name)

        await message.answer("Ваше обращение принято и передано администратору!")
        logger.info(f"Обращение от пользователя ID={user_id} ({full_name}) передано администратору.")

    except Exception as e:
        logger.exception(f"Ошибка при пересылке сообщения администратору: {e}")
        await message.answer("⚠️ Произошла ошибка при отправке обращения. Пожалуйста, попробуйте позже.")


async def main():
    """Точка входа для запуска бота в режиме самостоятельного процесса."""
    if not SUPPORT_BOT_TOKEN:
        logger.error("Критическая ошибка: SUPPORT_BOT_TOKEN не задан в .env файле!")
        print("❌ Не задан SUPPORT_BOT_TOKEN в файле .env. Бот техподдержки не может быть запущен.")
        return

    logger.info("🚀 Запуск бота технической поддержки...")
    await support_bot.delete_webhook(drop_pending_updates=True)
    await support_dp.start_polling(support_bot)


if __name__ == "__main__":
    asyncio.run(main())
