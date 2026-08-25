"""Профиль пользователя: что он ищет и как это показать словами.

Профиль хранит только личные критерии. Роль (что мы вообще считаем вакансией
дизайнера) общая для всех и живёт в config.json — это граница продукта,
а не настройка.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GRADES = [
    ("junior", "Junior"),
    ("middle", "Middle"),
    ("senior", "Senior"),
    ("lead", "Lead"),
]

SALARIES = [
    (100000, "от 100 000"),
    (150000, "от 150 000"),
    (200000, "от 200 000"),
    (250000, "от 250 000"),
]

FORMATS = [
    ("remote", "Удалённо"),
    ("hybrid", "Гибрид"),
    ("office", "Офис"),
]

CITIES = [
    ("Москва", "Москва"),
    ("Санкт-Петербург", "Санкт-Петербург"),
]


def blank() -> dict:
    """Пустой профиль: пусто везде значит «не важно», а не «ничего не подходит».
    Так человек, бросивший мастер на середине, всё равно получит дайджест."""
    return {
        "grades": [],
        "salary_min": None,
        "formats": [],
        "any_city": True,
        "cities": [],
        "abroad": False,
    }


def new_user(chat_id: int, username: str | None, today: str) -> dict:
    return {
        "chat_id": chat_id,
        "username": username,
        "created_at": today,
        "step": None,
        "mode": "setup",
        "profile": blank(),
        "seen": {},
    }


def needs_cities(profile: dict) -> bool:
    """Про города спрашиваем, только если человек готов куда-то ездить."""
    formats = profile.get("formats") or []
    return "hybrid" in formats or "office" in formats


def to_filters(profile: dict) -> dict:
    """Профиль в том виде, в каком его ждёт core.check — плюс общий фильтр роли."""
    config = json.loads((ROOT / "config.json").read_text("utf-8"))
    role = config.get("role", {})
    formats = profile.get("formats") or []
    # Пустой список форматов значит «любой», поэтому разрешаем все три.
    chosen = {name: (not formats or name in formats) for name, _ in FORMATS}

    return {
        "grades": profile.get("grades") or [],
        "include_any": role.get("include_any", []),
        "exclude_any": role.get("exclude_any", []),
        "salary_min": profile.get("salary_min"),
        "work_formats": {
            **chosen,
            "any_city": profile.get("any_city", True),
            "cities": profile.get("cities") or [],
            "abroad": profile.get("abroad", False),
        },
    }


# --------------------------------------------------------------------------
# как профиль выглядит словами
# --------------------------------------------------------------------------

ANY = "не важно"


def _named(selected: list, options: list[tuple]) -> str:
    if not selected:
        return ANY
    labels = [label for value, label in options if value in selected]
    return ", ".join(labels) if labels else ANY


def describe_grades(profile: dict) -> str:
    return _named(profile.get("grades") or [], GRADES)


def describe_salary(profile: dict) -> str:
    value = profile.get("salary_min")
    if not value:
        return ANY
    return f"от {value:,} рублей".replace(",", " ")


def describe_formats(profile: dict) -> str:
    return _named(profile.get("formats") or [], FORMATS)


def describe_cities(profile: dict) -> str:
    if not needs_cities(profile):
        # Города имеют смысл только для гибрида и офиса. Разводим два случая:
        # человек выбрал одну удалёнку — и человек не выбрал ничего.
        if profile.get("formats"):
            return "не спрашиваем, ты выбрал только удалёнку"
        return "не спрашиваем, пока формат работы не важен"
    if profile.get("any_city"):
        return "любой город"
    parts = list(profile.get("cities") or [])
    if profile.get("abroad"):
        parts.append("за границей")
    return ", ".join(parts) if parts else ANY


def summary(profile: dict) -> str:
    return (
        "<b>Твои критерии</b>\n\n"
        f"Грейд: {describe_grades(profile)}\n"
        f"Зарплата: {describe_salary(profile)}\n"
        f"Формат работы: {describe_formats(profile)}\n"
        f"Города: {describe_cities(profile)}"
    )
