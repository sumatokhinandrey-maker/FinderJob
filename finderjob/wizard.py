"""Мастер настройки: тексты, кнопки и переходы между шагами.

Весь диалог — одна карточка, которая переписывается на месте. Человек видит
не простыню из десяти сообщений, а один экран, который меняется под нажатия.

Кнопка «Любая»/«Любой» стирает критерий и ведёт дальше: незаполненное поле
ничего не отсекает. У грейда её нет — там выбор обязателен, потому что
неназванный уровень был главным источником нерелевантных вакансий.
"""

from __future__ import annotations

import re

from finderjob import profile as prof
from finderjob import taxonomy

STEPS = ["direction", "role", "grade", "salary", "format", "region"]

# Слово согласуется с существительным шага: «Зарплата — любая», но
# «Формат работы — любой». У grade и direction кнопки нет вовсе.
SKIP_LABEL = {
    "salary": "Любая",
    "format": "Выбрать всё",
    "region": "Любой",
    "sources": "Все",
}

CHECK = "✓ "  # галочка перед выбранным пунктом

SOURCE_GROUPS = {
    "companies": {"label": "Сайты компаний", "sources": ["companies", "ats"]},
    "aggregators": {
        "label": "Агрегаторы",
        "sources": ["hirehi", "finder", "habr", "getmatch", "careerspace", "designer"],
    },
    "hh": {"label": "HH.RU", "sources": ["hh"]},
    "telegram": {"label": "Telegram", "sources": ["telegram"]},
    "linkedin": {"label": "LinkedIn", "sources": ["linkedin"]},
}

# Описание бота — то, что видно в пустом чате до кнопки «Начать».
# Ставится через setup_bot.py, здесь лежит как источник правды.
DESCRIPTION = "Твой персональный дайджест актуальных вакансий на каждый день"
WELCOME = (
    "Привет! Я FinderJob. Каждый день присылаю вакансии по твоим критериям "
    "в 9:00 по Москве.\n\nНажми «Фильтры», чтобы настроить подборку."
)

REVIEW_PROMPT = "Проверь критерии. Если всё верно, нажми «Найти вакансии»."

EMPTY_POOL = (
    "Критерии сохранены.\n\n"
    "Свежих вакансий пока нет под рукой — первая подборка придёт "
    "завтра в 9 утра по Москве."
)

HELP = "Чтобы открыть или настроить подборку, нажми «Фильтры» внизу."
MENU_PROMPT = "Чтобы открыть или изменить критерии, нажми «Фильтры» внизу."

# Пороги зарплаты. Нижняя граница, а не вилка: верхняя отсекала бы и тех,
# кто платит больше потолка, — на этом уже обжигались.
SALARY_STEPS = [100_000, 150_000, 200_000, 250_000]

QUESTIONS = {
    "direction": "<b>Шаг 1 из 6. Направление</b>\n\nЧем занимаешься? Можно выбрать несколько.",
    "role": "<b>Шаг 2 из 6. Роли</b>\n\nКакие вакансии присылать? Можно выбрать несколько.",
    "grade": "<b>Шаг 3 из 6. Грейд</b>\n\nОтметь подходящие уровни. Можно выбрать несколько.",
    "salary": (
        "<b>Шаг 4 из 6. Вилка</b>\n\n"
        "Выбери минимальную зарплату. Вакансии без указанной вилки останутся."
    ),
    "format": "<b>Шаг 5 из 6. Формат</b>\n\nГде готов работать? Можно выбрать несколько.",
    "region": (
        "<b>Шаг 6 из 6. Регион</b>\n\n"
        "В каких регионах искать вакансии? Можно выбрать несколько."
    ),
    "sources": (
        "<b>Источники</b>\n\nИсточники вакансий. "
        "По умолчанию включены все подключённые источники."
    ),
}


def _button(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _rows(buttons: list[dict], per_row: int = 2) -> list[list[dict]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def _all_sources() -> list[str]:
    return [k for k, v in taxonomy.sources().items() if v.get("status") == "connected"]


def _toggle_from_all(profile: dict, field: str, value: str, everything: list[str]) -> None:
    """Снять один пункт там, где по умолчанию включено всё.

    Пустое поле значит «подходит всё», поэтому первое же снятие надо
    развернуть в полный список — иначе нажатие на «Россию» оставило бы
    ровно Россию вместо того, чтобы её убрать.
    """
    current = profile.get(field) or list(everything)
    if value in current:
        current.remove(value)
    else:
        current.append(value)
    # Сняли всё до последнего — это то же самое, что «подходит любой».
    profile[field] = [] if not current else current


# --------------------------------------------------------------------------
# переходы между шагами
# --------------------------------------------------------------------------


def next_step(step: str, profile: dict) -> str | None:
    following = STEPS[STEPS.index(step) + 1 :] if step in STEPS else []
    return following[0] if following else None


def prev_step(step: str, profile: dict) -> str | None:
    if step not in STEPS:
        return None
    earlier = STEPS[: STEPS.index(step)]
    return earlier[-1] if earlier else None


# --------------------------------------------------------------------------
# клавиатуры
# --------------------------------------------------------------------------


def _tail(step: str, profile: dict, mode: str) -> list[list[dict]]:
    """Хвост любого шага: пропустить, назад и далее.

    «Далее» появляется только когда на шаге что-то выбрано: на пустом шаге
    оно означало бы ровно то же, что «Неважно».

    На первом шаге назад некуда. В режиме правки назад ведёт
    в список настроек: человек пришёл поменять один пункт, а не проходить
    мастер заново.
    """
    nav = []
    if mode == "edit" or prev_step(step, profile):
        nav.append(_button("Назад", f"bk:{step}"))
    if prof.step_filled(profile, step):
        nav.append(_button("Сохранить" if mode == "edit" else "Продолжить", f"nx:{step}"))

    rows = []
    label = SKIP_LABEL.get(step)
    if label:
        rows.append([_button(label, f"sk:{step}")])
    if nav:
        rows.append(nav)
    return rows


def keyboard(step: str, profile: dict, mode: str) -> list[list[dict]]:
    if step == "direction":
        chosen = prof.directions_of(profile)
        buttons = [
            _button(f"{CHECK if key in chosen else ''}{body['label']}", f"td:{key}")
            for key, body in taxonomy.roles().items()
        ]
        return _rows(buttons) + _tail(step, profile, mode)

    if step == "role":
        # Показываем роли только выбранных направлений: тридцать кнопок
        # списком в телеграме — простыня, которую не читают.
        chosen = profile.get("roles") or []
        buttons = []
        for direction in prof.directions_of(profile):
            for key, body in taxonomy.roles()[direction]["roles"].items():
                full = f"{direction}.{key}"
                buttons.append(
                    _button(f"{CHECK if full in chosen else ''}{body['label']}", f"tr:{full}")
                )
        return _rows(buttons) + [[_button("Выбрать всё", "all:role")]] + _tail(step, profile, mode)

    if step == "region":
        # Пустой список означает «подходит любой», и показывать его пустым
        # нечестно: человек видит ноль галочек там, где на деле включено всё.
        # Рисуем все отмеченными — снимать лишнее понятнее, чем набирать
        # с нуля.
        chosen = profile.get("regions") or [value for value, _ in prof.REGIONS]
        buttons = [
            _button(f"{CHECK if value in chosen else ''}{label}", f"trg:{value}")
            for value, label in prof.REGIONS
        ]
        return _rows(buttons) + _tail(step, profile, mode)

    if step == "sources":
        # Рядом с каждой площадкой — что она даст именно по твоим профессиям.
        # Без этого можно выбрать фронтенд вместе с designer.ru и ждать
        # вакансий, которых оттуда не будет никогда.
        chosen = profile.get("sources") or _all_sources()
        fits = taxonomy.coverage(prof.roles_of(profile))
        buttons = []
        for group, body in SOURCE_GROUPS.items():
            available = [key for key in body["sources"] if key in _all_sources()]
            if not available:
                continue
            selected = [key for key in available if key in chosen]
            mark = CHECK if len(selected) == len(available) else ("◩ " if selected else "")
            covers = any(fits.get(key) for key in available)
            note = "" if covers else " (нет вакансий по этим ролям)"
            buttons.append(_button(f"{mark}{body['label']}{note}", f"tsrcgrp:{group}"))
        return _rows(buttons) + _tail(step, profile, mode)

    if step == "grade":
        chosen = profile.get("grades") or []
        buttons = [
            _button(f"{CHECK if value in chosen else ''}{label}", f"tg:{value}")
            for value, label in prof.GRADES
        ]
        return _rows(buttons) + _tail(step, profile, mode)

    if step == "salary":
        current = profile.get("salary_min")
        buttons = [
            _button(
                f"{CHECK if current == value else ''}от {value // 1000} 000",
                f"ts:{value}",
            )
            for value in SALARY_STEPS
        ]
        return _rows(buttons) + _tail(step, profile, mode)

    if step == "format":
        chosen = profile.get("formats") or []
        buttons = [
            _button(f"{CHECK if value in chosen else ''}{label}", f"tf:{value}")
            for value, label in prof.FORMATS
        ]
        return _rows(buttons, 3) + _tail(step, profile, mode)

    return []


def settings_keyboard() -> list[list[dict]]:
    """Меню карточки фильтров: каждая кнопка ведёт к точечной правке."""
    return [
        [_button("Профессия", "ed:direction"), _button("Грейд", "ed:grade")],
        [_button("Вилка", "ed:salary"), _button("Формат", "ed:format")],
        [_button("Регион", "ed:region"), _button("Источники", "ed:more")],
        [_button("Сбросить", "reset:ask")],
        [_button("Закрыть", "filters:close")],
    ]


def format_region_keyboard() -> list[list[dict]]:
    return [
        [_button("Формат", "ed:format"), _button("Регион", "ed:region")],
        [_button("Назад", "ed:menu")],
    ]


def menu_keyboard() -> dict:
    return {
        "keyboard": [[{"text": "Фильтры"}]],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
    }


def done_keyboard() -> list[list[dict]]:
    return [
        [_button("Найти вакансии", "search:find")],
        [_button("Изменить фильтры", "search:edit")],
    ]


def input_keyboard(step: str) -> list[list[dict]]:
    """Из ввода текста надо уметь выйти, ничего не написав."""
    return [[_button("Назад", f"ca:{step}")]]


def render(step: str, profile: dict, mode: str) -> tuple[str, list[list[dict]]]:
    body = QUESTIONS[step]
    if mode != "setup":
        body = re.sub(r"Шаг \d+ из \d+[.·] ", "", body, count=1)
    return body, keyboard(step, profile, mode)


def render_settings(profile: dict) -> tuple[str, list[list[dict]]]:
    return prof.summary(profile) + "\n\nЧто изменить?", settings_keyboard()


def render_more(profile: dict) -> tuple[str, list[list[dict]]]:
    return QUESTIONS["sources"], keyboard("sources", profile, "edit")


def render_format_region() -> tuple[str, list[list[dict]]]:
    return "<b>Формат и регион</b>\n\nЧто изменить?", format_region_keyboard()


def render_reset() -> tuple[str, list[list[dict]]]:
    return (
        "<b>Сбросить все фильтры?</b>\n\nПосле этого настроим подборку заново.",
        [[_button("Сбросить", "reset:confirm"), _button("Назад", "reset:cancel")]],
    )


# --------------------------------------------------------------------------
# применение нажатий
# --------------------------------------------------------------------------

CLEARS = {
    "role": ["roles"],
    "grade": ["grades"],
    "salary": ["salary_min", "salary_max"],
    "format": ["formats"],
    "region": ["regions"],
    "sources": ["sources"],
}


def _toggle(items: list, value: str) -> None:
    if value in items:
        items.remove(value)
    else:
        items.append(value)


def skip(step: str, profile: dict) -> None:
    """«Любая»/«Любой» — стереть критерий этого шага."""
    if step == "role":
        profile["roles"] = [
            f"{direction}.{role}"
            for direction in prof.directions_of(profile)
            for role in taxonomy.roles()[direction]["roles"]
        ]
        return
    for field in CLEARS.get(step, []):
        profile[field] = None if field.startswith("salary") else []


def apply(data: str, user: dict) -> str | None:
    """Применяем нажатие к профилю. Переходы между шагами делает вызывающий."""
    profile = user["profile"]

    if data == "all:role":
        skip("role", profile)
    elif data.startswith("td:"):
        # Направления храним отдельно, чтобы выбор роли был самостоятельным шагом.
        direction = data[3:]
        directions = prof.directions_of(profile)
        roles = profile.setdefault("roles", [])
        if direction in directions:
            directions.remove(direction)
            profile["roles"] = [r for r in roles if not r.startswith(f"{direction}.")]
        else:
            directions.append(direction)
        profile["directions"] = directions
    elif data.startswith("trg:"):
        _toggle_from_all(profile, "regions", data[4:], [v for v, _ in prof.REGIONS])
    elif data.startswith("tsrc:"):
        _toggle_from_all(profile, "sources", data[5:], _all_sources())
    elif data.startswith("tsrcgrp:"):
        group = SOURCE_GROUPS.get(data[8:])
        if group:
            available = [key for key in group["sources"] if key in _all_sources()]
            current = profile.get("sources") or _all_sources()
            if all(key in current for key in available):
                updated = [key for key in current if key not in available]
                if not updated:
                    return "Оставь включённой хотя бы одну группу источников."
                profile["sources"] = updated
            else:
                updated = list(dict.fromkeys(current + available))
                profile["sources"] = [] if set(updated) == set(_all_sources()) else updated
    elif data.startswith("tr:"):
        _toggle(profile.setdefault("roles", []), data[3:])
    elif data.startswith("tg:"):
        _toggle(profile.setdefault("grades", []), data[3:])
    elif data.startswith("tf:"):
        _toggle(profile.setdefault("formats", []), data[3:])
    elif data.startswith("ts:"):
        # Повторное нажатие снимает порог: иначе выбранное не отменить,
        # не сбрасывая шаг целиком.
        value = int(data[3:])
        profile["salary_min"] = None if profile.get("salary_min") == value else value
        profile["salary_max"] = None
    elif data.startswith("sk:"):
        skip(data[3:], profile)
    return None
