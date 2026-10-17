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
from copy import deepcopy
from datetime import date
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from finderjob import profile as prof  # noqa: E402
from finderjob import daily, stats, store, tg, wizard  # noqa: E402

STOPPED = (
    "Отписал. Дайджест больше не придёт, критерии стёр.\n\n"
    "Если передумаешь — нажми «Фильтры», настроим заново."
)


def first_digest(user: dict) -> None:
    """Подборка сразу после мастера — из запаса, собранного последней рассылкой.
    Показанное отмечаем, чтобы утром эти же вакансии не пришли повторно."""
    rows, collected_at = store.load_pool()
    if not rows:
        tg.send(
            user["chat_id"],
            wizard.EMPTY_POOL,
            wizard.menu_keyboard(),
        )
        user["menu_installed"] = wizard.MENU_VERSION
        store.save_user(user)
        return

    vacancies = core.fill_grades(core.from_rows(rows))
    passed, _ = core.apply_filters(vacancies, prof.to_filters(user["profile"]))
    seen = user.get("seen") or {}
    fresh = core.only_new(passed, seen)

    pages = core.build_pages(fresh)
    store.save_digest(user["chat_id"], pages)
    tg.send(user["chat_id"], pages[0], core.digest_keyboard(0, len(pages)))
    if collected_at == daily.moscow_day():
        store.mark_delivery_sent(user["chat_id"], collected_at)
    stats.track("digest_sent")
    stats.track("vacancies_sent", len(fresh))

    today = date.today().isoformat()
    for vacancy in fresh:
        seen[vacancy.id] = today
    user["seen"] = seen
    store.save_user(user)


def _open_step(user: dict, step: str, mode: str) -> None:
    """Показать шаг новой карточкой и запомнить её id: следующие нажатия
    правят её на месте, а не плодят копии."""
    user["awaiting"] = None
    user["mode"] = mode
    user["step"] = step
    body, keyboard = wizard.render(step, user["profile"], mode)
    user["card"] = tg.send(user["chat_id"], body, keyboard)
    store.save_user(user)


def _typed(user: dict, text: str) -> None:
    """Текстом больше не отвечают ни на один шаг: зарплата выбирается
    кнопками-порогами, город заменён регионом. Кто-то мог застать старый
    шаг открытым — просто возвращаем карточку мастера."""
    step = user["awaiting"]
    user["awaiting"] = None
    store.save_user(user)
    body, keyboard = wizard.render(step, user["profile"], user.get("mode", "setup"))
    _show(user, body, keyboard)


def _show(user: dict, body: str, keyboard: list) -> None:
    """Обновить карточку мастера на месте, а если её нет — завести новую."""
    card = user.get("card")
    if card:
        tg.edit(user["chat_id"], card, body, keyboard)
    else:
        user["card"] = tg.send(user["chat_id"], body, keyboard)
        store.save_user(user)


def _user(chat: dict) -> dict:
    existing = store.load_user(chat["id"])
    if existing:
        # Старые настроенные профили хранили пустые роли как UX/UI + веб.
        # Делаем этот фактический выбор явным, чтобы галочки совпадали с поиском.
        configured = existing.get("setup_complete") or (
            existing.get("mode") != "setup" and (existing.get("profile") or {}).get("grades")
        )
        if configured and not existing["profile"].get("roles"):
            existing["profile"]["roles"] = list(prof.DEFAULT_ROLES)
            directions = prof.directions_of(existing["profile"])
            if "design" not in directions:
                directions.append("design")
            existing["profile"]["directions"] = directions
        return existing
    return prof.new_user(
        chat["id"], chat.get("username"), date.today().isoformat()
    )


def _configured(user: dict) -> bool:
    return bool(user.get("setup_complete") or (user.get("profile") or {}).get("grades"))


def _show_filters(user: dict, chat_id: int) -> None:
    user.pop("draft_profile", None)
    user["awaiting"] = None
    user["mode"] = "edit"
    user["step"] = None
    body, keyboard = wizard.render_settings(
        user["profile"], user.get("pending_first_search", False)
    )
    tg.send(chat_id, body, keyboard)
    store.save_user(user)


def handle_message(message: dict) -> None:
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()
    if not chat.get("id"):
        return

    # Команда или кнопка меню своё дело сделали, в переписке оставлять их
    # не нужно: ответ бота показывает результат. Убираем до разбора.
    if (text.startswith("/") or text in wizard.MENU_BUTTON_TEXTS) and message.get("message_id"):
        tg.delete(chat["id"], message["message_id"])

    if text.startswith("/stop"):
        store.forget_user(chat["id"])
        stats.track("stop")
        tg.send(chat["id"], STOPPED)
        return

    user = _user(chat)

    if text.startswith("/start"):
        stats.track("start")
        if _configured(user):
            _show_filters(user, chat["id"])
        elif user.get("step"):
            _open_step(user, user["step"], "setup")
        else:
            tg.send(chat["id"], wizard.WELCOME, wizard.menu_keyboard())
            user["menu_installed"] = wizard.MENU_VERSION
            store.save_user(user)
        return

    if text in (*wizard.MENU_BUTTON_TEXTS, "/filters", "/settings"):
        if not _configured(user):
            _open_step(user, user.get("step") or "direction", "setup")
            return
        stats.track("settings")
        _show_filters(user, chat["id"])
        return

    if user.get("awaiting") and not text.startswith("/"):
        _typed(user, text)
        return

    if text.startswith("/stats") and str(chat["id"]) == os.environ.get("ADMIN_CHAT_ID"):
        tg.send(chat["id"], stats.report())
        return

    tg.send(chat["id"], wizard.HELP, wizard.menu_keyboard())
    user["menu_installed"] = wizard.MENU_VERSION
    store.save_user(user)


def _turn_page(chat_id: int, message_id: int | None, where: str) -> None:
    """Перелистнуть дайджест. Страницы лежат в хранилище неделю; если их уже
    нет — молчим, перерисовывать нечего, а сообщение остаётся как было."""
    if not message_id or where == "here":
        return
    try:
        index = int(where)
    except ValueError:
        return

    pages = store.load_digest(chat_id)
    if not pages or not 0 <= index < len(pages):
        return
    tg.edit(chat_id, message_id, pages[index],
            core.digest_keyboard(index, len(pages)))


def handle_callback(callback: dict) -> None:
    data = callback.get("data") or ""
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    if not chat.get("id"):
        tg.answer_callback(callback["id"])
        return

    # Листание дайджеста идёт в обход мастера: ни профиль, ни карточка
    # настроек тут ни при чём, правится только само сообщение с подборкой.
    if data.startswith("dg:"):
        _turn_page(chat["id"], message.get("message_id"), data[3:])
        tg.answer_callback(callback["id"])
        return

    if data == "filters:open":
        user = _user(chat)
        if _configured(user):
            _show_filters(user, chat["id"])
        else:
            _open_step(user, user.get("step") or "direction", "setup")
        tg.answer_callback(callback["id"])
        return

    user = _user(chat)
    profile = prof.migrate(user.get("draft_profile") or user["profile"])
    message_id = message.get("message_id")

    if data == "filters:close":
        user.pop("draft_profile", None)
        user["card"] = None
        user["step"] = None
        user["awaiting"] = None
        store.save_user(user)
        if message_id:
            tg.delete(chat["id"], message_id)
        tg.answer_callback(callback["id"])
        return

    finished = False
    edited = False
    notice = ""

    if data == "onb:start":
        user.pop("draft_profile", None)
        user["profile"] = profile = prof.blank()
        user["mode"] = "setup"
        user["setup_complete"] = False
        user["step"] = "direction"
        body, keyboard = wizard.render("direction", profile, "setup")

    elif data == "search:find":
        user.pop("draft_profile", None)
        user["mode"] = "edit"
        user["step"] = None
        if user.get("setup_complete") and not user.get("pending_first_search"):
            body, keyboard = wizard.render_settings(user["profile"])
        else:
            user["setup_complete"] = True
            user["pending_first_search"] = False
            body, keyboard = "Ищу вакансии…", []
            finished = True
            stats.track("wizard_done")

    elif data == "search:edit":
        user.pop("draft_profile", None)
        user["mode"] = "edit"
        user["step"] = None
        body, keyboard = wizard.render_settings(
            profile, user.get("pending_first_search", False)
        )

    elif data == "search:review":
        user.pop("draft_profile", None)
        profile = user["profile"]
        user["step"] = None
        if user.get("pending_first_search"):
            user["mode"] = "review"
            body, keyboard = wizard.render_review(profile)
        else:
            user["mode"] = "edit"
            body, keyboard = wizard.render_settings(profile)

    elif data == "ed:menu":
        user.pop("draft_profile", None)
        profile = user["profile"]
        user["mode"] = "edit"
        user["step"] = None
        body, keyboard = wizard.render_settings(
            profile, user.get("pending_first_search", False)
        )

    elif data == "ed:more":
        user["draft_profile"] = profile = deepcopy(user["profile"])
        user["mode"] = "edit"
        user["step"] = "sources"
        body, keyboard = wizard.render_more(profile)

    elif data == "ed:formatregion":
        user["mode"] = "edit"
        user["step"] = None
        body, keyboard = wizard.render_format_region()

    elif data in ("reset:ask", "go"):
        body, keyboard = wizard.render_reset()

    elif data == "reset:cancel":
        user["mode"] = "edit"
        user["step"] = None
        body, keyboard = wizard.render_settings(
            profile, user.get("pending_first_search", False)
        )

    elif data == "reset:confirm":
        user.pop("draft_profile", None)
        user["profile"] = profile = prof.blank()
        user["setup_complete"] = False
        user["pending_first_search"] = False
        user["mode"] = "setup"
        user["step"] = "direction"
        body, keyboard = wizard.render("direction", profile, "setup")

    elif data.startswith("ed:"):
        user["draft_profile"] = profile = deepcopy(user["profile"])
        user["mode"] = "edit"
        user["step"] = data[3:]
        if user["step"] == "more":
            user["step"] = "sources"
            body, keyboard = wizard.render_more(profile)
        elif user["step"] == "sources":
            body, keyboard = wizard.render("sources", profile, "edit")
        else:
            body, keyboard = wizard.render(user["step"], profile, "edit")

    elif data.startswith("ca:"):
        user["awaiting"] = None
        body, keyboard = wizard.render(data[3:], profile, user.get("mode", "setup"))

    elif data.startswith("bk:"):
        # В режиме правки «Назад» уводит в список настроек, а не на шаг раньше:
        # человек пришёл поменять один пункт, а не проходить мастер заново.
        if user.get("mode") == "edit":
            user.pop("draft_profile", None)
            profile = user["profile"]
            user["step"] = None
            body, keyboard = wizard.render_settings(
                profile, user.get("pending_first_search", False)
            )
        else:
            earlier = wizard.prev_step(data[3:], profile) or "direction"
            user["step"] = earlier
            body, keyboard = wizard.render(earlier, profile, "setup")

    elif data.startswith("nx:") or data.startswith("sk:"):
        wizard.apply(data, {"profile": profile})
        step = data[3:]
        user["awaiting"] = None
        if user.get("mode") == "edit":
            if step == "direction":
                user["step"] = "role"
                body, keyboard = wizard.render("role", profile, "edit")
            elif user.get("pending_first_search"):
                user["profile"] = profile
                user.pop("draft_profile", None)
                user["mode"] = "review"
                user["step"] = None
                body, keyboard = wizard.render_review(profile)
            else:
                user["profile"] = profile
                user.pop("draft_profile", None)
                user["step"] = None
                edited = True
                body, keyboard = wizard.render_settings(profile)
        else:
            following = wizard.next_step(step, profile)
            user["step"] = following
            if following:
                body, keyboard = wizard.render(following, profile, "setup")
            else:
                body, keyboard = wizard.render_review(profile)
                user["pending_first_search"] = True
                user["mode"] = "review"

    else:
        if user.get("mode") == "edit" and user.get("draft_profile"):
            profile = user["draft_profile"]
        notice = wizard.apply(data, {"profile": profile})
        step = user.get("step") or "direction"
        body, keyboard = wizard.render(step, profile, user.get("mode", "setup"))

    if message_id:
        user["card"] = message_id
    store.save_user(user)
    if message_id:
        tg.edit(chat["id"], message_id, body, keyboard)
    else:
        tg.send(chat["id"], body, keyboard)
    tg.answer_callback(callback["id"], notice)

    if finished:
        first_digest(user)
    elif edited:
        # Правка критерия молча — это правка вслепую: человек не знает,
        # что она дала. Показываем подборку по новым критериям сразу,
        # из того же запаса, что и после мастера.
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
