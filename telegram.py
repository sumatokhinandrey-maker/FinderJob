"""Отправка в Telegram. Только stdlib — библиотека ради одного POST не нужна."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/sendMessage"

# Telegram режет сообщения длиннее 4096 символов.
LIMIT = 4000


def _chunks(text: str) -> list[str]:
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


def send(token: str, chat_id: str, text: str) -> None:
    for part in _chunks(text):
        payload = json.dumps(
            {
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            API.format(token=token),
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                result = json.loads(resp.read())
            if not result.get("ok"):
                raise RuntimeError(f"Telegram отказал: {result}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Telegram вернул {exc.code}: {exc.read().decode('utf-8', 'replace')}"
            ) from exc
