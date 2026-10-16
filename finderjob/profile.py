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

# Регион вместо ввода городов руками: четыре корзины закрывают тот же вопрос
# без клавиатуры. Пустой выбор значит «любой».
REGIONS = [
    ("ru", "Россия"),
    ("cis", "СНГ"),
    ("abroad", "Зарубежные"),
]

# Слово согласуется с существительным шага, поэтому одной константы мало:
# «Зарплата: любая», но «Формат работы: любой».
ANY = "любой"
ANY_F = "любая"

# Роли по умолчанию — то же, что бот искал, когда роль была общей для всех
# и означала «дизайн». Нужны для тех, кто прошёл мастер до появления выбора
# профессии: без них им утром пришла бы другая подборка, и это выглядело бы
# как поломка.
DEFAULT_ROLES = ["design.product", "design.web"]


def roles_of(profile: dict) -> list[str]:
    return profile.get("roles") or DEFAULT_ROLES


def blank() -> dict:
    """Пустой профиль: пусто везде значит «пропущено», а не «ничего
    не подходит». Так человек, бросивший мастер на середине, всё равно
    получит дайджест."""
    return {
        "directions": [],
        "roles": [],
        "grades": [],
        "salary_min": None,
        "salary_max": None,
        "formats": [],
        "regions": [],
        "sources": [],
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


def needs_region(profile: dict) -> bool:
    """Регион имеет смысл, только если человек готов куда-то ездить.
    Пустой список форматов — это «пропущено», то есть офис и гибрид тоже
    подходят, и место снова важно."""
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
    if step == "direction":
        return bool(directions_of(profile))
    if step == "role":
        return bool(profile.get("roles"))
    if step == "region":
        # Пустой список регионов означает «любой», а клавиатура показывает
        # все регионы отмеченными. Значит, продолжить можно сразу, не снимая
        # ни одной отметки.
        return True
    if step == "sources":
        return True
    return False


def migrate(profile: dict) -> dict:
    """Профили, заполненные до появления регионов, хранят место двумя старыми
    полями: places («города России» / «за рубежом») и cities — списком городов
    словами. Переносим их в корзины регионов, иначе выбор молча перестал бы
    действовать и человеку поехали бы вакансии со всей страны.

    Конкретный город корзиной не заменить: у того, кто выбрал Петербург,
    подборка станет шире. Это цена отказа от ввода городов руками.
    """
    if "directions" not in profile:
        profile["directions"] = directions_of(profile)
    if profile.get("regions"):
        return profile
    if "places" not in profile and "cities" not in profile:
        return profile

    regions: list[str] = []
    for place in profile.pop("places", None) or []:
        value = "ru" if place == "russia" else "abroad"
        if value not in regions:
            regions.append(value)
    for city in profile.pop("cities", None) or []:
        value = geo.where(city)
        if value and value not in regions:
            regions.append(value)
    profile["regions"] = regions
    return profile


def directions_of(profile: dict) -> list[str]:
    """Направления, выбранные в начале мастера; старые профили выводим из ролей."""
    if "directions" in profile:
        return list(profile.get("directions") or [])
    seen = []
    for role in profile.get("roles") or []:
        direction = role.partition(".")[0]
        if direction not in seen:
            seen.append(direction)
    return seen


def to_filters(profile: dict) -> dict:
    """Профиль в том виде, в каком его ждёт core.check.

    Общего фильтра роли здесь больше нет: что считать вакансией профессии —
    свойство самой профессии (roles.json), а не одна настройка на всех.
    Глобальные стоп-слова тоже ушли туда: пока они были общими, «графический
    дизайн» резался у всех и роль «графический дизайнер» была невыразима.
    """
    formats = profile.get("formats") or []
    # Пустой список форматов значит «пропущено», поэтому разрешаем все три.
    chosen = {name: (not formats or name in formats) for name, _ in FORMATS}

    return {
        "grades": profile.get("grades") or [],
        "roles": roles_of(profile),
        "sources": profile.get("sources") or [],
        "salary_min": profile.get("salary_min"),
        "salary_max": profile.get("salary_max"),
        "work_formats": {
            **chosen,
            "regions": profile.get("regions") or [],
        },
    }


# --------------------------------------------------------------------------
# как профиль выглядит словами
# --------------------------------------------------------------------------


def _named(selected: list, options: list[tuple], empty: str = ANY) -> str:
    if not selected:
        return empty
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
    return ANY_F


def describe_formats(profile: dict) -> str:
    return _named(profile.get("formats") or [], FORMATS)


def describe_roles(profile: dict) -> str:
    from finderjob import taxonomy

    roles = roles_of(profile)
    display = {
        "design.product": "UX/UI-дизайнер",
        "design.graphic": "Графический дизайнер",
        "design.web": "Веб-дизайнер",
        "design.motion": "Моушн-дизайнер",
        "design.game": "Гейм-дизайнер",
        "product.analyst": "Аналитик",
    }
    labels = []
    for role in roles:
        label = display.get(role, taxonomy.label(role))
        if label.casefold().endswith(" (любой)"):
            label = label[: -len(" (любой)")]
        if label.casefold() not in {item.casefold() for item in labels}:
            labels.append(label)
    return ", ".join(labels) if labels else "дизайн"


def describe_regions(profile: dict) -> str:
    if not needs_region(profile):
        return "не спрашиваем, ты выбрал только удалёнку"
    return _named(profile.get("regions") or [], REGIONS)


def describe_sources(profile: dict) -> str:
    from finderjob import wizard

    keys = set(profile.get("sources") or [])
    all_sources = {
        source
        for group in wizard.SOURCE_GROUPS.values()
        for source in group["sources"]
    }
    if not keys or all_sources.issubset(keys):
        return "все"
    selected = []
    for key, label in wizard.SOURCE_GROUPS.items():
        if keys.intersection(label["sources"]):
            selected.append(label["label"])
    return ", ".join(selected)


def summary(profile: dict) -> str:
    from html import escape
    from finderjob import wizard

    roles = escape(describe_roles(profile))
    grades = escape(describe_grades(profile).lower())
    raw_salary = describe_salary(profile).lower()
    salary = escape(raw_salary + (" ₽" if "от " in raw_salary or "до " in raw_salary else ""))
    formats = profile.get("formats") or [value for value, _ in FORMATS]
    format_labels = {"remote": "удалёнка", "hybrid": "гибрид", "office": "офис"}
    work = ", ".join(format_labels[value] for value in formats if value in format_labels)
    if not needs_region(profile):
        region = "не нужен для удалённой работы"
    else:
        region_labels = {"ru": "РФ", "cis": "СНГ", "abroad": "зарубежье"}
        region = ", ".join(
            region_labels[value]
            for value in profile.get("regions") or []
            if value in region_labels
        ) or "любой"

    body = (
        "<b>Фильтры</b>\n\n"
        f"• <strong>{roles}</strong>\n"
        f"⚡️ Грейд: {grades}\n"
        f"💰 Вилка: {salary}\n"
        f"💼 Формат: {escape(work)}\n"
        f"📍 Регион: {escape(region)}"
    )
    sources = set(profile.get("sources") or [])
    available_sources = {
        source
        for group in wizard.SOURCE_GROUPS.values()
        for source in group["sources"]
    }
    if sources and not available_sources.issubset(sources):
        body += f"\nИсточники: {escape(describe_sources(profile))}"
    return body


