"""Разговор с Telegram Bot API. Только stdlib."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram режет сообщения длиннее 4096 символов.
LIMIT = 4000


def token() -> str:
    value = os.environ.get("TELEGRAM_TOKEN")
    if not value:
        raise RuntimeError("Нет TELEGRAM_TOKEN в окружении")
    return value


def call(method: str, **params: object) -> dict:
    payload = json.dumps(params, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        API.format(token=token(), method=method),
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Telegram вернул {exc.code}: {exc.read().decode('utf-8', 'replace')}"
        ) from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram отказал: {result}")
    return result["result"]


def chunks(text: str) -> list[str]:
    if len(text) <= LIMIT:
        return [text]
    parts, current = [], ""
    for block in text.split("\n\n"):
        if len(current) + len(block) + 2 > LIMIT:
            parts.append(current.rstrip())
            current = ""
        current += block + "\n\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts


def send(chat_id: int | str, text: str, keyboard: list | None = None) -> int | None:
    """Длинное сообщение уходит частями, кнопки вешаем на последнюю —
    иначе они уедут в середину простыни. Возвращаем id последней части:
    её потом правит мастер настройки."""
    parts = chunks(text)
    message_id = None
    for index, part in enumerate(parts):
        params: dict = {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard is not None and index == len(parts) - 1:
            params["reply_markup"] = {"inline_keyboard": keyboard}
        message_id = call("sendMessage", **params).get("message_id")
    return message_id


def edit(chat_id: int | str, message_id: int, text: str, keyboard: list | None) -> None:
    """Правим сообщение на месте — так мастер настройки не засоряет чат
    десятком одинаковых карточек."""
    params: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard is not None:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        call("editMessageText", **params)
    except RuntimeError as exc:
        # «message is not modified» — человек нажал ту же кнопку дважды.
        # Это не ошибка, молчим.
        if "not modified" not in str(exc):
            raise


def answer_callback(callback_id: str, text: str = "") -> None:
    """Гасим часики на кнопке. Без этого Telegram крутит их несколько секунд."""
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except RuntimeError:
        pass
