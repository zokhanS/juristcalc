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

from contextlib import contextmanager

@contextmanager
def patch_env(new_vars):
    old_values = {k: os.environ.get(k) for k in new_vars}
    os.environ.update(new_vars)
    try:
        yield
    finally:
        for k, v in old_values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

import httpx
import platega_service
import bot
import support_bot
import main


async def test_webapp_and_keyboard():
    print("=" * 60)
    print("ТЕСТ 1: Проверка актуальности URL WebApp и структуры клавиатур")
    print("=" * 60)

    # 1. Проверяем WEBAPP_URL в bot.py
    print(f"[CHECK] bot.WEBAPP_URL = '{bot.WEBAPP_URL}'")
    assert bot.WEBAPP_URL == "https://217-199-253-99.sslip.io", f"Ожидался URL https://217-199-253-99.sslip.io, получен {bot.WEBAPP_URL}"

    # 2. Проверяем структуру клавиатуры get_start_keyboard
    kb = bot.get_start_keyboard()
    rows = kb.inline_keyboard
    assert len(rows) == 3, f"Ожидалось 3 ряда кнопок в главном меню, получено {len(rows)}"

    # Ряд 1: Открыть Калькулятор
    assert len(rows[0]) == 1
    btn_webapp = rows[0][0]
    print(f"[CHECK] Главное меню Ряд 1: '{btn_webapp.text}' -> {btn_webapp.web_app.url}")
    assert "Открыть Калькулятор" in btn_webapp.text
    assert btn_webapp.web_app.url == bot.WEBAPP_URL

    # Ряд 2: Оплатить подписку
    assert len(rows[1]) == 1
    btn_pay = rows[1][0]
    print(f"[CHECK] Главное меню Ряд 2: '{btn_pay.text}' -> callback_data={btn_pay.callback_data}")
    assert "Оплатить подписку" in btn_pay.text
    assert btn_pay.callback_data == "buy_subscription"

    # Ряд 3: Профиль, Условия, Поддержка
    assert len(rows[2]) == 3
    btn_prof, btn_terms, btn_supp = rows[2][0], rows[2][1], rows[2][2]
    print(f"[CHECK] Главное меню Ряд 3: '{btn_prof.text}', '{btn_terms.text}', '{btn_supp.text}'")
    assert "Профиль" in btn_prof.text
    assert btn_prof.callback_data == "bot_profile"
    assert "Условия" in btn_terms.text
    assert btn_terms.callback_data == "bot_terms"
    assert "Поддержка" in btn_supp.text
    expected_support_url = f"https://t.me/{bot.SUPPORT_BOT_USERNAME}" if bot.SUPPORT_BOT_USERNAME else "https://t.me/"
    assert btn_supp.url == expected_support_url, f"Неверный URL поддержки: {btn_supp.url}"

    # 3. Проверяем клавиатуру меню профиля get_profile_keyboard
    prof_kb = bot.get_profile_keyboard()
    prof_rows = prof_kb.inline_keyboard
    assert len(prof_rows) == 3, f"Ожидалось 3 ряда кнопок в меню профиля, получено {len(prof_rows)}"
    assert "Оплатить подписку (299 ₽)" in prof_rows[0][0].text
    assert prof_rows[0][0].callback_data == "buy_subscription"
    assert "Открыть Калькулятор" in prof_rows[1][0].text
    assert prof_rows[1][0].web_app.url == bot.WEBAPP_URL
    assert "Назад в меню" in prof_rows[2][0].text
    assert prof_rows[2][0].callback_data == "back_to_menu"
    print(f"[CHECK] Меню профиля: кнопки проверены корректно.")

    # 4. Проверяем клавиатуру меню условий get_terms_keyboard
    terms_kb = bot.get_terms_keyboard()
    terms_rows = terms_kb.inline_keyboard
    assert len(terms_rows) == 3, f"Ожидалось 3 ряда кнопок в меню условий, получено {len(terms_rows)}"
    btn_terms = terms_rows[0][0]
    assert "Пользовательское соглашение" in btn_terms.text
    assert btn_terms.url == bot.TELEGRAPH_TERMS_URL
    print(f"[CHECK] Меню условий: кнопка Пользовательское соглашение '{btn_terms.text}' -> {btn_terms.url}")

    btn_policy = terms_rows[1][0]
    assert "Политика конфиденциальности" in btn_policy.text
    assert btn_policy.url == bot.TELEGRAPH_POLICY_URL
    print(f"[CHECK] Меню условий: кнопка Политика конфиденциальности '{btn_policy.text}' -> {btn_policy.url}")

    btn_back = terms_rows[2][0]
    assert "Назад в меню" in btn_back.text
    assert btn_back.callback_data == "back_to_menu"
    print(f"[CHECK] Меню условий: кнопка «Назад в меню» проверена корректно.")

    print("\n✅ ТЕСТ 1 УСПЕШНО ПРОЙДЕН!\n")


async def test_supabase_start_scenarios():
    print("=" * 60)
    print("ТЕСТ 2: Проверка сценариев /start, удаления сообщений и актуального названия")
    print("=" * 60)

    test_user_id = 777888999
    mock_message = AsyncMock()
    mock_message.chat.id = test_user_id
    mock_message.from_user.id = test_user_id
    mock_message.from_user.first_name = "Алексей"
    mock_message.delete = AsyncMock()
    
    sent_mock_msg = MagicMock()
    sent_mock_msg.message_id = 12345
    mock_message.answer = AsyncMock(return_value=sent_mock_msg)
    
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
    original_delete_message = bot.bot.delete_message
    mock_delete_message = AsyncMock()
    bot.bot.delete_message = mock_delete_message
    bot.get_supabase = lambda: mock_supabase

    try:
        # --- СЦЕНАРИЙ 1: Новый пользователь (not data) + удаление предыдущего сообщения ---
        print("\n--- Сценарий 1: Новый пользователь (первый запуск) ---")
        mock_eq.execute.return_value = MagicMock(data=[])
        mock_table.insert.reset_mock()
        mock_message.delete.reset_mock()
        mock_delete_message.reset_mock()
        bot.last_menu_messages[test_user_id] = 998877

        await bot.command_start_handler(mock_message, mock_command)

        # Проверка удаления команды пользователя
        assert mock_message.delete.called, "Сообщение команды /start пользователя должно удаляться!"
        print("[CHECK] Удаление входящей команды /start выполнено.")

        # Проверка удаления предыдущего меню
        mock_delete_message.assert_called_with(chat_id=test_user_id, message_id=998877)
        print("[CHECK] Предыдущее меню бота (ID=998877) успешно удалено.")

        # Проверка обновления last_menu_messages
        assert bot.last_menu_messages.get(test_user_id) == 12345, "ID нового меню должен быть сохранен в last_menu_messages"
        print(f"[CHECK] Новый message_id сохранен: {bot.last_menu_messages.get(test_user_id)}")

        # Проверка вставки в Supabase
        assert mock_table.insert.called, "Метод insert НЕ был вызван для нового пользователя!"
        inserted_payload = mock_table.insert.call_args[0][0]
        print(f"[CHECK] Переданные данные в insert: {inserted_payload}")
        assert inserted_payload["telegram_id"] == test_user_id
        assert inserted_payload["trial_used"] is False

        call_args = mock_message.answer.call_args
        msg_text = call_args[0][0]
        print(f"[CHECK] Текст сообщения нового пользователя:\n{msg_text}")
        assert "Юридический помощник" in msg_text, "Сообщение должно содержать новое название сервиса"
        assert "Судебный & Исполнительный Помощник PRO" not in msg_text, "Старое название не должно присутствовать"
        assert "бесплатный пробный доступ на 5 дней" in msg_text, "Сообщение должно содержать начисление триала на 5 дней"

        # --- СЦЕНАРИЙ 2А: Повторный вход с активной подпиской ---
        print("\n--- Сценарий 2А: Повторный вход (подписка АКТИВНА) ---")
        mock_table.insert.reset_mock()
        mock_message.delete.reset_mock()
        future_date = datetime.now(timezone.utc) + timedelta(days=15)
        mock_eq.execute.return_value = MagicMock(data=[{
            "telegram_id": test_user_id,
            "subscription_until": future_date.isoformat(),
            "trial_used": True
        }])

        await bot.command_start_handler(mock_message, mock_command)

        assert mock_message.delete.called, "Команда /start должна удаляться и при повторном входе"
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
        bot.bot.delete_message = original_delete_message

    print("\n✅ ТЕСТ 2 УСПЕШНО ПРОЙДЕН!\n")


async def test_bot_callbacks():
    print("=" * 60)
    print("ТЕСТ 3: Проверка callback-обработчиков (In-place edit_text, bot_terms, back_to_menu)")
    print("=" * 60)

    test_user_id = 555666777
    mock_cb = AsyncMock()
    mock_cb.from_user.id = test_user_id
    mock_cb.from_user.first_name = "Елена"
    mock_cb.message = AsyncMock()
    mock_cb.message.chat.id = test_user_id
    mock_cb.message.message_id = 555111

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
        assert mock_cb.answer.called, "callback_query.answer() должен быть вызван"
        assert mock_cb.message.edit_text.called, "Должен использоваться edit_text для in-place обновления"
        
        call_args = mock_cb.message.edit_text.call_args
        profile_text = call_args[0][0]
        reply_markup = call_args[1].get("reply_markup")
        print(f"[CHECK] Ответ на callback bot_profile (edit_text):\n{profile_text}")
        assert str(test_user_id) in profile_text
        assert "Елена" in profile_text
        assert "🟢 Статус: <b>Подписка активна</b>" in profile_text
        assert "Подписка активна" in profile_text
        assert future_date.strftime("%d.%m.%Y %H:%M") in profile_text
        
        # Проверяем наличие кнопки «Назад в меню»
        back_btns = [btn for row in reply_markup.inline_keyboard for btn in row if btn.callback_data == "back_to_menu"]
        assert len(back_btns) == 1, "В меню профиля должна быть кнопка «Назад в меню»"
        print("[CHECK] Кнопка «« Назад в меню» присутствует в клавиатуре профиля.")

        # 1Б. Проверяем bot_profile для пользователя на пробном периоде (trial_used: False)
        mock_eq.execute.return_value = MagicMock(data=[{
            "telegram_id": test_user_id,
            "subscription_until": future_date.isoformat(),
            "trial_used": False
        }])
        await bot.process_profile_callback(mock_cb)
        trial_profile_text = mock_cb.message.edit_text.call_args[0][0]
        assert "🟡 Статус: <b>Пробный период</b>" in trial_profile_text
        assert "🟢 Статус: <b>Подписка активна</b>" not in trial_profile_text
        print("[CHECK] Меню профиля: для trial_used=False отображается 'Пробный период'.")

        # 1В. Проверяем команду /profile
        mock_cmd_msg = AsyncMock()
        mock_cmd_msg.from_user.id = test_user_id
        mock_cmd_msg.from_user.first_name = "Елена"
        mock_cmd_msg.chat.id = test_user_id
        mock_cmd_msg.answer = AsyncMock()
        mock_cmd_msg.delete = AsyncMock()
        await bot.command_profile_handler(mock_cmd_msg)
        assert mock_cmd_msg.answer.called
        cmd_profile_text = mock_cmd_msg.answer.call_args[0][0]
        assert "Пробный период" in cmd_profile_text
        print("[CHECK] Команда /profile работает корректно.")

        # 2. Проверяем bot_terms
        mock_cb.reset_mock()
        mock_cb.message.reset_mock()

        await bot.process_terms_callback(mock_cb)
        assert mock_cb.answer.called
        mock_cb.answer.assert_called_with("Вы открыли окно с политика/условия")
        print("[CHECK] callback_query.answer вызван со всплывающим уведомлением 'Вы открыли окно с политика/условия'.")

        assert mock_cb.message.edit_text.called, "bot_terms должен использовать edit_text"
        
        call_args = mock_cb.message.edit_text.call_args
        terms_text = call_args[0][0]
        terms_markup = call_args[1].get("reply_markup")
        print(f"\n[CHECK] Ответ на callback bot_terms:\n{terms_text[:200]}...")
        assert "Политика конфиденциальности и Пользовательское соглашение" in terms_text
        assert "Редакция от 15.09.2026 г." in terms_text
        assert "Юридический помощник" in terms_text
        assert "Instant View" in terms_text
        assert len(terms_text) < 4096, f"Текст условий превышает лимит Telegram: {len(terms_text)} символов"
        
        # Проверяем кнопки в меню условий
        assert len(terms_markup.inline_keyboard) == 3
        assert "Пользовательское соглашение" in terms_markup.inline_keyboard[0][0].text
        assert terms_markup.inline_keyboard[0][0].url == bot.TELEGRAPH_TERMS_URL
        assert "Политика конфиденциальности" in terms_markup.inline_keyboard[1][0].text
        assert terms_markup.inline_keyboard[1][0].url == bot.TELEGRAPH_POLICY_URL
        assert terms_markup.inline_keyboard[2][0].callback_data == "back_to_menu"
        print("[CHECK] Текст условий и кнопки Telegraph / Назад проверены успешно.")

        # 3. Проверяем back_to_menu
        mock_cb.reset_mock()
        mock_cb.message.reset_mock()

        await bot.process_back_to_menu_callback(mock_cb)
        assert mock_cb.answer.called
        assert mock_cb.message.edit_text.called, "back_to_menu должен использовать edit_text"
        
        call_args = mock_cb.message.edit_text.call_args
        menu_text = call_args[0][0]
        menu_markup = call_args[1].get("reply_markup")
        print(f"\n[CHECK] Ответ на callback back_to_menu:\n{menu_text[:150]}...")
        assert "С возвращением, Елена!" in menu_text
        assert "Ваша подписка активна" in menu_text
        # Проверяем что вернулась стартовая клавиатура (3 ряда кнопок)
        assert len(menu_markup.inline_keyboard) == 3, "Должна вернуться клавиатура главного меню"
        print("[CHECK] Возврат в главное меню через edit_text проверен успешно.")

        # 4. Проверяем безопасный перехват TelegramBadRequest при повторном клике
        mock_cb.reset_mock()
        mock_cb.message.reset_mock()
        mock_cb.message.edit_text.side_effect = bot.TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: message is not modified"
        )
        # Не должно вызывать исключений
        await bot.process_profile_callback(mock_cb)
        await bot.process_terms_callback(mock_cb)
        await bot.process_back_to_menu_callback(mock_cb)
        print("[CHECK] Ошибка TelegramBadRequest (message is not modified) перехвачена безопасно.")

    finally:
        bot.get_supabase = original_get_supabase

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

    # 3. Проверяем метаданные FastAPI
    print(f"[CHECK] main.app.title = '{main.app.title}'")
    assert "Юридический помощник" in main.app.title, f"Ожидалось название с 'Юридический помощник', получено: {main.app.title}"

    # 4. Проверяем GZipMiddleware и Cache-Control для статики
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Проверка корневого пути / с gzip
        res_gzip = await client.get("/", headers={"Accept-Encoding": "gzip"})
        assert res_gzip.status_code == 200
        assert res_gzip.headers.get("content-encoding") == "gzip", "Ответ должен быть сжат через gzip!"
        assert res_gzip.headers.get("cache-control") == "public, max-age=300", f"Ожидался cache-control 'public, max-age=300', получен '{res_gzip.headers.get('cache-control')}'"
        print("[CHECK] GET /: отдает Content-Encoding: gzip и Cache-Control: public, max-age=300")

        # Проверка /index.html
        res_index = await client.get("/index.html")
        assert res_index.status_code == 200
        assert res_index.headers.get("cache-control") == "public, max-age=300"
        print("[CHECK] GET /index.html: отдает Cache-Control: public, max-age=300")

    print("\n✅ ТЕСТ 5 УСПЕШНО ПРОЙДЕН!\n")


async def test_naming_and_legal_documents():
    print("=" * 60)
    print("ТЕСТ 6: Проверка переименования сервиса и отсутствия устаревших интеграций")
    print("=" * 60)

    # 1. Проверка bot.py
    with open("bot.py", "r", encoding="utf-8") as f:
        bot_content = f.read()
    assert "Судебный & Исполнительный Помощник PRO" not in bot_content, "Старое название найдено в bot.py!"
    assert "Юридический помощник" in bot_content, "Новое название не найдено в bot.py!"
    print("[CHECK] bot.py: старые названия удалены, актуальное название присутствует.")

    # 2. Проверка index.html
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    assert "Судебный & Исполнительный Помощник PRO" not in html_content, "Старое название найдено в index.html!"
    assert "Помощник PRO" not in html_content, "Старое сокращение Помощник PRO найдено в index.html!"
    assert "<title>Юридический помощник</title>" in html_content, "Тег title в index.html не обновлен!"
    assert "terms-btn" not in html_content, "Кнопка terms-btn должна быть удалена из index.html!"
    assert "terms-modal" not in html_content, "Модальное окно terms-modal должно быть удалено из index.html!"
    assert 'id="court-header-btn"' in html_content, "Кнопка court-header-btn не найдена в index.html!"
    assert "openCourtHeaderConstructor()" in html_content, "Функция openCourtHeaderConstructor не найдена в index.html!"
    assert "=== ЮРИДИЧЕСКИЙ ПОМОЩНИК: ОТЧЕТ ПО ГОСПОШЛИНЕ ===" in html_content, "Заголовок отчета по госпошлине не обновлен!"
    assert '<script src="https://telegram.org/js/telegram-web-app.js" defer></script>' in html_content, "Скрипт Telegram SDK должен содержать defer!"
    assert "window.Telegram?.WebApp?.ready?.()" in html_content, "Безопасный вызов Telegram.WebApp.ready?.() не найден в index.html!"
    assert "checkSubscriptionInBackground()" in html_content, "Вызов checkSubscriptionInBackground() не найден в index.html!"
    print("[CHECK] index.html: кнопка условий и модальное окно удалены, title, defer и неблокирующая инициализация проверены.")

    # 3. Проверка main.py
    with open("main.py", "r", encoding="utf-8") as f:
        main_content = f.read()
    assert "Судебный & Исполнительный Помощник PRO" not in main_content
    assert "Юридический помощник" in main_content, "Новое название не найдено в main.py!"
    print("[CHECK] main.py: метаданные FastAPI обновлены.")

    # 4. Проверка полного удаления Tome и Paymaster из рабочей кодовой базы
    code_files_to_check = ["bot.py", "main.py", ".env.example", "platega_service.py"]
    for filename in code_files_to_check:
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read().lower()
        assert "tome" not in content, f"Обнаружено упоминание 'tome' в {filename}!"
        assert "paymaster" not in content, f"Обнаружено упоминание 'paymaster' в {filename}!"
        assert "send_subscription_invoice" not in content, f"Обнаружена устаревшая функция send_subscription_invoice в {filename}!"
        assert "labeledprice" not in content, f"Обнаружен LabeledPrice в {filename}!"
    print("[CHECK] Кодовая база полностью очищена от следов Tome.ru и Paymaster.")

    print("\n✅ ТЕСТ 6 УСПЕШНО ПРОЙДЕН!\n")


async def test_platega_service_logic():
    print("=" * 60)
    print("ТЕСТ 7: Проверка сервиса Platega (проверка подписи и генерация ссылки)")
    print("=" * 60)

    # Используем строго фиктивные тестовые ключи (безопасность!)
    dummy_secret = "dummy_test_secret_key_0123456789abcdef"
    dummy_merchant = "dummy_test_merchant_uuid"
    dummy_api_key = "dummy_test_api_key_xyz"

    mock_env = {
        "PLATEGA_MERCHANT_ID": dummy_merchant,
        "PLATEGA_API_KEY": dummy_api_key,
        "PLATEGA_SECRET_KEY": dummy_secret,
        "PLATEGA_API_URL": "https://api.platega.io"
    }

    # 1. Проверка валидации цифровой подписи verify_platega_signature
    test_body = b'{"status":"CONFIRMED","amount":299,"order_id":"sub_123_456"}'
    
    with patch_env(mock_env):
        # 1A: Проверка валидной подписи в формате HEX
        import hmac, hashlib, base64
        valid_hex = hmac.new(dummy_secret.encode("utf-8"), test_body, hashlib.sha256).hexdigest()
        assert platega_service.verify_platega_signature(test_body, valid_hex) is True
        print("[CHECK] verify_platega_signature: валидный HEX принимается.")

        # 1Б: Проверка валидной подписи в формате Base64
        valid_b64 = base64.b64encode(hmac.new(dummy_secret.encode("utf-8"), test_body, hashlib.sha256).digest()).decode("utf-8")
        assert platega_service.verify_platega_signature(test_body, valid_b64) is True
        print("[CHECK] verify_platega_signature: валидный Base64 принимается.")

        # 1В: Проверка прямого совпадения секрета (X-Secret) и API-ключа
        assert platega_service.verify_platega_signature(test_body, dummy_secret) is True
        assert platega_service.verify_platega_signature(test_body, dummy_api_key) is True
        print("[CHECK] verify_platega_signature: прямой секрет X-Secret и API-ключ принимаются.")

        # 1Г: Проверка префикса sha256= и регистра HEX (верхний и нижний регистр)
        upper_hex = valid_hex.upper()
        assert platega_service.verify_platega_signature(test_body, upper_hex) is True
        assert platega_service.verify_platega_signature(test_body, f"sha256={valid_hex}") is True
        assert platega_service.verify_platega_signature(test_body, f"SHA256= {upper_hex} ") is True
        print("[CHECK] verify_platega_signature: префикс sha256= и регистр HEX обрабатываются корректно.")

        # 1Д: Проверка хэша от строкового параметра payload (если Platega подписывает конкретный параметр)
        payload_param = "1439183990"
        sig_from_payload = hmac.new(dummy_secret.encode("utf-8"), payload_param.encode("utf-8"), hashlib.sha256).hexdigest()
        body_with_payload_param = json.dumps({"payload": payload_param, "status": "PAID"}).encode("utf-8")
        assert platega_service.verify_platega_signature(body_with_payload_param, sig_from_payload) is True
        print("[CHECK] verify_platega_signature: хэш от параметра payload успешно проверен.")

        # 1Е: Проверка некорректной подписи (должна отвергаться)
        assert platega_service.verify_platega_signature(test_body, "fake_invalid_signature_hex") is False
        assert platega_service.verify_platega_signature(test_body, "") is False
        assert platega_service.verify_platega_signature(test_body, None) is False
        print("[CHECK] verify_platega_signature: неверная подпись отклоняется.")

        # 1Ж: Проверка валидации через параметр secret_header (X-Secret)
        assert platega_service.verify_platega_signature(test_body, signature_header="", secret_header=dummy_secret) is True
        assert platega_service.verify_platega_signature(test_body, signature_header=None, secret_header=dummy_api_key) is True
        assert platega_service.verify_platega_signature(test_body, signature_header="invalid_sig", secret_header=dummy_secret) is True
        assert platega_service.verify_platega_signature(test_body, signature_header="invalid_sig", secret_header="wrong_secret") is False
        print("[CHECK] verify_platega_signature: secret_header проверен успешно.")

    # 2. Проверка генерации ссылки create_platega_payment
    mock_post_response = MagicMock()
    mock_post_response.status_code = 200
    mock_post_response.json.return_value = {
        "redirect": "https://pay.platega.io/checkout/test_session_12345"
    }

    with patch_env(mock_env), \
         patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_post_response)) as mock_post:
        payment_url = await platega_service.create_platega_payment(telegram_id=999888, amount=299.0, days=30)
        assert payment_url == "https://pay.platega.io/checkout/test_session_12345"
        
        # Проверяем переданные заголовки и тело
        call_kwargs = mock_post.call_args[1]
        req_json = call_kwargs.get("json", {})
        req_headers = call_kwargs.get("headers", {})
        
        assert req_headers.get("X-MerchantId") == dummy_merchant
        assert req_headers.get("X-Secret") == dummy_api_key
        assert req_json.get("amount") in (299, 299.0)
        assert req_json.get("currency") == "RUB"
        assert req_json.get("orderId", "").startswith("sub_999888_")
        assert "paymentMethod" not in req_json or req_json.get("paymentMethod") is None
        assert req_json.get("paymentDetails") == {"amount": 299, "currency": "RUB"}
        print("[CHECK] create_platega_payment: запрос сформирован по универсальной спецификации (без жесткой привязки к СБП) и ссылка получена.")

    print("\n✅ ТЕСТ 7 УСПЕШНО ПРОЙДЕН!\n")


async def test_platega_fastapi_endpoints():
    print("=" * 60)
    print("ТЕСТ 8: Проверка эндпоинтов FastAPI (/api/create-payment и /api/webhook/platega)")
    print("=" * 60)

    dummy_secret = "dummy_test_secret_for_webhook_validation"
    mock_env = {
        "PLATEGA_MERCHANT_ID": "dummy_merchant",
        "PLATEGA_API_KEY": "dummy_key",
        "PLATEGA_SECRET_KEY": dummy_secret,
        "PLATEGA_API_URL": "https://api.platega.io"
    }

    # 1. Тестирование эндпоинта /api/create-payment и /health
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1-0: Проверка health-check
        res_health = await client.get("/health")
        assert res_health.status_code == 200
        assert res_health.json() == {"status": "ok"}
        print("[CHECK] GET /health: эндпоинт отдает 200 {'status': 'ok'}.")

        # 1А: Запрос без заголовка initData -> 401
        res_no_auth = await client.post("/api/create-payment")
        assert res_no_auth.status_code == 401, f"Ожидался 401, получен {res_no_auth.status_code}"
        print("[CHECK] /api/create-payment: запрос без X-Telegram-Init-Data возвращает 401.")

        # 1Б: Запрос с недействительной сессией -> 401
        with patch("main.verify_telegram_init_data", return_value=None):
            res_invalid_auth = await client.post(
                "/api/create-payment",
                headers={"X-Telegram-Init-Data": "invalid_session_data"}
            )
            assert res_invalid_auth.status_code == 401
            assert res_invalid_auth.json().get("detail") == "Недействительная сессия"
            print("[CHECK] /api/create-payment: недействительная сессия возвращает 401.")

        # 1В: Запрос с валидным initData -> 200 и payment_url
        with patch("main.verify_telegram_init_data", return_value={"id": 777666, "first_name": "Иван"}), \
             patch("main.create_platega_payment", AsyncMock(return_value="https://pay.platega.io/test-order")):
            res_auth = await client.post(
                "/api/create-payment",
                headers={"X-Telegram-Init-Data": "dummy_valid_init_data"}
            )
            assert res_auth.status_code == 200
            json_res = res_auth.json()
            assert json_res.get("payment_url") == "https://pay.platega.io/test-order"
            print("[CHECK] /api/create-payment: успешное создание счета и возврат payment_url.")

        # 1Г: Внутренняя ошибка при создании счета -> 500 Внутренняя ошибка сервера
        with patch("main.verify_telegram_init_data", return_value={"id": 777666, "first_name": "Иван"}), \
             patch("main.create_platega_payment", AsyncMock(side_effect=RuntimeError("Connection timeout"))):
            res_gateway_err = await client.post(
                "/api/create-payment",
                headers={"X-Telegram-Init-Data": "dummy_valid_init_data"}
            )
            assert res_gateway_err.status_code == 500
            assert res_gateway_err.json().get("detail") == "Внутренняя ошибка сервера"
            print("[CHECK] /api/create-payment: ошибка шлюза возвращает 500 'Внутренняя ошибка сервера' без раскрытия трейса.")

    # 2. Тестирование эндпоинта /api/webhook/platega
    import hmac, hashlib
    
    # Мок клиента Supabase и бота Telegram
    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_sub_select = MagicMock()
    mock_sub_eq = MagicMock()
    mock_sub_upsert = MagicMock()

    mock_table.select.return_value = mock_sub_select
    mock_sub_select.eq.return_value = mock_sub_eq
    mock_sub_eq.execute.return_value = MagicMock(data=[])
    mock_table.upsert.return_value = mock_sub_upsert
    mock_sub_upsert.execute.return_value = MagicMock(data=[{}])

    mock_pay_table = MagicMock()
    mock_pay_select = MagicMock()
    mock_pay_eq = MagicMock()
    mock_pay_insert = MagicMock()

    mock_pay_table.select.return_value = mock_pay_select
    mock_pay_select.eq.return_value = mock_pay_eq
    mock_pay_eq.execute.return_value = MagicMock(data=[])
    mock_pay_table.insert.return_value = mock_pay_insert
    mock_pay_insert.execute.return_value = MagicMock(data=[{}])

    def table_router(name):
        if name == "payments":
            return mock_pay_table
        return mock_table

    mock_supabase.table.side_effect = table_router
    main.PROCESSED_PAYMENTS.clear()

    with patch_env(mock_env), \
         patch("main.get_supabase", return_value=mock_supabase), \
         patch.object(main.bot, "send_message", AsyncMock()) as mock_bot_send:

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 2-Неверная подпись: строгий отказ 403 Invalid signature
            res_bad_sig = await client.post(
                "/api/webhook/platega",
                json={"status": "CONFIRMED", "payload": "1439183990"},
                headers={"X-Signature": "invalid_wrong_signature"}
            )
            assert res_bad_sig.status_code == 403
            assert res_bad_sig.json().get("detail") == "Invalid signature"
            print("[CHECK] /api/webhook/platega: неверная подпись отклоняется со статусом 403.")

            # 2А: Пропуск неактуального статуса (например, PENDING или FAILED)
            pending_payload = {"status": "PENDING", "payload": "1439183990"}
            res_pending = await client.post(
                "/api/webhook/platega",
                json=pending_payload,
                headers={"X-Secret": dummy_secret}
            )
            assert res_pending.status_code == 200
            assert res_pending.json().get("status") == "ignored"
            print("[CHECK] /api/webhook/platega: статус PENDING корректно пропущен (ignored).")

            # 2Б: Строковый payload: "1439183990" (проблема 1) + статус CONFIRMED + заголовок X-Signature
            webhook_payload_str = {
                "status": "CONFIRMED",
                "payload": "1439183990"
            }
            raw_body_str = json.dumps(webhook_payload_str).encode("utf-8")
            valid_sig_str = hmac.new(dummy_secret.encode("utf-8"), raw_body_str, hashlib.sha256).hexdigest()

            mock_table.upsert.reset_mock()
            mock_bot_send.reset_mock()

            res_valid = await client.post(
                "/api/webhook/platega",
                content=raw_body_str,
                headers={"X-Signature": valid_sig_str, "Content-Type": "application/json"}
            )
            assert res_valid.status_code == 200
            assert res_valid.json() == {"status": "ok"}
            assert mock_table.upsert.called, "Запись подписки должна быть обновлена/вставлена через upsert!"
            upsert_record = mock_table.upsert.call_args[0][0]
            assert upsert_record.get("telegram_id") == 1439183990
            assert upsert_record.get("trial_used") is True
            assert mock_bot_send.called, "Бот должен отправить уведомление об успешной оплате!"
            call_text = mock_bot_send.call_args[1].get("text", "")
            assert "Оплата успешно получена" in call_text
            assert "30 дней" in call_text
            print("[CHECK] /api/webhook/platega: строковый payload '1439183990' успешно обработан, подписка начислена.")

            # 2В: Вложенные данные data.payload и data.status = "PAID" (проблема 1 и 2) + заголовок Signature
            nested_payload = {
                "data": {
                    "payload": "1439183990",
                    "status": "PAID"
                }
            }
            raw_nested = json.dumps(nested_payload).encode("utf-8")
            valid_sig_nested = hmac.new(dummy_secret.encode("utf-8"), raw_nested, hashlib.sha256).hexdigest()

            res_nested = await client.post(
                "/api/webhook/platega",
                content=raw_nested,
                headers={"Signature": valid_sig_nested, "Content-Type": "application/json"}
            )
            assert res_nested.status_code == 200
            assert res_nested.json() == {"status": "ok"}
            print("[CHECK] /api/webhook/platega: вложенные data.payload и data.status=PAID успешно распознаны.")

            # 2Г: Статус COMPLETED и заголовок X-Secret (проблема 2 и 3)
            completed_payload = {
                "status": "COMPLETED",
                "payload": "1439183990"
            }
            res_completed = await client.post(
                "/api/webhook/platega",
                json=completed_payload,
                headers={"X-Secret": dummy_secret}
            )
            assert res_completed.status_code == 200
            assert res_completed.json() == {"status": "ok"}
            print("[CHECK] /api/webhook/platega: статус COMPLETED и авторизация через X-Secret успешно обработаны.")

            # 2Д: Подпись передана прямо в JSON-теле (проблема 3)
            body_sig_payload = {
                "status": "SUCCESS",
                "payload": "1439183990",
                "signature": dummy_secret
            }
            res_body_sig = await client.post(
                "/api/webhook/platega",
                json=body_sig_payload
            )
            assert res_body_sig.status_code == 200
            assert res_body_sig.json() == {"status": "ok"}
            print("[CHECK] /api/webhook/platega: подпись внутри JSON тела успешно принята.")

            # 2Е: Fallback извлечения telegram_id из orderId ("sub_998877_1790000000")
            order_payload = {
                "status": "SUCCESS",
                "orderId": "sub_998877_1790000000"
            }
            mock_table.upsert.reset_mock()
            res_order = await client.post(
                "/api/webhook/platega",
                json=order_payload,
                headers={"X-Secret": dummy_secret}
            )
            assert res_order.status_code == 200
            assert res_order.json() == {"status": "ok"}
            assert mock_table.upsert.call_args[0][0].get("telegram_id") == 998877
            print("[CHECK] /api/webhook/platega: fallback извлечение telegram_id из orderId работает корректно.")

            # 2Ж: Если telegram_id не найден ни в одном поле -> {"status": "error", "message": "telegram_id not found"}
            no_id_payload = {"status": "CONFIRMED"}
            res_no_id = await client.post(
                "/api/webhook/platega",
                json=no_id_payload,
                headers={"X-Secret": dummy_secret}
            )
            assert res_no_id.status_code == 200
            assert res_no_id.json() == {"status": "error", "message": "telegram_id not found"}
            print("[CHECK] /api/webhook/platega: отсутствие telegram_id возвращает ошибку с описанием.")

            # 2З: Каскадный поиск статуса (transaction.status и payment.status)
            trans_status_payload = {
                "transaction": {"status": "PAID"},
                "payload": "1439183990"
            }
            res_trans_status = await client.post(
                "/api/webhook/platega",
                json=trans_status_payload,
                headers={"X-Secret": dummy_secret}
            )
            assert res_trans_status.status_code == 200
            assert res_trans_status.json() == {"status": "ok"}

            payment_status_payload = {
                "payment": {"status": "COMPLETED"},
                "payload": "1439183990"
            }
            res_pay_status = await client.post(
                "/api/webhook/platega",
                json=payment_status_payload,
                headers={"X-Secret": dummy_secret}
            )
            assert res_pay_status.status_code == 200
            assert res_pay_status.json() == {"status": "ok"}
            print("[CHECK] /api/webhook/platega: каскадный поиск статуса (transaction/payment) работает безошибочно.")

            # 2И: Надежный парсинг даты из Supabase с пробелом вместо T и смещением часового пояса
            mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
                data=[{"subscription_until": "2026-10-15 12:00:00+00:00"}]
            )
            mock_table.upsert.reset_mock()
            res_date_test = await client.post(
                "/api/webhook/platega",
                json={"status": "CONFIRMED", "payload": "1439183990"},
                headers={"X-Secret": dummy_secret}
            )
            assert res_date_test.status_code == 200
            assert res_date_test.json() == {"status": "ok"}
            assert mock_table.upsert.called
            new_until_str = mock_table.upsert.call_args[0][0]["subscription_until"]
            assert "2026-11-14" in new_until_str
            print("[CHECK] /api/webhook/platega: надежный парсинг даты подписки со смешанным форматом (пробел вместо T) работает корректно.")

            # 2К: Защита от повторных начислений (Идемпотентность)
            mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            idem_tx_id = "platega_tx_test_idempotent_12345"
            idem_payload = {
                "id": idem_tx_id,
                "status": "PAID",
                "payload": "1439183990"
            }
            # Первичное начисление
            res_idem_1 = await client.post(
                "/api/webhook/platega",
                json=idem_payload,
                headers={"X-Secret": dummy_secret}
            )
            assert res_idem_1.status_code == 200
            assert res_idem_1.json() == {"status": "ok"}

            # Повторный запрос с тем же transaction_id -> Already processed
            mock_table.upsert.reset_mock()
            res_idem_2 = await client.post(
                "/api/webhook/platega",
                json=idem_payload,
                headers={"X-Secret": dummy_secret}
            )
            assert res_idem_2.status_code == 200
            assert res_idem_2.json().get("message") == "Already processed"
            assert not mock_table.upsert.called, "Повторный вебхук не должен повторно продлевать подписку в БД!"
            print("[CHECK] /api/webhook/platega: защита от повторных начислений (идемпотентность) подтверждена.")

    # 3. Тестирование эндпоинта /api/check-subscription
    with patch_env(mock_env), \
         patch("main.get_supabase", return_value=mock_supabase):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 3А: Новый пользователь -> trial_used: False, status_text: 'Пробный период', plan_type: 'trial'
            mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            mock_table.insert.reset_mock()
            with patch("main.verify_telegram_init_data", return_value={"id": 111222, "first_name": "User"}):
                res_sub_new = await client.get(
                    "/api/check-subscription",
                    headers={"X-Telegram-Init-Data": "valid_init_data"}
                )
                assert res_sub_new.status_code == 200
                data_new = res_sub_new.json()
                assert data_new["active"] is True
                assert data_new["trial_used"] is False
                assert data_new["status_text"] == "Пробный период"
                assert data_new["plan_type"] == "trial"
                assert mock_table.insert.called
                assert mock_table.insert.call_args[0][0]["trial_used"] is False
                print("[CHECK] /api/check-subscription: новый пользователь получает status_text='Пробный период', plan_type='trial'.")

            # 3Б: Оплаченный пользователь (active=True, trial_used=True) -> status_text: 'Активна', plan_type: 'paid'
            future_sub = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
            mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[{
                "subscription_until": future_sub,
                "trial_used": True
            }])
            with patch("main.verify_telegram_init_data", return_value={"id": 111222, "first_name": "User"}):
                res_sub_paid = await client.get(
                    "/api/check-subscription",
                    headers={"X-Telegram-Init-Data": "valid_init_data"}
                )
                assert res_sub_paid.status_code == 200
                data_paid = res_sub_paid.json()
                assert data_paid["active"] is True
                assert data_paid["trial_used"] is True
                assert data_paid["status_text"] == "Активна"
                assert data_paid["plan_type"] == "paid"
                print("[CHECK] /api/check-subscription: оплаченный пользователь получает status_text='Активна', plan_type='paid'.")

            # 3В: Пользователь на триале (active=True, trial_used=False) -> status_text: 'Пробный период', plan_type: 'trial'
            mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[{
                "subscription_until": future_sub,
                "trial_used": False
            }])
            with patch("main.verify_telegram_init_data", return_value={"id": 111222, "first_name": "User"}):
                res_sub_trial = await client.get(
                    "/api/check-subscription",
                    headers={"X-Telegram-Init-Data": "valid_init_data"}
                )
                assert res_sub_trial.status_code == 200
                data_trial = res_sub_trial.json()
                assert data_trial["active"] is True
                assert data_trial["trial_used"] is False
                assert data_trial["status_text"] == "Пробный период"
                assert data_trial["plan_type"] == "trial"
                print("[CHECK] /api/check-subscription: пользователь на триале получает status_text='Пробный период', plan_type='trial'.")

            # 3Г: Истекшая подписка -> status_text: 'Истекла', plan_type: 'none', active: False
            past_sub = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[{
                "subscription_until": past_sub,
                "trial_used": True
            }])
            with patch("main.verify_telegram_init_data", return_value={"id": 111222, "first_name": "User"}):
                res_sub_exp = await client.get(
                    "/api/check-subscription",
                    headers={"X-Telegram-Init-Data": "valid_init_data"}
                )
                assert res_sub_exp.status_code == 200
                data_exp = res_sub_exp.json()
                assert data_exp["active"] is False
                assert data_exp["status_text"] == "Истекла"
                assert data_exp["plan_type"] == "none"
                print("[CHECK] /api/check-subscription: истекшая подписка получает status_text='Истекла', plan_type='none'.")

            # 3Д: Внутренняя ошибка сервера -> 500 'Внутренняя ошибка сервера'
            mock_table.select.return_value.eq.return_value.execute.side_effect = RuntimeError("DB connection error")
            with patch("main.verify_telegram_init_data", return_value={"id": 111222, "first_name": "User"}):
                res_sub_err = await client.get(
                    "/api/check-subscription",
                    headers={"X-Telegram-Init-Data": "valid_init_data"}
                )
                assert res_sub_err.status_code == 500
                assert res_sub_err.json().get("detail") == "Внутренняя ошибка сервера"
                print("[CHECK] /api/check-subscription: внутренняя ошибка возвращает 500 'Внутренняя ошибка сервера'.")

    print("\n✅ ТЕСТ 8 УСПЕШНО ПРОЙДЕН!\n")


async def main_test_suite():
    await test_webapp_and_keyboard()
    await test_supabase_start_scenarios()
    await test_bot_callbacks()
    await test_support_bot_functionality()
    await test_main_config_and_api()
    await test_naming_and_legal_documents()
    await test_platega_service_logic()
    await test_platega_fastapi_endpoints()
    print("=" * 60)
    print("🎉 ВСЕ 8 ТЕСТОВЫХ НАБОРОВ УСПЕШНО ПРОЙДЕНЫ!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main_test_suite())
