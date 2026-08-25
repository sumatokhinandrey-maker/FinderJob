"""Мастер настройки: тексты, кнопки и переходы между шагами.

Весь диалог — одно сообщение, которое переписывается на месте. Человек видит
не простыню из десяти карточек, а одну, которая меняется под его нажатия.
"""

from __future__ import annotations

from finderjob import profile as prof

STEPS = ["grade", "salary", "format", "cities"]

CHECK = "✓ "  # галочка перед выбранным пунктом

# Описание бота — то, что видно в пустом чате до нажатия «Начать».
# Ставится один раз через setMyDescription, здесь лежит как источник правды.
DESCRIPTION = "Твой персональный дайджест актуальных вакансий на каждый день"

COMMANDS = [
    ("start", "перезапустить бота"),
    ("settings", "открыть фильтры"),
]

DONE = (
    "Критерии сохранены. Дальше дайджест будет приходить сам, "
    "каждый день в 7 утра по Москве.\n\n"
    "Поменять критерии — /settings."
)

EMPTY_POOL = (
    "Критерии сохранены.\n\n"
    "Свежих вакансий пока нет под рукой — первая подборка придёт "
    "завтра в 7 утра по Москве."
)

HELP = (
    "<b>FinderJob</b> присылает ежедневный дайджест вакансий для дизайнеров.\n\n"
    "/start — начать заново\n"
    "/settings — посмотреть и поменять критерии\n"
    "/stop — отписаться от дайджеста\n\n"
    "Вакансии беру с hirehi.ru. Роль фиксированная — продуктовый, UX/UI "
    "и веб-дизайн; графику, моушн и 3D отсекаю."
)

ASK_CITY = (
    "<b>Шаг 4 из 4. Города</b>\n\n"
    "Напиши название города одним сообщением. Например: Казань."
)


def ask_city_keyboard() -> list[list[dict]]:
    """Из ввода города надо уметь выйти, ничего не написав."""
    return [[_button("Назад", "tc:cancel")]]

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
# переходы между шагами
# --------------------------------------------------------------------------


def next_step(step: str, profile: dict) -> str | None:
    """Шаг вперёд. Про города спрашиваем только тех, кто готов ездить, —
    остальным этот вопрос ничего не даёт."""
    for candidate in STEPS[STEPS.index(step) + 1 :]:
        if candidate == "cities" and not prof.needs_cities(profile):
            continue
        return candidate
    return None


def prev_step(step: str, profile: dict) -> str | None:
    for candidate in reversed(STEPS[: STEPS.index(step)]):
        if candidate == "cities" and not prof.needs_cities(profile):
            continue
        return candidate
    return None


# --------------------------------------------------------------------------
# клавиатуры
# --------------------------------------------------------------------------


def _nav(step: str, profile: dict, mode: str) -> list[dict]:
    """Нижний ряд: назад и вперёд. На первом шаге назад некуда,
    в режиме правки назад ведёт в список настроек."""
    row = []
    if mode == "edit" or prev_step(step, profile):
        row.append(_button("Назад", f"bk:{step}"))
    row.append(_button("Сохранить" if mode == "edit" else "Дальше", f"nx:{step}"))
    return row


def keyboard(step: str, profile: dict, mode: str) -> list[list[dict]]:
    if step == "grade":
        chosen = profile.get("grades") or []
        buttons = [
            _button(f"{CHECK if value in chosen else ''}{label}", f"tg:{value}")
            for value, label in prof.GRADES
        ]
        return (
            _rows(buttons)
            + [[_button(f"{CHECK if not chosen else ''}Не важно", "ta:g")]]
            + [_nav(step, profile, mode)]
        )

    if step == "salary":
        current = profile.get("salary_min")
        buttons = [
            _button(f"{CHECK if current == value else ''}{label}", f"sl:{value}")
            for value, label in prof.SALARIES
        ]
        return (
            _rows(buttons)
            + [[_button(f"{CHECK if not current else ''}Не важно", "sl:any")]]
            + [_nav(step, profile, mode)]
        )

    if step == "format":
        chosen = profile.get("formats") or []
        buttons = [
            _button(f"{CHECK if value in chosen else ''}{label}", f"tf:{value}")
            for value, label in prof.FORMATS
        ]
        return (
            _rows(buttons, 3)
            + [[_button(f"{CHECK if not chosen else ''}Не важно", "ta:f")]]
            + [_nav(step, profile, mode)]
        )

    if step == "cities":
        any_city = profile.get("any_city")
        chosen = profile.get("cities") or []
        # Москва и Питер — быстрые кнопки, остальные города человек вводит сам:
        # зашивать список городов России в клавиатуру бессмысленно.
        quick = [
            _button(
                f"{CHECK if not any_city and name in chosen else ''}{name}",
                f"tc:{index}",
            )
            for index, name in enumerate(prof.QUICK_CITIES)
        ]
        extra = [name for name in chosen if name not in prof.QUICK_CITIES]
        typed = [
            _button(f"{CHECK}{name}", f"tx:{name[:40]}") for name in extra[:4]
        ]
        return (
            _rows(quick)
            + _rows(typed)
            + [[_button("Ввести город", "tc:input")]]
            + [[_button(f"{CHECK if any_city else ''}Не важно", "tc:any")]]
            + [_nav(step, profile, mode)]
        )

    return []


def settings_keyboard() -> list[list[dict]]:
    return [
        [_button("Грейд", "ed:grade"), _button("Зарплата", "ed:salary")],
        [_button("Формат работы", "ed:format"), _button("Города", "ed:cities")],
        [_button("Задать заново", "go")],
    ]


def done_keyboard() -> list[list[dict]]:
    return [[_button("Поменять критерии", "ed:menu")]]


def render(step: str, profile: dict, mode: str) -> tuple[str, list[list[dict]]]:
    return QUESTIONS[step], keyboard(step, profile, mode)


def render_settings(profile: dict) -> tuple[str, list[list[dict]]]:
    return prof.summary(profile) + "\n\nЧто поменять?", settings_keyboard()


# --------------------------------------------------------------------------
# применение нажатий
# --------------------------------------------------------------------------


def _toggle(items: list, value: str) -> None:
    if value in items:
        items.remove(value)
    else:
        items.append(value)


def apply(data: str, user: dict) -> None:
    """Применяем нажатие к профилю. Переходы между шагами делает вызывающий."""
    profile = user["profile"]

    if data.startswith("tg:"):
        _toggle(profile.setdefault("grades", []), data[3:])

    elif data == "ta:g":
        profile["grades"] = []

    elif data.startswith("sl:"):
        value = data[3:]
        profile["salary_min"] = None if value == "any" else int(value)

    elif data.startswith("tf:"):
        _toggle(profile.setdefault("formats", []), data[3:])

    elif data == "ta:f":
        profile["formats"] = []

    elif data == "tc:any":
        profile["any_city"] = True
        profile["cities"] = []

    elif data.startswith("tc:"):
        profile["any_city"] = False
        _toggle(profile.setdefault("cities", []), prof.QUICK_CITIES[int(data[3:])])
        _fallback_to_any(profile)

    elif data.startswith("tx:"):
        profile["any_city"] = False
        _toggle(profile.setdefault("cities", []), data[3:])
        _fallback_to_any(profile)


def add_city(profile: dict, name: str) -> str:
    """Город, введённый руками. Возвращает то, что записали."""
    name = " ".join(name.split())[:40].strip("-—,. ")
    name = name[:1].upper() + name[1:]
    cities = profile.setdefault("cities", [])
    if name not in cities:
        cities.append(name)
    profile["any_city"] = False
    return name


def _fallback_to_any(profile: dict) -> None:
    """Человек снял все города — это снова «не важно», иначе он остался бы
    вообще без вакансий с офисом."""
    if not profile.get("cities"):
        profile["any_city"] = True
