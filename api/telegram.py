"""Вебхук Telegram. Сюда прилетает каждое сообщение и каждое нажатие кнопки.

Живёт на Vercel как отдельная функция: просыпается на запрос, отвечает
за доли секунды и засыпает. Ежедневный обход вакансий сюда не помещается —
он занимает минуты и остаётся в GitHub Actions.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import date
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from finderjob import profile as prof  # noqa: E402
from finderjob import store, tg, wizard  # noqa: E402

STOPPED = (
    "Отписал. Дайджест больше не придёт, критерии стёр.\n\n"
    "Если передумаешь — /start, настроим заново."
)


def first_digest(user: dict) -> None:
    """Подборка сразу после мастера — из запаса, собранного последней рассылкой.
    Показанное отмечаем, чтобы утром эти же вакансии не пришли повторно."""
    rows, collected_at = store.load_pool()
    if not rows:
        tg.send(user["chat_id"], wizard.EMPTY_POOL)
        return

    vacancies = core.from_rows(rows)
    passed, rejected = core.apply_filters(vacancies, prof.to_filters(user["profile"]))
    seen = user.get("seen") or {}
    fresh = core.only_new(passed, seen)

    tg.send(user["chat_id"], core.build_digest(fresh, rejected, len(vacancies)))

    today = date.today().isoformat()
    for vacancy in fresh:
        seen[vacancy.id] = today
    user["seen"] = seen
    store.save_user(user)


def _user(chat: dict) -> dict:
    existing = store.load_user(chat["id"])
    if existing:
        return existing
    return prof.new_user(
        chat["id"], chat.get("username"), date.today().isoformat()
    )


def handle_message(message: dict) -> None:
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()
    if not chat.get("id"):
        return

    if text.startswith("/stop"):
        store.forget_user(chat["id"])
        tg.send(chat["id"], STOPPED)
        return

    user = _user(chat)

    if text.startswith("/start"):
        user["mode"] = "setup"
        user["step"] = "grade"
        store.save_user(user)
        body, keyboard = wizard.render("grade", user["profile"], "setup")
        tg.send(chat["id"], wizard.START + "\n\n" + body, keyboard)
        return

    if text.startswith("/settings"):
        store.save_user(user)
        body, keyboard = wizard.render_settings(user["profile"])
        tg.send(chat["id"], body, keyboard)
        return

    tg.send(chat["id"], wizard.HELP)


def handle_callback(callback: dict) -> None:
    data = callback.get("data") or ""
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    if not chat.get("id"):
        tg.answer_callback(callback["id"])
        return

    user = _user(chat)
    profile = user["profile"]
    message_id = message.get("message_id")
    finished = False

    if data == "go":
        user["mode"] = "setup"
        user["step"] = "grade"
        body, keyboard = wizard.render("grade", profile, "setup")

    elif data == "ed:menu":
        user["mode"] = "edit"
        user["step"] = None
        body, keyboard = wizard.render_settings(profile)

    elif data.startswith("ed:"):
        user["mode"] = "edit"
        user["step"] = data[3:]
        body, keyboard = wizard.render(user["step"], profile, "edit")

    elif data.startswith("nx:") or data.startswith("sl:"):
        # Зарплата — выбор из одного варианта, поэтому после неё сразу дальше,
        # лишнее нажатие «Дальше» тут ничего не решает.
        wizard.apply(data, user)
        step = data.split(":")[1] if data.startswith("nx:") else "salary"
        if user.get("mode") == "edit":
            user["step"] = None
            body, keyboard = wizard.render_settings(profile)
        else:
            following = wizard.next_step(step, profile)
            user["step"] = following
            if following:
                body, keyboard = wizard.render(following, profile, "setup")
            else:
                body = prof.summary(profile) + "\n\n" + wizard.DONE
                keyboard = wizard.done_keyboard()
                finished = True

    else:
        wizard.apply(data, user)
        step = user.get("step") or "grade"
        body, keyboard = wizard.render(step, profile, user.get("mode", "setup"))

    store.save_user(user)
    if message_id:
        tg.edit(chat["id"], message_id, body, keyboard)
    else:
        tg.send(chat["id"], body, keyboard)
    tg.answer_callback(callback["id"])

    if finished:
        first_digest(user)


def process(update: dict) -> None:
    if "callback_query" in update:
        handle_callback(update["callback_query"])
    elif "message" in update:
        handle_message(update["message"])


class handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: str = "ok") -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self) -> None:
        self._reply(200, "FinderJob webhook")

    def do_POST(self) -> None:
        secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
        if secret and self.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
            self._reply(403, "forbidden")
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            update = json.loads(raw)
        except json.JSONDecodeError:
            self._reply(400, "bad json")
            return

        try:
            process(update)
        except Exception:
            # Отвечаем 200 при любой поломке: на ошибку Telegram шлёт тот же
            # апдейт снова и снова, и бот залипает в цикле повторов.
            traceback.print_exc()

        self._reply(200)
