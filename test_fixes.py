import sys
import asyncio
import os
import json
import urllib.request
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

load_dotenv(override=True)

import bot


async def test_webapp_url():
    print("=" * 60)
    print("ТЕСТ 1: Проверка актуальности URL WebApp")
    print("=" * 60)

    # 1. Проверяем WEBAPP_URL в bot.py
    print(f"[CHECK] bot.WEBAPP_URL = '{bot.WEBAPP_URL}'")
    assert bot.WEBAPP_URL == "https://217-199-253-99.sslip.io", f"Ожидался URL https://217-199-253-99.sslip.io, получен {bot.WEBAPP_URL}"

    # 2. Проверяем кнопку в command_start_handler
    mock_message = AsyncMock()
    mock_message.chat.id = 123456
    mock_message.from_user.id = 999999999
    mock_message.from_user.first_name = "Иван"
    mock_command = MagicMock()
    mock_command.args = None

    await bot.command_start_handler(mock_message, mock_command)

    # Проверяем переданную клавиатуру
    call_args = mock_message.answer.call_args
    assert call_args is not None, "message.answer не был вызван"
    _, kwargs = call_args
    reply_markup = kwargs.get("reply_markup")
    assert reply_markup is not None, "reply_markup отсутствует в ответе"

    first_button = reply_markup.inline_keyboard[0][0]
    print(f"[CHECK] Текст кнопки: '{first_button.text}'")
    print(f"[CHECK] URL в WebAppInfo: '{first_button.web_app.url}'")
    assert first_button.web_app.url == "https://217-199-253-99.sslip.io", f"Неверный URL в кнопке: {first_button.web_app.url}"

    # 3. Проверяем Menu Button в Telegram Bot API
    bot_token = os.getenv("BOT_TOKEN")
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getChatMenuButton"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            menu_button = data.get("result", {})
            print(f"[CHECK] Telegram Menu Button API result: {menu_button}")
            if menu_button.get("type") == "web_app":
                menu_url = menu_button.get("web_app", {}).get("url")
                print(f"[CHECK] Telegram Menu Button URL: {menu_url}")
    except Exception as e:
        print(f"[WARN] Не удалось запросить getChatMenuButton: {e}")

    print("\n✅ ТЕСТ 1 УСПЕШНО ПРОЙДЕН!\n")


async def test_supabase_start_logic():
    print("=" * 60)
    print("ТЕСТ 2: Проверка логики регистрации триала в Supabase при /start")
    print("=" * 60)

    test_user_id = 777888999
    mock_message = AsyncMock()
    mock_message.chat.id = test_user_id
    mock_message.from_user.id = test_user_id
    mock_message.from_user.first_name = "Тест"
    mock_command = MagicMock()
    mock_command.args = None

    # Моделируем сценарий 1: Нового пользователя нет в базе (data = [])
    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_eq = MagicMock()
    mock_insert = MagicMock()

    mock_supabase.table.return_value = mock_table
    mock_table.select.return_value = mock_select
    mock_select.eq.return_value = mock_eq
    
    # 1. Первая попытка: пользователя нет в базе
    mock_eq.execute.return_value = MagicMock(data=[])
    
    # Реакция на insert
    def mock_insert_execute():
        payload = mock_table.insert.call_args[0][0]
        return MagicMock(data=[payload])

    mock_table.insert.return_value = mock_insert
    mock_insert.execute.side_effect = mock_insert_execute

    # Подменяем get_supabase на mock
    original_get_supabase = bot.get_supabase
    bot.get_supabase = lambda: mock_supabase

    try:
        print("\n--- Тестирование первого вызова /start (новый пользователь) ---")
        await bot.command_start_handler(mock_message, mock_command)

        # Проверяем, что был вызван select
        mock_table.select.assert_called_with("subscription_until, trial_used")
        mock_select.eq.assert_called_with("telegram_id", test_user_id)

        # Проверяем, что был вызван insert
        assert mock_table.insert.called, "Метод insert НЕ был вызван для нового пользователя!"
        inserted_payload = mock_table.insert.call_args[0][0]
        print(f"[CHECK] Переданные данные в insert: {inserted_payload}")

        assert inserted_payload["telegram_id"] == test_user_id, f"Неверный telegram_id: {inserted_payload.get('telegram_id')}"
        assert inserted_payload["trial_used"] is True, "Флаг trial_used должен быть True"
        
        # Проверяем дату триала (+5 дней)
        sub_until = datetime.fromisoformat(inserted_payload["subscription_until"])
        now = datetime.now(timezone.utc)
        diff_days = (sub_until - now).total_seconds() / 86400
        print(f"[CHECK] Рассчитанный срок действия триала: {sub_until} (через {diff_days:.2f} дней)")
        assert 4.9 <= diff_days <= 5.1, f"Ожидалось около 5 дней, получено {diff_days}"

        print("\n--- Тестирование повторного вызова /start (пользователь уже есть в базе) ---")
        # Теперь имитируем, что запись уже есть в базе
        mock_table.insert.reset_mock()
        mock_eq.execute.return_value = MagicMock(data=[inserted_payload])

        await bot.command_start_handler(mock_message, mock_command)

        assert not mock_table.insert.called, "Метод insert НЕ должен вызываться, если пользователь уже есть в базе!"
        print("[CHECK] Повторный insert не вызывался, как и положено.")

    finally:
        bot.get_supabase = original_get_supabase

    print("\n✅ ТЕСТ 2 УСПЕШНО ПРОЙДЕН!\n")


async def test_live_supabase_call():
    print("=" * 60)
    print("ТЕСТ 3: Вызов реального обработчика с текущей конфигурацией Supabase")
    print("=" * 60)
    print(f"Текущий SUPABASE_URL: {os.getenv('SUPABASE_URL')}")
    print(f"Текущий SUPABASE_KEY: {os.getenv('SUPABASE_KEY')[:20]}...{os.getenv('SUPABASE_KEY')[-10:]}")

    test_user_id = 123456789
    mock_message = AsyncMock()
    mock_message.chat.id = test_user_id
    mock_message.from_user.id = test_user_id
    mock_message.from_user.first_name = "РеальныйТест"
    mock_command = MagicMock()
    mock_command.args = None

    print("\nЗапуск bot.command_start_handler с логированием...")
    await bot.command_start_handler(mock_message, mock_command)
    print("\nКоманда /start успешно обработана (бот не упал, логи сформированы).")


async def main():
    await test_webapp_url()
    await test_supabase_start_logic()
    await test_live_supabase_call()


if __name__ == "__main__":
    asyncio.run(main())
