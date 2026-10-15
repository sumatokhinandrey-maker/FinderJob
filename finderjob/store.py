"""Хранилище профилей — Upstash Redis по HTTP.

Redis выбран не ради скорости, а ради того, что к нему можно ходить обычным
HTTP-запросом. Значит, и вебхук на Vercel, и ежедневный прогон в GitHub Actions
читают одни и те же данные, не таща за собой ни одной зависимости.
"""

from __future__ import annotations

import json
import time
import os
import urllib.error
import urllib.request

# Upstash через маркетплейс Vercel кладёт переменные под именами KV_*,
# при ручной установке — под UPSTASH_*. Принимаем оба.
def _clean_url(value: str | None) -> str | None:
    """Значение приезжает из секретов гитхаба и переменных Vercel, где легко
    оставить кавычки или потерять схему при копировании. Чиним молча —
    падать на этом посреди рассылки незачем."""
    if not value:
        return None
    value = value.strip().strip("\"'").rstrip("/")
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


def _credentials() -> tuple[str | None, str]:
    """Читаем доступы в момент обращения, а не при импорте: локально .env
    подхватывается уже после того, как модуль загружен."""
    url = _clean_url(
        os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    )
    token = (
        os.environ.get("KV_REST_API_TOKEN")
        or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        or ""
    ).strip().strip("\"'")
    return url, token

USERS_KEY = "finderjob:users"
POOL_KEY = "finderjob:pool"
SENT_KEY = "finderjob:sent"


class StoreError(RuntimeError):
    pass


TIMEOUT_SEC = 10
TRIES = 3


def _command(*args: object) -> object:
    URL, TOKEN = _credentials()
    if not URL or not TOKEN:
        raise StoreError(
            "Нет доступа к хранилищу: задай KV_REST_API_URL и KV_REST_API_TOKEN"
        )
    if ".upstash.io" not in URL:
        # Самая частая поломка при настройке — адрес и токен вставлены
        # не в свои поля. Говорим об этом прямо, не показывая значений.
        raise StoreError(
            "KV_REST_API_URL не похож на адрес хранилища: ожидается что-то "
            "вида https://xxx.upstash.io. Проверь, не перепутаны ли местами "
            "адрес и токен."
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
    # Upstash отвечает не всегда с первого раза: рукопожатие TLS иногда висит
    # до таймаута. Без повторов одна такая заминка роняла рассылку целиком —
    # и не в начале, а на середине списка подписчиков. Все наши команды
    # (GET, SET, SMEMBERS, SADD) идемпотентны, повторять их безопасно.
    body = None
    for attempt in range(1, TRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
                body = json.loads(response.read())
            break
        except urllib.error.HTTPError as exc:
            # 5xx — сторона хранилища, её стоит переспросить. 4xx это наша
            # ошибка (токен, синтаксис) — повторять бессмысленно.
            if exc.code >= 500 and attempt < TRIES:
                time.sleep(attempt)
                continue
            raise StoreError(
                f"Хранилище вернуло {exc.code}: {exc.read().decode('utf-8', 'replace')}"
            ) from exc
        except OSError as exc:
            # URLError, обрыв соединения и таймаут рукопожатия — все они OSError.
            if attempt < TRIES:
                time.sleep(attempt)
                continue
            raise StoreError(
                f"Хранилище не ответило за {TRIES} попытки: {exc}"
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


def save_pool(rows: list[dict], collected_at: str) -> None:
    """Последний собранный список вакансий. Нужен, чтобы бот мог показать
    подборку сразу после мастера: сам он обойти hirehi не успеет —
    это минуты, а у функции секунды."""
    _command(
        "SET",
        POOL_KEY,
        json.dumps({"collected_at": collected_at, "rows": rows}, ensure_ascii=False),
    )


def load_pool() -> tuple[list[dict], str | None]:
    raw = _command("GET", POOL_KEY)
    if not raw:
        return [], None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [], None
    return data.get("rows") or [], data.get("collected_at")


def save_digest(chat_id: int | str, pages: list[str]) -> None:
    """Страницы последнего дайджеста: по ним кнопки «Далее» и «Назад»
    перерисовывают сообщение. Живут неделю — к следующему дайджесту старые
    страницы не нужны, а место в хранилище бесплатным не бывает."""
    if len(pages) <= 1:
        return
    _command(
        "SET", f"finderjob:digest:{chat_id}",
        json.dumps(pages, ensure_ascii=False), "EX", 7 * 24 * 3600,
    )


def load_digest(chat_id: int | str) -> list[str]:
    raw = _command("GET", f"finderjob:digest:{chat_id}")
    if not raw:
        return []
    try:
        pages = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return pages if isinstance(pages, list) else []


def mark_sent(day: str) -> None:
    """Отметка «за этот день рассылка состоялась». По ней догоняющие запуски
    понимают, что делать нечего, и выходят молча."""
    _command("SET", SENT_KEY, day)


def last_sent() -> str | None:
    """Дата последней состоявшейся рассылки (YYYY-MM-DD) или None."""
    value = _command("GET", SENT_KEY)
    return str(value) if value else None


def all_users() -> list[dict]:
    ids = _command("SMEMBERS", USERS_KEY) or []
    users = []
    for chat_id in ids:
        user = load_user(chat_id)
        if user:
            users.append(user)
    return users
