#!/usr/bin/env python3
"""JobResearch — ежедневный дайджест новых вакансий для UX/UI и продуктовых дизайнеров.

Запуск:
    python3 main.py --dry-run      посмотреть результат, ничего не отправляя
    python3 main.py --limit 30     обойти только 30 свежих страниц (быстрая проверка)
    python3 main.py                собрать и отправить в Telegram

Токен и чат берутся из переменных окружения TELEGRAM_TOKEN и TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import core
import telegram
from sources import hirehi

ROOT = Path(__file__).parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Дайджест вакансий для дизайнеров")
    parser.add_argument(
        "--dry-run", action="store_true", help="не отправлять и не запоминать"
    )
    parser.add_argument("--limit", type=int, help="ограничить число страниц")
    parser.add_argument("--days", type=int, help="за сколько дней смотреть")
    parser.add_argument("--json", action="store_true", help="выгрузить результат в JSON")
    args = parser.parse_args()

    config = json.loads((ROOT / "config.json").read_text("utf-8"))
    profile = config["profile"]
    days = args.days or config.get("lookback_days", 2)
    since = (date.today() - timedelta(days=days)).isoformat()

    print(f"Собираю вакансии с {since}…")
    vacancies = hirehi.collect(since, limit=args.limit)
    print(f"Найдено на hirehi: {len(vacancies)}")

    passed, rejected = core.apply_filters(vacancies, profile)
    print(f"Прошло фильтр: {len(passed)}")

    seen = core.load_seen()
    fresh = core.only_new(passed, seen)
    print(f"Из них новых (не показывали раньше): {len(fresh)}")

    if args.json:
        (ROOT / "last_run.json").write_text(core.to_json(fresh), "utf-8")
        print("Записано в last_run.json")

    digest = core.build_digest(fresh, rejected, scanned=len(vacancies))
    print("\n" + "─" * 60)
    print(digest.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    print("─" * 60 + "\n")

    if args.dry_run:
        print("dry-run: ничего не отправлено, память не тронута")
        return 0

    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(
            "Нет TELEGRAM_TOKEN или TELEGRAM_CHAT_ID в окружении.\n"
            "Запусти с --dry-run либо задай переменные — см. README."
        )
        return 1

    telegram.send(token, chat_id, digest)
    print("Отправлено в Telegram")

    today = date.today().isoformat()
    for vacancy in fresh:
        seen[vacancy.id] = today
    core.save_seen(seen)
    print(f"Запомнено вакансий: {len(seen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
