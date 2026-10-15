#!/usr/bin/env python3
"""Медленный сбор LinkedIn в общий запас.

Зачем отдельно от рассылки. LinkedIn терпит редкие запросы и затыкается
на частых: в общем прогоне мы ловили 429 уже на втором слове. Поэтому он
собирается сам по себе несколько раз в сутки, по чуть-чуть, и складывает
найденное в хранилище. Рассылка потом просто берёт накопленное.

Второе следствие: за сутки набегает заметно больше вакансий, чем успел бы
один прогон, — ровно то, ради чего источник и держим.

Каждый запуск берёт свою часть стран по кругу, чтобы за день обойти все
и нигде не частить. Запуск: python3 linkedin_sync.py [--slot N].
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from finderjob import profile as prof, store, taxonomy  # noqa: E402
from sources import linkedin  # noqa: E402

# По две страны за запуск: четыре запуска в сутки обходят весь список
# дважды, и ни один прогон не делает больше горстки запросов.
PLACES_PER_RUN = 2
LOOKBACK_DAYS = 14


def main() -> int:
    parser = argparse.ArgumentParser(description="Сбор LinkedIn в запас")
    parser.add_argument("--slot", type=int, default=0,
                        help="номер запуска за сутки: сдвигает список стран")
    parser.add_argument("--dry-run", action="store_true",
                        help="не писать в хранилище")
    args = parser.parse_args()

    from main import load_env
    load_env()

    users = store.all_users()
    if not users:
        print("Подписчиков нет — собирать незачем.")
        return 0

    wanted = sorted({role for user in users for role in prof.roles_of(user["profile"])})
    routes = taxonomy.plan(wanted).get("linkedin") or []
    if not routes:
        print("По этим профессиям у linkedin маршрутов нет.")
        return 0

    names = list(linkedin.GEO)
    start = (args.slot * PLACES_PER_RUN) % len(names)
    chosen = [names[(start + i) % len(names)] for i in range(PLACES_PER_RUN)]
    print(f"Запуск {args.slot}: страны {', '.join(chosen)}")
    print(f"Запросы: {', '.join(routes[:linkedin.MAX_QUERIES])}")

    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    found = linkedin.fetch(
        since, routes=routes, geo_ids=[linkedin.GEO[name] for name in chosen]
    )
    print(f"Собрано: {len(found)}")
    if not found:
        # Пустой сбор — повод посмотреть лог, а не молча считать день удачным.
        return 1

    russian = sum(1 for v in found if core.CYRILLIC_RE.search(v.title))
    print(f"Из них с русским заголовком: {russian}")

    if args.dry_run:
        for vacancy in found[:10]:
            print(f"  {vacancy.posted_at} | {vacancy.title[:44]:44} | "
                  f"{vacancy.company[:20]:20} | {vacancy.city or ''}")
        return 0

    added = store.merge_linkedin(core.to_rows(found))
    print(f"Новых в запасе: {added}, всего в запасе: {len(store.load_linkedin())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
