"""Мастер настройки: тексты, кнопки и переходы между шагами.

Весь диалог — одно сообщение, которое переписывается на месте. Человек видит
не простыню из десяти карточек, а одну, которая меняется под его нажатия.
"""

from __future__ import annotations

from finderjob import profile as prof

STEPS = ["grade", "salary", "format", "cities"]

CITY_CODES = {"msk": "Москва", "spb": "Санкт-Петербург"}

CHECK = "✓ "  # галочка перед выбранным пунктом


START = (
    "<b>FinderJob</b>\n"
    "Твой персональный дайджест актуальных вакансий на каждый день."
)

DONE = (
    "Критерии сохранены. Дальше дайджест будет приходить сам, "
    "каждый день в 7 утра по Москве.\n\n"
    "Поменять критерии — /settings."
)

# Показываем, пока подбираем первую подборку: обращение к хранилищу и отправка
# занимают секунду-другую, и без этой строки кажется, что бот завис.
LOOKING = "Собираю подборку на сегодня…"

EMPTY_POOL = (
    "Критерии сохранены.\n\n"
    "Свежих вакансий пока нет под рукой — первая подборка придёт "
    "завтра в 7 утра по Москве."
)

HELP = (
    "<b>FinderJob</b> присылает ежедневный дайджест вакансий для дизайнеров.\n\n"
    "/settings — посмотреть и поменять критерии\n"
    "/stop — отписаться от дайджеста\n\n"
    "Вакансии беру с hirehi.ru. Роль фиксированная — продуктовый, UX/UI "
    "и веб-дизайн; графику, моушн и 3D отсекаю."
)

QUESTIONS = {
    "grade": "<b>Шаг 1 из 4. Грейд</b>",
    "salary": (
        "<b>Шаг 2 из 4. Зарплата</b>\n\n"
        "Нижняя граница. Вакансии без указанной вилки я пропускаю — "
        "её указывает примерно каждая десятая."
    ),
    "format": "<b>Шаг 3 из 4. Формат работы</b>",
    "cities": (
        "<b>Шаг 4 из 4. Города</b>\n\n"
        "Где готов ездить в офис. На удалённые вакансии это не влияет."
    ),
}


def _button(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _rows(buttons: list[dict], per_row: int = 2) -> list[list[dict]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


# --------------------------------------------------------------------------
# клавиатуры шагов
# --------------------------------------------------------------------------


def keyboard(step: str, profile: dict, mode: str) -> list[list[dict]]:
    tail = "Сохранить" if mode == "edit" else "Дальше"

    if step == "grade":
        chosen = profile.get("grades") or []
        buttons = [
            _button(f"{CHECK if value in chosen else ''}{label}", f"tg:{value}")
            for value, label in prof.GRADES
        ]
        return _rows(buttons) + [
            [
                _button(f"{CHECK if not chosen else ''}Не важно", "ta:g"),
                _button(tail, "nx:grade"),
            ]
        ]

    if step == "salary":
        current = profile.get("salary_min")
        buttons = [
            _button(f"{CHECK if current == value else ''}{label}", f"sl:{value}")
            for value, label in prof.SALARIES
        ]
        return _rows(buttons) + [
            [_button(f"{CHECK if not current else ''}Не важно", "sl:any")]
        ]

    if step == "format":
        chosen = profile.get("formats") or []
        buttons = [
            _button(f"{CHECK if value in chosen else ''}{label}", f"tf:{value}")
            for value, label in prof.FORMATS
        ]
        return _rows(buttons, 3) + [
            [
                _button(f"{CHECK if not chosen else ''}Не важно", "ta:f"),
                _button(tail, "nx:format"),
            ]
        ]

    if step == "cities":
        any_city = profile.get("any_city")
        chosen = profile.get("cities") or []
        buttons = [
            _button(
                f"{CHECK if not any_city and name in chosen else ''}{name}",
                f"tc:{code}",
            )
            for code, name in CITY_CODES.items()
        ]
        buttons.append(
            _button(
                f"{CHECK if not any_city and profile.get('abroad') else ''}За границей",
                "tc:abroad",
            )
        )
        return _rows(buttons) + [
            [
                _button(f"{CHECK if any_city else ''}Любой город", "tc:any"),
                _button(tail, "nx:cities"),
            ]
        ]

    return []


def settings_keyboard() -> list[list[dict]]:
    return [
        [_button("Грейд", "ed:grade"), _button("Зарплата", "ed:salary")],
        [_button("Формат работы", "ed:format"), _button("Города", "ed:cities")],
        [_button("Задать заново", "go")],
    ]


def start_keyboard() -> list[list[dict]]:
    return [[_button("Задать критерии", "go")]]


def done_keyboard() -> list[list[dict]]:
    return [[_button("Поменять критерии", "ed:menu")]]


# --------------------------------------------------------------------------
# переходы
# --------------------------------------------------------------------------


def next_step(step: str, profile: dict) -> str | None:
    """Следующий шаг мастера. Про города спрашиваем только тех, кто готов
    ездить, — остальным этот вопрос ничего не даёт."""
    index = STEPS.index(step)
    for candidate in STEPS[index + 1 :]:
        if candidate == "cities" and not prof.needs_cities(profile):
            continue
        return candidate
    return None


def render(step: str, profile: dict, mode: str) -> tuple[str, list[list[dict]]]:
    return QUESTIONS[step], keyboard(step, profile, mode)


def render_settings(profile: dict) -> tuple[str, list[list[dict]]]:
    return (
        prof.summary(profile) + "\n\nЧто поменять?",
        settings_keyboard(),
    )


def apply(data: str, user: dict) -> str | None:
    """Применяем нажатие к профилю. Возвращаем короткий ответ для всплывашки
    или None. Сами переходы между шагами делает вызывающий код."""
    profile = user["profile"]

    if data.startswith("tg:"):
        value = data[3:]
        grades = profile.setdefault("grades", [])
        if value in grades:
            grades.remove(value)
        else:
            grades.append(value)
        return None

    if data == "ta:g":
        profile["grades"] = []
        return None

    if data.startswith("sl:"):
        value = data[3:]
        profile["salary_min"] = None if value == "any" else int(value)
        return None

    if data.startswith("tf:"):
        value = data[3:]
        formats = profile.setdefault("formats", [])
        if value in formats:
            formats.remove(value)
        else:
            formats.append(value)
        return None

    if data == "ta:f":
        profile["formats"] = []
        return None

    if data.startswith("tc:"):
        code = data[3:]
        if code == "any":
            profile["any_city"] = True
            profile["cities"] = []
            profile["abroad"] = False
            return None
        profile["any_city"] = False
        if code == "abroad":
            profile["abroad"] = not profile.get("abroad")
        else:
            name = CITY_CODES[code]
            cities = profile.setdefault("cities", [])
            if name in cities:
                cities.remove(name)
            else:
                cities.append(name)
        # Человек снял всё до последнего — это снова «любой город»,
        # иначе он остался бы вообще без вакансий с офисом.
        if not profile.get("cities") and not profile.get("abroad"):
            profile["any_city"] = True
        return None

    return None
