#!/usr/bin/env python3
"""Ежедневный дайджест FinderJob.

Один обход hirehi на всех: вакансии собираются один раз, а дальше каждому
подписчику применяются его собственные фильтры и его собственная память
о показанном.

Запуск:
    python3 main.py --dry-run      посмотреть результат, ничего не отправляя
    python3 main.py --limit 30     обойти только 30 свежих страниц
    python3 main.py                собрать и разослать

Токен берётся из TELEGRAM_TOKEN, хранилище — из KV_REST_API_URL
и KV_REST_API_TOKEN. Локально всё это можно положить в .env.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from finderjob import profile as prof  # noqa: E402
from finderjob import store, tg  # noqa: E402
from sources import hirehi  # noqa: E402


def load_env() -> None:
    """Подтягиваем .env, если он есть. Файл в .gitignore и нужен только для
    локальных запусков — в GitHub Actions те же имена приходят из секретов.
    Уже заданные переменные окружения не перетираем."""
    import os

    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def deliver(user: dict, vacancies: list, dry_run: bool) -> int:
    """Готовим и отправляем дайджест одному человеку. Возвращаем,
    сколько вакансий ему ушло."""
    filters = prof.to_filters(user["profile"])
    passed, rejected = core.apply_filters(vacancies, filters)

    seen = user.get("seen") or {}
    fresh = core.only_new(passed, seen)

    digest = core.build_digest(fresh, rejected, scanned=len(vacancies))
    label = user.get("username") or user["chat_id"]
    print(f"\n--- {label}: подошло {len(passed)}, из них новых {len(fresh)}")
    print(core.to_plain(digest))

    if dry_run:
        return len(fresh)

    tg.send(user["chat_id"], digest)

    today = date.today().isoformat()
    for vacancy in fresh:
        seen[vacancy.id] = today
    user["seen"] = core.trim_seen(seen)
    store.save_user(user)
    return len(fresh)


def main() -> int:
    parser = argparse.ArgumentParser(description="Дайджест вакансий для дизайнеров")
    parser.add_argument(
        "--dry-run", action="store_true", help="не отправлять и не запоминать"
    )
    parser.add_argument("--limit", type=int, help="ограничить число страниц")
    parser.add_argument("--days", type=int, help="за сколько дней смотреть")
    parser.add_argument("--json", action="store_true", help="выгрузить сбор в JSON")
    args = parser.parse_args()

    load_env()

    users = store.all_users()
    print(f"Подписчиков: {len(users)}")
    if not users:
        print("Никого нет — обходить hirehi незачем.")
        return 0

    config = json.loads((ROOT / "config.json").read_text("utf-8"))
    days = args.days or config.get("lookback_days", 2)
    since = (date.today() - timedelta(days=days)).isoformat()

    print(f"Собираю вакансии с {since}…")
    vacancies = hirehi.collect(since, limit=args.limit)
    print(f"Найдено на hirehi: {len(vacancies)}")

    if args.json:
        (ROOT / "last_run.json").write_text(core.to_json(vacancies), "utf-8")
        print("Записано в last_run.json")

    if not args.dry_run and vacancies:
        # Кладём собранное в хранилище: из этого запаса бот показывает
        # подборку новому подписчику сразу после мастера настройки.
        store.save_pool(core.to_rows(vacancies), date.today().isoformat())
        print("Запас вакансий обновлён")

    sent = 0
    for user in users:
        try:
            sent += deliver(user, vacancies, args.dry_run)
        except Exception as exc:
            # Один упавший подписчик не должен ронять рассылку остальным.
            # Чаще всего это «bot was blocked by the user».
            print(f"  ! не доставлено {user.get('chat_id')}: {exc}")

    print(f"\nВсего отправлено вакансий: {sent}")
    if args.dry_run:
        print("dry-run: ничего не отправлено, память не тронута")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
