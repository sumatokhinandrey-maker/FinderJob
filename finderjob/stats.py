"""Счётчики событий бота.

Vercel Analytics считает просмотры страниц, а у нас страниц нет — есть вебхук
и рассылка. Поэтому события считаем сами, в том же Upstash, где лежат профили.

Счётчиков два на событие: общий за всё время и дневной. Дневные живут 90 дней
и сами исчезают — держать их вечно незачем, а тренд за квартал показывают.
"""

from __future__ import annotations

from datetime import date, timedelta

from finderjob import store

PREFIX = "finderjob:stat"
KEEP_DAYS = 90

# Что считаем. Порядок важен: в таком виде события идут в отчёте,
# и по нему читается воронка.
EVENTS = [
    ("start", "запустили бота"),
    ("wizard_done", "прошли мастер до конца"),
    ("settings", "открывали настройки"),
    ("city_typed", "вводили свой город"),
    ("digest_sent", "получили дайджест"),
    ("vacancies_sent", "вакансий отправлено"),
    ("stop", "отписались"),
]


def track(event: str, count: int = 1, today: str | None = None) -> None:
    """Событие произошло. Молча проглатываем сбои хранилища: аналитика
    не должна ронять ни вебхук, ни рассылку."""
    day = today or date.today().isoformat()
    try:
        store._command("INCRBY", f"{PREFIX}:total:{event}", count)
        daily = f"{PREFIX}:day:{day}:{event}"
        store._command("INCRBY", daily, count)
        store._command("EXPIRE", daily, KEEP_DAYS * 24 * 3600)
    except Exception:
        pass


def _number(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def totals() -> dict[str, int]:
    return {
        event: _number(store._command("GET", f"{PREFIX}:total:{event}"))
        for event, _ in EVENTS
    }


def by_day(days: int = 14) -> dict[str, dict[str, int]]:
    today = date.today()
    out: dict[str, dict[str, int]] = {}
    for shift in range(days):
        day = (today - timedelta(days=shift)).isoformat()
        out[day] = {
            event: _number(store._command("GET", f"{PREFIX}:day:{day}:{event}"))
            for event, _ in EVENTS
        }
    return out


def report(days: int = 7) -> str:
    """Сводка для телеграма. Воронка сверху, дни снизу."""
    total = totals()
    users = len(store.all_users())

    started = total.get("start", 0)
    finished = total.get("wizard_done", 0)
    share = f"{finished * 100 // started}%" if started else "—"

    lines = [
        "<b>Аналитика FinderJob</b>",
        "",
        f"Подписчиков сейчас: {users}",
        "",
        "<b>За всё время</b>",
        f"Запустили бота: {started}",
        f"Дошли до конца мастера: {finished} ({share})",
        f"Отписались: {total.get('stop', 0)}",
        f"Дайджестов отправлено: {total.get('digest_sent', 0)}",
        f"Вакансий в них: {total.get('vacancies_sent', 0)}",
    ]

    daily = by_day(days)
    active = {d: v for d, v in daily.items() if any(v.values())}
    if active:
        lines += ["", f"<b>Последние {days} дней</b>"]
        for day in sorted(active, reverse=True):
            v = active[day]
            parts = []
            if v["start"]:
                parts.append(f"пришло {v['start']}")
            if v["wizard_done"]:
                parts.append(f"настроились {v['wizard_done']}")
            if v["digest_sent"]:
                parts.append(f"дайджестов {v['digest_sent']}")
            if v["vacancies_sent"]:
                parts.append(f"вакансий {v['vacancies_sent']}")
            if v["stop"]:
                parts.append(f"ушло {v['stop']}")
            lines.append(f"{day[8:10]}.{day[5:7]} — " + ", ".join(parts))

    return "\n".join(lines)
