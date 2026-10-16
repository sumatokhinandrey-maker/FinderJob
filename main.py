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
from finderjob import stats, store, taxonomy, tg  # noqa: E402
from sources import (ats, careerspace, companies, designer, finder,  # noqa: E402
                     friendwork, getmatch, habr, hh, hirehi, linkedin,
                     telegram)


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


def deliver(user: dict, vacancies: list, dry_run: bool, cap: int = 0) -> int:
    """Готовим и отправляем дайджест одному человеку. Возвращаем,
    сколько вакансий ему ушло."""
    prof.migrate(user["profile"])
    filters = prof.to_filters(user["profile"])
    passed, rejected = core.apply_filters(vacancies, filters)

    seen = user.get("seen") or {}
    fresh = core.only_new(passed, seen)

    # Окно в месяц на первом прогоне даёт человеку сотни вакансий разом —
    # это не подборка, а простыня на полтора десятка сообщений. Отдаём самое
    # свежее, остальное не помечаем показанным: оно придёт следующими днями.
    tail = 0
    if cap and len(fresh) > cap:
        fresh.sort(key=lambda v: v.posted_at, reverse=True)
        fresh, tail = fresh[:cap], len(fresh) - cap

    pages = core.build_pages(fresh, prof.roles_of(user["profile"]))
    if tail:
        # Приписку вешаем на последнюю страницу: она про то, что будет
        # дальше, и читается в конце подборки, а не поверх неё.
        pages[-1] += (
            f"\n\nЕщё {tail} " + core._plural(tail)
            + " — пришлю в следующие дни, чтобы не заваливать одним сообщением."
        )
    digest = pages[0]
    label = user.get("username") or user["chat_id"]
    breakdown = ", ".join(
        f"{core.REASONS.get(k, k)} {v}" for k, v in sorted(rejected.items())
    )
    print(f"\n--- {label}: подошло {len(passed)}, из них новых {len(fresh)}")
    if breakdown:
        print(f"    отсеяно: {breakdown}")
    print(core.to_plain(digest))

    if dry_run:
        return len(fresh)

    store.save_digest(user["chat_id"], pages)
    tg.send(user["chat_id"], digest, core.digest_keyboard(0, len(pages)))
    stats.track("digest_sent")
    stats.track("vacancies_sent", len(fresh))

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
    parser.add_argument("--stats", action="store_true", help="показать аналитику и выйти")
    parser.add_argument(
        "--once-a-day",
        action="store_true",
        help="выйти, если сегодняшняя рассылка уже состоялась (для догоняющих запусков)",
    )
    args = parser.parse_args()

    load_env()

    if args.stats:
        print(core.to_plain(stats.report(days=14)))
        return 0

    today = date.today().isoformat()
    if args.once_a_day:
        # Расписание GitHub — обещание, а не гарантия: запуск могут задержать
        # на часы или пропустить совсем. Поэтому в течение дня будят ещё
        # несколько раз, и каждый следующий проверяет, не ушла ли рассылка.
        done = store.last_sent()
        if done == today:
            print(f"Рассылка за {today} уже состоялась — выхожу.")
            return 0
        print(f"Рассылки за {today} ещё не было (последняя: {done or 'не помню'}).")

    users = store.all_users()
    print(f"Подписчиков: {len(users)}")
    if not users:
        print("Никого нет — обходить hirehi незачем.")
        return 0

    config = json.loads((ROOT / "config.json").read_text("utf-8"))
    days = args.days or config.get("lookback_days", 2)
    since = (date.today() - timedelta(days=days)).isoformat()

    # Медленным доскам своё окно: designer.ru публикует единицы в неделю,
    # и на общих двух сутках оттуда приходит одна вакансия или ни одной.
    # Повторов это не создаёт — их отсекает память о показанном.
    windows = config.get("lookback_days_by_source") or {}

    # Потолок страниц на источник. hirehi отдаёт каждую вакансию отдельной
    # страницей, и после расширения на маркетинг, аналитику и менеджмент
    # их стало 4011 за прогон (~100 минут) — рассылка перестала укладываться
    # в сутки и 15.10.2026 не ушла вовсе. Страницы отсортированы по свежести,
    # поэтому потолок режет хвост окна, а не сегодняшние вакансии.
    caps = config.get("max_pages_by_source") or {}

    # Маршрутный лист: куда идти ради тех профессий, которые кто-нибудь
    # из подписчиков ищет. Раньше обход был слепым — все источники всегда,
    # а больше половины собранного выбрасывалось фильтром роли.
    wanted = sorted({role for user in users for role in prof.roles_of(user["profile"])})
    routes = taxonomy.plan(wanted)
    print(f"Ищем профессии: {', '.join(taxonomy.label(r) for r in wanted)}")

    print(f"Собираю вакансии с {since}…")
    vacancies = []
    for name, key, source in (("hirehi", "hirehi", hirehi),
                              ("finder", "finder", finder), ("habr", "habr", habr),
                              ("careerspace", "careerspace", careerspace),
                              ("hh", "hh", hh), ("getmatch", "getmatch", getmatch),
                              ("designer.ru", "designer", designer),
                              ("карьерные сайты", "companies", companies),
                              ("доски компаний", "ats", ats),
                              ("linkedin", "linkedin", linkedin),
                              ("friend.work", "friendwork", friendwork),
                              ("telegram", "telegram", telegram)):
        own = windows.get(key)
        start = (date.today() - timedelta(days=own)).isoformat() if own else since
        plan = routes.get(key)
        if not plan:
            # Источник по этим профессиям ничего не отдаёт — ходить незачем.
            continue
        extra = {"areas": config["hh_areas"]} if key == "hh" and config.get("hh_areas") else {}
        try:
            found = source.collect(start, limit=args.limit or caps.get(key),
                                   routes=plan, **extra)
        except Exception as exc:
            # Упавший источник не должен ронять рассылку целиком:
            # лучше дайджест по одной площадке, чем молчание.
            print(f"  ! {name} не отдал вакансии: {exc}")
            continue
        print(f"Найдено на {name}: {len(found)}" + (f" (окно {own} дней)" if own else ""))
        vacancies += found

    vacancies = core.fill_grades(vacancies)
    before = len(vacancies)
    vacancies = core.dedupe(vacancies)
    if before != len(vacancies):
        print(f"Склеено дублей между источниками: {before - len(vacancies)}")
    print(f"Всего вакансий: {len(vacancies)}")

    if args.json:
        (ROOT / "last_run.json").write_text(core.to_json(vacancies), "utf-8")
        print("Записано в last_run.json")

    if not vacancies:
        # Пустой сбор — это не «сегодня ничего не публиковали», а поломка:
        # даже в выходные площадки дают десятки вакансий. Молчим и выходим
        # с ошибкой, чтобы запуск загорелся красным, а догоняющий попробовал
        # снова: разослать «ничего нового» было бы враньём.
        print("Ни один источник не отдал вакансий — рассылку не делаю.")
        return 1

    if not args.dry_run:
        # Кладём собранное в хранилище: из этого запаса бот показывает
        # подборку новому подписчику сразу после мастера настройки.
        store.save_pool(core.to_rows(vacancies), date.today().isoformat())
        print("Запас вакансий обновлён")

    sent = 0
    for user in users:
        try:
            sent += deliver(user, vacancies, args.dry_run,
                            config.get("max_per_digest", 0))
        except Exception as exc:
            # Один упавший подписчик не должен ронять рассылку остальным.
            # Чаще всего это «bot was blocked by the user».
            print(f"  ! не доставлено {user.get('chat_id')}: {exc}")

    if not args.dry_run:
        # Отметку ставим только после того, как круг подписчиков пройден:
        # упади мы раньше, догоняющий запуск должен взяться за дело.
        store.mark_sent(today)

    print(f"\nВсего отправлено вакансий: {sent}")
    if args.dry_run:
        print("dry-run: ничего не отправлено, память не тронута")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
