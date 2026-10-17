"""Доступ к фильтрам без системного меню команд Telegram."""

import unittest
from unittest.mock import patch

import core
import setup_bot
from api import telegram
from finderjob import wizard


class NavigationTests(unittest.TestCase):
    def test_filters_keyboard_stays_available(self):
        keyboard = wizard.menu_keyboard()
        self.assertEqual(keyboard["keyboard"], [[{"text": "Фильтры"}]])
        self.assertTrue(keyboard["is_persistent"])
        self.assertFalse(keyboard["one_time_keyboard"])

    def test_every_digest_has_filters_button(self):
        self.assertEqual(
            core.digest_keyboard(0, 1),
            [[{"text": "⚙️ Фильтры", "callback_data": "filters:open"}]],
        )
        keyboard = core.digest_keyboard(0, 2)
        self.assertEqual(keyboard[0][0]["callback_data"], "dg:1")
        self.assertEqual(keyboard[-1][0]["callback_data"], "filters:open")

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
