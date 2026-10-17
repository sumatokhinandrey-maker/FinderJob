"""Проверки расписанной отправки без Telegram и живого Redis."""

import unittest
from unittest.mock import patch

import main
from api.send_digest import handler
from finderjob import daily, store


class MemoryRedis:
    def __init__(self):
        self.values = {}

    def command(self, *args):
        op = args[0]
        if op == "GET":
            return self.values.get(args[1])
        if op == "SET":
            if "NX" in args and args[1] in self.values:
                return None
            self.values[args[1]] = args[2]
            return "OK"
        if op == "EVAL":
            key, claim = args[3:5]
            if self.values.get(key) != claim:
                return None
            if "'DEL'" in args[1]:
                del self.values[key]
            else:
                self.values[key] = "sent"
            return "OK"
        raise AssertionError(args)


class DailyTests(unittest.TestCase):
    def test_endpoint_rejects_unauthorized_request(self):
        request = handler.__new__(handler)
        request.headers = {"Authorization": "Bearer wrong"}
        with patch.dict("os.environ", {"DIGEST_SEND_SECRET": "right"}), \
             patch.object(request, "_reply") as reply, \
             patch.object(daily, "send_prepared") as send:
            request.do_POST()
            self.assertEqual(reply.call_args.args[0], 401)
            send.assert_not_called()

    def test_prepare_saves_pool_without_sending(self):
        user = {"chat_id": 1, "profile": {"roles": ["design.product"]}}
        with patch("sys.argv", ["main.py", "--prepare"]), \
             patch.object(main, "load_env"), \
             patch.object(store, "all_users", return_value=[user]), \
             patch.object(main.taxonomy, "plan", return_value={"hirehi": ["design.product"]}), \
             patch.object(main.hirehi, "collect", return_value=[object()]), \
             patch.object(main.core, "fill_grades", side_effect=lambda rows: rows), \
             patch.object(main.core, "dedupe", side_effect=lambda rows: rows), \
             patch.object(main.core, "to_rows", return_value=[{"id": "x"}]), \
             patch.object(store, "save_pool") as save_pool, \
             patch.object(daily.tg, "send") as send:
            self.assertEqual(main.main(), 0)
            save_pool.assert_called_once()
            send.assert_not_called()

    def test_old_pool_never_sends(self):
        with patch.object(store, "load_pool", return_value=([{"id": "x"}], "2000-01-01")), \
             patch.object(daily.tg, "send") as send:
            with self.assertRaisesRegex(RuntimeError, "Нет подготовленных"):
                daily.send_prepared()
            send.assert_not_called()

    def test_retry_sends_only_unfinished_user(self):
        redis = MemoryRedis()
        day = daily.moscow_day()
        users = [{"chat_id": 1}, {"chat_id": 2}]
        calls = []

        def deliver(user, _vacancies, _cap, claim, track):
            self.assertFalse(track)
            chat_id = user["chat_id"]
            calls.append(chat_id)
            if chat_id == 2 and calls.count(2) == 1:
                raise RuntimeError("Telegram unavailable")
            store.finish_delivery(chat_id, day, claim, True)
            return 3

        with patch.object(store, "_command", side_effect=redis.command), \
             patch.object(store, "load_pool", return_value=([{}], day)), \
             patch.object(store, "all_users", return_value=users), \
             patch.object(daily.core, "from_rows", return_value=[object()]), \
             patch.object(daily.core, "fill_grades", side_effect=lambda rows: rows), \
             patch.object(daily, "deliver", side_effect=deliver):
            self.assertEqual(daily.send_prepared(), (1, 3))
            self.assertEqual(daily.send_prepared(), (1, 3))
            self.assertEqual(calls, [1, 2, 2])

    def test_news_invitation_is_after_digest_and_only_once(self):
        redis = MemoryRedis()
        users = [{"chat_id": 1}, {"chat_id": 2}]
        day = daily.NEWS_DAY
        redis.values[f"{store.DELIVERY_KEY}:{day}:1"] = "sent"
        with patch.object(store, "_command", side_effect=redis.command), \
             patch.object(daily.tg, "send") as send:
            daily.send_news_invitation(users, day)
            daily.send_news_invitation(users, day)
            daily.send_news_invitation(users, "2026-10-19")
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], 1)
        self.assertIn('emoji-id="5206607081334906820"', send.call_args.args[1])
        button = send.call_args.args[2][0][0]
        self.assertEqual(button["text"], "FinderJob")
        self.assertEqual(button["url"], "https://t.me/finderjobupdate")
        self.assertEqual(button["icon_custom_emoji_id"], "5875465628285931233")


if __name__ == "__main__":
    unittest.main()
