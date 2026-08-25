"""Хранилище профилей — Upstash Redis по HTTP.

Redis выбран не ради скорости, а ради того, что к нему можно ходить обычным
HTTP-запросом. Значит, и вебхук на Vercel, и ежедневный прогон в GitHub Actions
читают одни и те же данные, не таща за собой ни одной зависимости.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# Upstash через маркетплейс Vercel кладёт переменные под именами KV_*,
# при ручной установке — под UPSTASH_*. Принимаем оба.
URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get(
    "UPSTASH_REDIS_REST_TOKEN"
)

USERS_KEY = "finderjob:users"


class StoreError(RuntimeError):
    pass


def _command(*args: object) -> object:
    if not URL or not TOKEN:
        raise StoreError(
            "Нет доступа к хранилищу: задай KV_REST_API_URL и KV_REST_API_TOKEN"
        )
    payload = json.dumps([str(a) for a in args]).encode("utf-8")
    request = urllib.request.Request(
        URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise StoreError(
            f"Хранилище вернуло {exc.code}: {exc.read().decode('utf-8', 'replace')}"
        ) from exc
    if "error" in body:
        raise StoreError(body["error"])
    return body.get("result")


def _key(chat_id: int | str) -> str:
    return f"finderjob:user:{chat_id}"


def load_user(chat_id: int | str) -> dict | None:
    raw = _command("GET", _key(chat_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def save_user(user: dict) -> None:
    chat_id = user["chat_id"]
    _command("SET", _key(chat_id), json.dumps(user, ensure_ascii=False))
    _command("SADD", USERS_KEY, chat_id)


def forget_user(chat_id: int | str) -> None:
    """Отписка. Профиль стираем целиком — хранить настройки человека,
    который ушёл, незачем."""
    _command("DEL", _key(chat_id))
    _command("SREM", USERS_KEY, chat_id)


def all_users() -> list[dict]:
    ids = _command("SMEMBERS", USERS_KEY) or []
    users = []
    for chat_id in ids:
        user = load_user(chat_id)
        if user:
            users.append(user)
    return users
