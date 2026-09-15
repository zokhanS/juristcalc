import sys
import asyncio
import os
import json
import urllib.request
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

load_dotenv(override=True)

import bot
import support_bot
import main


async def test_webapp_and_keyboard():
    print("=" * 60)
    print("ТЕСТ 1: Проверка актуальности URL WebApp и структуры клавиатуры")
    print("=" * 60)

    # 1. Проверяем WEBAPP_URL в bot.py
    print(f"[CHECK] bot.WEBAPP_URL = '{bot.WEBAPP_URL}'")
    assert bot.WEBAPP_URL == "https://217-199-253-99.sslip.io", f"Ожидался URL https://217-199-253-99.sslip.io, получен {bot.WEBAPP_URL}"

    # 2. Проверяем структуру клавиатуры get_start_keyboard
    kb = bot.get_start_keyboard()
    rows = kb.inline_keyboard
    assert len(rows) == 3, f"Ожидалось 3 ряда кнопок, получено {len(rows)}"

    # Ряд 1: Открыть Калькулятор
    assert len(rows[0]) == 1
    btn_webapp = rows[0][0]
    print(f"[CHECK] Ряд 1: '{btn_webapp.text}' -> {btn_webapp.web_app.url}")
    assert "Открыть Калькулятор" in btn_webapp.text
    assert btn_webapp.web_app.url == bot.WEBAPP_URL

    # Ряд 2: Оплатить подписку
    assert len(rows[1]) == 1
    btn_pay = rows[1][0]
    print(f"[CHECK] Ряд 2: '{btn_pay.text}' -> callback_data={btn_pay.callback_data}")
    assert "Оплатить подписку" in btn_pay.text
    assert btn_pay.callback_data == "buy_subscription"

    # Ряд 3: Профиль, Условия, Поддержка
    assert len(rows[2]) == 3
    btn_prof, btn_terms, btn_supp = rows[2][0], rows[2][1], rows[2][2]
    print(f"[CHECK] Ряд 3: '{btn_prof.text}', '{btn_terms.text}', '{btn_supp.text}' -> {btn_supp.url}")
    assert "Профиль" in btn_prof.text
    assert btn_prof.callback_data == "bot_profile"
    assert "Условия" in btn_terms.text
    assert btn_terms.callback_data == "bot_terms"
    assert "Поддержка" in btn_supp.text
    expected_support_url = f"https://t.me/{bot.SUPPORT_BOT_USERNAME}" if bot.SUPPORT_BOT_USERNAME else "https://t.me/"
    assert btn_supp.url == expected_support_url, f"Неверный URL поддержки: {btn_supp.url}"

    print("\n✅ ТЕСТ 1 УСПЕШНО ПРОЙДЕН!\n")


async def test_supabase_start_scenarios():
    print("=" * 60)
    print("ТЕСТ 2: Проверка сценариев /start (Новый пользователь, Активная и Истекшая подписка)")
    print("=" * 60)

    test_user_id = 777888999
    mock_message = AsyncMock()
    mock_message.chat.id = test_user_id
    mock_message.from_user.id = test_user_id
    mock_message.from_user.first_name = "Алексей"
    mock_command = MagicMock()
    mock_command.args = None

    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_eq = MagicMock()
    mock_insert = MagicMock()

    mock_supabase.table.return_value = mock_table
    mock_table.select.return_value = mock_select
    mock_select.eq.return_value = mock_eq

    def mock_insert_execute():
        payload = mock_table.insert.call_args[0][0]
        return MagicMock(data=[payload])

    mock_table.insert.return_value = mock_insert
    mock_insert.execute.side_effect = mock_insert_execute

    original_get_supabase = bot.get_supabase
    bot.get_supabase = lambda: mock_supabase

    try:
        # --- СЦЕНАРИЙ 1: Новый пользователь (not data) ---
        print("\n--- Сценарий 1: Новый пользователь (первый запуск) ---")
        mock_eq.execute.return_value = MagicMock(data=[])
        mock_table.insert.reset_mock()

        await bot.command_start_handler(mock_message, mock_command)

        assert mock_table.insert.called, "Метод insert НЕ был вызван для нового пользователя!"
        inserted_payload = mock_table.insert.call_args[0][0]
        print(f"[CHECK] Переданные данные в insert: {inserted_payload}")
        assert inserted_payload["telegram_id"] == test_user_id
        assert inserted_payload["trial_used"] is True

        call_args = mock_message.answer.call_args
        msg_text = call_args[0][0]
        print(f"[CHECK] Текст сообщения нового пользователя:\n{msg_text}")
        assert "бесплатный пробный доступ на 5 дней" in msg_text, "Сообщение должно содержать начисление триала на 5 дней"

        # --- СЦЕНАРИЙ 2А: Повторный вход с активной подпиской ---
        print("\n--- Сценарий 2А: Повторный вход (подписка АКТИВНА) ---")
        mock_table.insert.reset_mock()
        future_date = datetime.now(timezone.utc) + timedelta(days=15)
        mock_eq.execute.return_value = MagicMock(data=[{
            "telegram_id": test_user_id,
            "subscription_until": future_date.isoformat(),
            "trial_used": True
        }])

        await bot.command_start_handler(mock_message, mock_command)

        assert not mock_table.insert.called, "Метод insert НЕ должен вызываться для существующего пользователя!"
        call_args = mock_message.answer.call_args
        msg_text = call_args[0][0]
        print(f"[CHECK] Текст сообщения при активной подписке:\n{msg_text}")
        assert "С возвращением" in msg_text
        assert "Ваша подписка активна до:" in msg_text
        expected_date_str = future_date.strftime("%d.%m.%Y %H:%M")
        assert expected_date_str in msg_text, f"Ожидалась дата {expected_date_str} в тексте"
        assert "бесплатный пробный доступ на 5 дней" not in msg_text, "Триал не должен предлагаться повторно!"

        # --- СЦЕНАРИЙ 2Б: Повторный вход с истекшей подпиской ---
        print("\n--- Сценарий 2Б: Повторный вход (подписка ИСТЕКЛА) ---")
        mock_table.insert.reset_mock()
        past_date = datetime.now(timezone.utc) - timedelta(days=2)
        mock_eq.execute.return_value = MagicMock(data=[{
            "telegram_id": test_user_id,
            "subscription_until": past_date.isoformat(),
            "trial_used": True
        }])

        await bot.command_start_handler(mock_message, mock_command)

        assert not mock_table.insert.called, "Метод insert НЕ должен вызываться для существующего пользователя!"
        call_args = mock_message.answer.call_args
        msg_text = call_args[0][0]
        print(f"[CHECK] Текст сообщения при истекшей подписке:\n{msg_text}")
        assert "С возвращением" in msg_text
        assert "Срок действия вашего доступа истек" in msg_text
        assert "299 ₽" in msg_text
        assert "бесплатный пробный доступ на 5 дней" not in msg_text, "Триал не должен предлагаться повторно!"

    finally:
        bot.get_supabase = original_get_supabase

    print("\n✅ ТЕСТ 2 УСПЕШНО ПРОЙДЕН!\n")


async def test_bot_callbacks():
    print("=" * 60)
    print("ТЕСТ 3: Проверка callback-обработчиков bot_profile и bot_terms")
    print("=" * 60)

    test_user_id = 555666777
    mock_cb = AsyncMock()
    mock_cb.from_user.id = test_user_id
    mock_cb.from_user.first_name = "Елена"
    mock_cb.message = AsyncMock()

    # 1. Проверяем bot_profile
    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_eq = MagicMock()

    mock_supabase.table.return_value = mock_table
    mock_table.select.return_value = mock_select
    mock_select.eq.return_value = mock_eq

    future_date = datetime.now(timezone.utc) + timedelta(days=4)
    mock_eq.execute.return_value = MagicMock(data=[{
        "telegram_id": test_user_id,
        "subscription_until": future_date.isoformat(),
        "trial_used": True
    }])

    original_get_supabase = bot.get_supabase
    bot.get_supabase = lambda: mock_supabase

    try:
        await bot.process_profile_callback(mock_cb)
        assert mock_cb.answer.called
        call_args = mock_cb.message.answer.call_args
        profile_text = call_args[0][0]
        print(f"[CHECK] Ответ на callback bot_profile:\n{profile_text}")
        assert str(test_user_id) in profile_text
        assert "Елена" in profile_text
        assert "Активна" in profile_text
        assert future_date.strftime("%d.%m.%Y %H:%M") in profile_text
    finally:
        bot.get_supabase = original_get_supabase

    # 2. Проверяем bot_terms
    mock_cb.reset_mock()
    mock_cb.message.reset_mock()

    await bot.process_terms_callback(mock_cb)
    assert mock_cb.answer.called
    call_args = mock_cb.message.answer.call_args
    terms_text = call_args[0][0]
    print(f"\n[CHECK] Ответ на callback bot_terms:\n{terms_text[:150]}...")
    assert "Условия использования" in terms_text
    assert "299 ₽" in terms_text

    print("\n✅ ТЕСТ 3 УСПЕШНО ПРОЙДЕН!\n")


async def test_support_bot_functionality():
    print("=" * 60)
    print("ТЕСТ 4: Проверка логики support_bot (regex, маппинг и Reply-ответы)")
    print("=" * 60)

    # 1. Проверка regex извлечения user_id
    sample_text1 = "📩 Новое обращение\n🆔 ID: <code>12345678</code>\nДля ответа..."
    sample_text2 = "📩 Новое обращение\n🆔 ID пользователя: <code>87654321</code>\nПодпись"
    sample_text3 = "🆔 user_id: 99887766 сообщение"

    assert support_bot.extract_user_id_from_text(sample_text1) == 12345678, "Не удалось извлечь user_id из ID: <code>...</code>"
    assert support_bot.extract_user_id_from_text(sample_text2) == 87654321, "Не удалось извлечь user_id из ID пользователя: <code>...</code>"
    assert support_bot.extract_user_id_from_text(sample_text3) == 99887766, "Не удалось извлечь user_id из user_id: ..."
    print("[CHECK] Извлечение user_id через регулярные выражения работает корректно.")

    # 2. Проверка Reply-ответа администратора (с БД-маппингом и fallback-регексом)
    mock_admin_msg = AsyncMock()
    mock_admin_msg.from_user.id = support_bot.ADMIN_TELEGRAM_ID
    mock_admin_msg.text = "Здравствуйте! Решили вашу проблему."
    mock_replied_msg = MagicMock()
    mock_replied_msg.message_id = 991122
    mock_replied_msg.text = sample_text1
    mock_replied_msg.caption = None
    mock_admin_msg.reply_to_message = mock_replied_msg

    # Имитируем поддержку отправки сообщения
    mock_support_bot_instance = AsyncMock()
    with patch.object(support_bot, "support_bot", mock_support_bot_instance):
        # 2А: Пользователь есть в SQLite/памяти
        support_bot.save_message_mapping(991122, 12345678, "Алексей")
        await support_bot.admin_reply_handler(mock_admin_msg)

        mock_support_bot_instance.send_message.assert_called_with(
            chat_id=12345678,
            text="👨‍💻 <b>Ответ службы поддержки:</b>\n\nЗдравствуйте! Решили вашу проблему.",
            parse_mode="HTML"
        )
        print("[CHECK] Reply-ответ по связке из БД доставлен успешно.")

        # 2Б: Связки в БД нет, извлекается через regex из текста цитаты
        mock_support_bot_instance.reset_mock()
        mock_replied_msg.message_id = 884422  # Новый ID без записи в базе
        await support_bot.admin_reply_handler(mock_admin_msg)

        mock_support_bot_instance.send_message.assert_called_with(
            chat_id=12345678,
            text="👨‍💻 <b>Ответ службы поддержки:</b>\n\nЗдравствуйте! Решили вашу проблему.",
            parse_mode="HTML"
        )
        print("[CHECK] Reply-ответ по regex из цитаты доставлен успешно.")

        # 2В: Обработка TelegramForbiddenError (пользователь заблокировал бота)
        mock_support_bot_instance.send_message.side_effect = support_bot.TelegramForbiddenError(
            method=MagicMock(), message="Forbidden: bot was blocked by the user"
        )
        await support_bot.admin_reply_handler(mock_admin_msg)
        mock_admin_msg.reply.assert_called_with("❌ Пользователь заблокировал бота. Доставка ответа невозможна.")
        print("[CHECK] Ошибка TelegramForbiddenError корректно перехвачена и администратор уведомлен.")

    print("\n✅ ТЕСТ 4 УСПЕШНО ПРОЙДЕН!\n")


async def test_main_config_and_api():
    print("=" * 60)
    print("ТЕСТ 5: Проверка конфигурации main.py и параметров запуска")
    print("=" * 60)

    # 1. Проверяем RUN_SUPPORT_IN_MAIN
    print(f"[CHECK] main.RUN_SUPPORT_IN_MAIN = {main.RUN_SUPPORT_IN_MAIN}")
    assert main.RUN_SUPPORT_IN_MAIN is False, "RUN_SUPPORT_IN_MAIN должен быть False, чтобы не конфликтовать с systemd"

    # 2. Проверяем SUPPORT_BOT_USERNAME
    print(f"[CHECK] main.SUPPORT_BOT_USERNAME = '{main.SUPPORT_BOT_USERNAME}'")
    assert main.SUPPORT_BOT_USERNAME == "urcalcsupport_bot"

    print("\n✅ ТЕСТ 5 УСПЕШНО ПРОЙДЕН!\n")


async def main_test_suite():
    await test_webapp_and_keyboard()
    await test_supabase_start_scenarios()
    await test_bot_callbacks()
    await test_support_bot_functionality()
    await test_main_config_and_api()
    print("=" * 60)
    print("🎉 ВСЕ 5 ТЕСТОВЫХ НАБОРОВ УСПЕШНО ПРОЙДЕНЫ!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main_test_suite())
