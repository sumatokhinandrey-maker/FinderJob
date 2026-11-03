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
from finderjob import daily, stats, store, taxonomy  # noqa: E402
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
    parser.add_argument("--prepare", action="store_true",
                        help="собрать вакансии заранее, ничего не отправляя")
    parser.add_argument("--send-prepared", action="store_true",
                        help="отправить только сегодняшний заранее собранный запас")
    args = parser.parse_args()

    load_env()

    if args.stats:
        print(core.to_plain(stats.report(days=14)))
        return 0

    if args.prepare and args.send_prepared:
        parser.error("выбери только один режим")
    if args.send_prepared and args.dry_run:
        parser.error("--send-prepared нельзя сочетать с --dry-run")
    if args.send_prepared:
        # Вне дневного окна не шлём и не пересобираем: поздний ночной запуск
        # иначе уходил в следующие московские сутки и слал завтрашний дайджест
        # раньше срока. Замок — внутри send_prepared, здесь дублируем, чтобы
        # не молотить сбор на 40 минут впустую.
        if not daily.send_window_open():
            print(daily.send_window_note())
            return 0
        rows, collected_at = store.load_pool()
        if rows and collected_at == daily.moscow_day():
            daily.send_prepared()
            return 0
        print("Сегодняшнего запаса нет — собираю перед отправкой")

    today = date.today().isoformat()
    if args.once_a_day and not (args.prepare or args.send_prepared):
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
        store.save_pool(core.to_rows(vacancies), daily.moscow_day())
        print("Запас вакансий обновлён")

    if args.prepare:
        print("Подготовка завершена; подписчикам ничего не отправлено")
        return 0
    if args.send_prepared:
        daily.send_prepared()
        return 0

    sent = 0
    for user in users:
        try:
            sent += daily.deliver(user, vacancies,
                                  cap=config.get("max_per_digest", 0),
                                  dry_run=args.dry_run)
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
