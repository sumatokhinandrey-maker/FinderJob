"""Доступ к фильтрам без системного меню команд Telegram."""

import unittest
from unittest.mock import patch

import core
import setup_bot
from api import telegram
from finderjob import icons, profile, tg, wizard


class NavigationTests(unittest.TestCase):
    def test_filters_keyboard_stays_available(self):
        keyboard = wizard.menu_keyboard()
        self.assertEqual(keyboard["keyboard"], [[{"text": "Фильтры"}]])
        self.assertTrue(keyboard["is_persistent"])
        self.assertFalse(keyboard["one_time_keyboard"])

    def test_digest_only_shows_page_navigation(self):
        self.assertEqual(core.digest_keyboard(0, 1), [])
        keyboard = core.digest_keyboard(0, 2)
        self.assertEqual(keyboard[0][0]["callback_data"], "dg:1")
        self.assertFalse(any(button["callback_data"] == "filters:open"
                             for row in keyboard for button in row))

    def test_filters_card_and_custom_button_icons(self):
        chosen = profile.blank()
        chosen.update({
            "roles": ["design.product_designer", "design.product"],
            "grades": ["intern", "middle"],
            "salary_min": 150_000,
            "formats": ["remote", "hybrid"],
            "sources": ["hh"],
        })
        text, keyboard = wizard.render_settings(chosen)
        self.assertEqual(text, (
            "<b>Фильтры</b>\n\n"
            "• Продуктовый дизайнер, UX/UI-дизайнер\n"
            "⚡️ Грейд: стажёр, middle\n"
            "💰 Вилка: от 150 000 ₽\n"
            "🏠 Формат: удалёнка, гибрид\n"
            "📍 Регион: любой\n"
            "🔎 Источники: HH.RU\n\nЧто изменить?"
        ))
        labels = ["Профессия", "Грейд", "Вилка", "Формат", "Регион", "Источники"]
        keys = ["profession", "grade", "salary", "format", "region", "sources"]
        for button, label, key in zip((b for row in keyboard[:3] for b in row), labels, keys):
            self.assertEqual(button["text"], label)
            self.assertEqual(button["icon_custom_emoji_id"], icons.FILTER_ICONS[key][1])

    def test_sources_have_no_select_all_button(self):
        chosen = profile.blank()
        keyboard = wizard.keyboard("sources", chosen, "edit")
        self.assertFalse(any(button["callback_data"] == "sk:sources"
                             for row in keyboard for button in row))
        chosen["sources"] = ["hh"]
        notice = wizard.apply("tsrc:hh", {"profile": chosen})
        self.assertEqual(notice, "Оставь включённым хотя бы один источник.")
        self.assertEqual(chosen["sources"], ["hh"])
        notice = wizard.apply("tsrcgrp:hh", {"profile": chosen})
        self.assertEqual(notice, "Оставь включённой хотя бы одну группу источников.")
        self.assertEqual(chosen["sources"], ["hh"])
        self.assertIn("🔎 Источники: все", profile.summary(profile.blank()))

    def test_single_page_digest_has_no_empty_inline_markup(self):
        with patch.object(tg, "call", return_value={"message_id": 1}) as call:
            tg.send(42, "Дайджест", core.digest_keyboard(0, 1))
        self.assertNotIn("reply_markup", call.call_args.kwargs)

    def test_custom_button_icons_fall_back_without_sending_live_message(self):
        keyboard = wizard.settings_keyboard()
        with patch.object(tg, "call", side_effect=[RuntimeError("custom emoji unavailable"),
                                                   {"message_id": 1}]) as call:
            self.assertEqual(tg.send(42, "Фильтры", keyboard), 1)
        first = call.call_args_list[0].kwargs["reply_markup"]["inline_keyboard"][0][0]
        second = call.call_args_list[1].kwargs["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(first["icon_custom_emoji_id"], icons.FILTER_ICONS["profession"][1])
        self.assertEqual(second["text"], "💼 Профессия")
        self.assertNotIn("icon_custom_emoji_id", second)

    def test_custom_button_icons_fall_back_when_editing_card(self):
        keyboard = wizard.settings_keyboard()
        with patch.object(tg, "call", side_effect=[RuntimeError("custom emoji unavailable"),
                                                   True]) as call:
            tg.edit(42, 1, "Фильтры", keyboard)
        second = call.call_args_list[1].kwargs["reply_markup"]["inline_keyboard"][1][0]
        self.assertEqual(second["text"], "🪙 Вилка")
        self.assertNotIn("icon_custom_emoji_id", second)

    def test_digest_button_opens_current_filters(self):
        callback = {
            "id": "callback",
            "data": "filters:open",
            "message": {"chat": {"id": 42}, "message_id": 1},
        }
        user = {"chat_id": 42, "profile": {}, "setup_complete": True}
        with patch.object(telegram, "_user", return_value=user), \
             patch.object(telegram, "_show_filters") as show, \
             patch.object(telegram.tg, "answer_callback") as answer:
            telegram.handle_callback(callback)
            show.assert_called_once_with(user, 42)
            answer.assert_called_once_with("callback")

    def test_setup_removes_commands_without_messages(self):
        with patch.object(setup_bot, "load_env"), \
             patch.object(setup_bot.store, "all_users", return_value=[{"chat_id": 42}]), \
             patch.object(setup_bot.tg, "call", return_value={"description": "bot"}) as call, \
             patch.object(setup_bot.tg, "send") as send, \
             patch.object(setup_bot.time, "sleep"):
            self.assertEqual(setup_bot.main(), 0)
            self.assertIn("deleteMyCommands", [c.args[0] for c in call.call_args_list])
            self.assertNotIn("setMyCommands", [c.args[0] for c in call.call_args_list])
            call.assert_any_call("setChatMenuButton", menu_button={"type": "default"})
            call.assert_any_call("deleteMyCommands", scope={"type": "chat", "chat_id": 42})
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
