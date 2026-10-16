"""Разговор с Telegram Bot API. Только stdlib."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram режет сообщения длиннее 4096 символов.
LIMIT = 4000


def _plain_custom_emoji(text: str) -> str:
    return re.sub(r'<tg-emoji emoji-id="[^"]+">(.*?)</tg-emoji>', r"\1", text)


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


def send(chat_id: int | str, text: str, keyboard: list | dict | None = None) -> int | None:
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
            params["reply_markup"] = (
                keyboard if isinstance(keyboard, dict) else {"inline_keyboard": keyboard}
            )
        try:
            message_id = call("sendMessage", **params).get("message_id")
        except RuntimeError:
            if "<tg-emoji " not in part:
                raise
            params["text"] = _plain_custom_emoji(part)
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
        if "not modified" not in str(exc) and "<tg-emoji " in text:
            params["text"] = _plain_custom_emoji(text)
            call("editMessageText", **params)
            return
        # «message is not modified» — человек нажал ту же кнопку дважды.
        # Это не ошибка, молчим.
        if "not modified" not in str(exc):
            raise


def delete(chat_id: int | str, message_id: int) -> None:
    """Убираем сообщение из чата. Удалить чужое сообщение бот вправе только
    в личной переписке и только пока ему меньше 48 часов, а ещё человек мог
    стереть его сам — поэтому отказ Telegram здесь не ошибка."""
    try:
        call("deleteMessage", chat_id=chat_id, message_id=message_id)
    except RuntimeError:
        pass


def answer_callback(callback_id: str, text: str = "") -> None:
    """Гасим часики на кнопке. Без этого Telegram крутит их несколько секунд."""
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except RuntimeError:
        pass
