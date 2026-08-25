"""Профиль пользователя: что он ищет и как это показать словами.

Профиль хранит только личные критерии. Роль (что мы вообще считаем вакансией
дизайнера) общая для всех и живёт в config.json — это граница продукта,
а не настройка.
"""

from __future__ import annotations

import json
from pathlib import Path

from finderjob import geo

ROOT = Path(__file__).resolve().parent.parent

GRADES = [
    ("intern", "Стажёр"),
    ("junior", "Junior"),
    ("middle", "Middle"),
    ("senior", "Senior"),
    ("lead", "Lead"),
]

FORMATS = [
    ("remote", "Удалённо"),
    ("hybrid", "Гибрид"),
    ("office", "Офис"),
]

# Корзины мест. Конкретные города человек добавляет сам, списком через запятую.
PLACES = [
    ("russia", "Города России"),
    ("abroad", "За рубежом"),
]

ANY = "неважно"


def blank() -> dict:
    """Пустой профиль: пусто везде значит «пропущено», а не «ничего
    не подходит». Так человек, бросивший мастер на середине, всё равно
    получит дайджест."""
    return {
        "grades": [],
        "salary_min": None,
        "salary_max": None,
        "formats": [],
        "places": [],
        "cities": [],
    }


def new_user(chat_id: int, username: str | None, today: str) -> dict:
    return {
        "chat_id": chat_id,
        "username": username,
        "created_at": today,
        "step": None,
        "mode": "setup",
        "awaiting": None,
        "profile": blank(),
        "seen": {},
    }


def needs_cities(profile: dict) -> bool:
    """Про место спрашиваем, только если человек готов куда-то ездить.
    Пустой список форматов — это «пропущено», то есть офис и гибрид тоже
    подходят, и место снова имеет значение."""
    formats = profile.get("formats") or []
    if not formats:
        return True
    return "hybrid" in formats or "office" in formats


def step_filled(profile: dict, step: str) -> bool:
    """Выбрано ли хоть что-то на шаге. Пока не выбрано, «Далее» ничем
    не отличается от «Пропустить», поэтому его не показываем."""
    if step == "grade":
        return bool(profile.get("grades"))
    if step == "salary":
        return bool(profile.get("salary_min") or profile.get("salary_max"))
    if step == "format":
        return bool(profile.get("formats"))
    if step == "cities":
        return bool(profile.get("places") or profile.get("cities"))
    return False


def to_filters(profile: dict) -> dict:
    """Профиль в том виде, в каком его ждёт core.check — плюс общий фильтр роли."""
    config = json.loads((ROOT / "config.json").read_text("utf-8"))
    role = config.get("role", {})
    formats = profile.get("formats") or []
    # Пустой список форматов значит «пропущено», поэтому разрешаем все три.
    chosen = {name: (not formats or name in formats) for name, _ in FORMATS}

    return {
        "grades": profile.get("grades") or [],
        "include_any": role.get("include_any", []),
        "exclude_any": role.get("exclude_any", []),
        "salary_min": profile.get("salary_min"),
        "salary_max": profile.get("salary_max"),
        "work_formats": {
            **chosen,
            "places": profile.get("places") or [],
            "cities": profile.get("cities") or [],
        },
    }


# --------------------------------------------------------------------------
# как профиль выглядит словами
# --------------------------------------------------------------------------


def _named(selected: list, options: list[tuple]) -> str:
    if not selected:
        return ANY
    labels = [label for value, label in options if value in selected]
    return ", ".join(labels) if labels else ANY


def _money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def describe_grades(profile: dict) -> str:
    return _named(profile.get("grades") or [], GRADES)


def describe_salary(profile: dict) -> str:
    low, high = profile.get("salary_min"), profile.get("salary_max")
    if low and high:
        return f"от {_money(low)} до {_money(high)}"
    if low:
        return f"от {_money(low)}"
    if high:
        return f"до {_money(high)}"
    return ANY


def describe_formats(profile: dict) -> str:
    return _named(profile.get("formats") or [], FORMATS)


def describe_cities(profile: dict) -> str:
    if not needs_cities(profile):
        return "не спрашиваем, ты выбрал только удалёнку"
    parts = [label for value, label in PLACES if value in (profile.get("places") or [])]
    parts += list(profile.get("cities") or [])
    return ", ".join(parts) if parts else ANY


def summary(profile: dict) -> str:
    return (
        "<b>Твои критерии</b>\n\n"
        f"Грейд: {describe_grades(profile)}\n"
        f"Зарплата: {describe_salary(profile)}\n"
        f"Формат работы: {describe_formats(profile)}\n"
        f"Город: {describe_cities(profile)}"
    )


# --------------------------------------------------------------------------
# разбор того, что человек написал руками
# --------------------------------------------------------------------------


def parse_salary(text: str) -> tuple[int | None, int | None] | None:
    """Вилка из свободного текста: «150000-250000», «от 150 до 250 тысяч»,
    «150к», «200000». Возвращает (от, до) или None, если чисел не нашлось."""
    import re

    cleaned = text.lower().replace(" ", " ").replace("—", "-").replace("–", "-")
    numbers = []
    for raw, suffix in re.findall(r"(\d[\d\s]*)\s*(к|k|тыс[а-я.]*)?", cleaned):
        digits = raw.replace(" ", "")
        if not digits:
            continue
        value = int(digits)
        if suffix or value < 1000:
            value *= 1000
        numbers.append(value)

    if not numbers:
        return None
    if len(numbers) == 1:
        # «до 200000» — единственное число, но это потолок, а не порог.
        if re.search(r"\bдо\b", cleaned) and not re.search(r"\bот\b", cleaned):
            return None, numbers[0]
        return numbers[0], None
    low, high = min(numbers[0], numbers[1]), max(numbers[0], numbers[1])
    return low, high


def parse_cities(text: str) -> list[str]:
    """Список городов через запятую. Приводим написание к канону справочника,
    чтобы «питер» и «Санкт-Петербург» не разъезжались."""
    names = []
    for chunk in text.replace(";", ",").split(","):
        name = " ".join(chunk.split()).strip("-—.;:")
        if not name or len(name) > 40:
            continue
        canonical = geo.canon(name)
        pretty = "-".join(part[:1].upper() + part[1:] for part in canonical.split("-"))
        if pretty not in names:
            names.append(pretty)
    return names[:10]
