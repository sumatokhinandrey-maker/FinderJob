"""Мастер настройки: тексты, кнопки и переходы между шагами.

Весь диалог — одна карточка, которая переписывается на месте. Человек видит
не простыню из десяти сообщений, а один экран, который меняется под нажатия.

«Пропустить» на любом шаге стирает критерий и ведёт дальше: незаполненное
поле ничего не отсекает.
"""

from __future__ import annotations

from finderjob import profile as prof

STEPS = ["grade", "salary", "format", "cities"]

CHECK = "✓ "  # галочка перед выбранным пунктом

# Описание бота — то, что видно в пустом чате до кнопки «Начать».
# Ставится через setup_bot.py, здесь лежит как источник правды.
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

ASK_SALARY = (
    "<b>Шаг 2 из 4. Зарплата</b>\n\n"
    "Напиши вилку одним сообщением. Понимаю по-разному:\n"
    "150000-250000, от 150 до 250 тысяч, 150к, просто 200000."
)

ASK_CITIES = (
    "<b>Шаг 4 из 4. Город</b>\n\n"
    "Напиши город или несколько через запятую. Например: Москва, Казань."
)

BAD_SALARY = "Не нашёл в этом чисел. Напиши вилку цифрами, например 150000-250000."
BAD_CITIES = "Не разобрал ни одного города. Напиши названия через запятую."

QUESTIONS = {
    "grade": "<b>Шаг 1 из 4. Грейд</b>",
    "salary": (
        "<b>Шаг 2 из 4. Зарплата</b>\n\n"
        "Вакансии без указанной вилки я пропускаю в любом случае — "
        "её указывает примерно каждая десятая."
    ),
    "format": "<b>Шаг 3 из 4. Формат работы</b>",
    "cities": (
        "<b>Шаг 4 из 4. Город</b>\n\n"
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
    """Шаг вперёд. Про место спрашиваем только тех, кто готов куда-то ездить."""
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


def _tail(step: str, profile: dict, mode: str) -> list[list[dict]]:
    """Хвост любого шага: пропустить, назад и далее.

    На первом шаге назад некуда. В режиме правки из /settings назад ведёт
    в список настроек: человек пришёл поменять один пункт, а не проходить
    мастер заново.
    """
    nav = []
    if mode == "edit" or prev_step(step, profile):
        nav.append(_button("Назад", f"bk:{step}"))
    nav.append(_button("Сохранить" if mode == "edit" else "Далее", f"nx:{step}"))
    return [[_button("Пропустить", f"sk:{step}")], nav]


def keyboard(step: str, profile: dict, mode: str) -> list[list[dict]]:
    if step == "grade":
        chosen = profile.get("grades") or []
        buttons = [
            _button(f"{CHECK if value in chosen else ''}{label}", f"tg:{value}")
            for value, label in prof.GRADES
        ]
        return _rows(buttons) + _tail(step, profile, mode)

    if step == "salary":
        current = prof.describe_salary(profile)
        label = "Значение от — до" if current == prof.ANY else f"{CHECK}{current}"
        return [[_button(label, "in:salary")]] + _tail(step, profile, mode)

    if step == "format":
        chosen = profile.get("formats") or []
        buttons = [
            _button(f"{CHECK if value in chosen else ''}{label}", f"tf:{value}")
            for value, label in prof.FORMATS
        ]
        return _rows(buttons, 3) + _tail(step, profile, mode)

    if step == "cities":
        picked = profile.get("places") or []
        rows = _rows(
            [
                _button(f"{CHECK if value in picked else ''}{label}", f"tp:{value}")
                for value, label in prof.PLACES
            ]
        )
        # Города, введённые руками, показываем отдельными кнопками —
        # так их видно и так же легко снять.
        own = profile.get("cities") or []
        rows += _rows([_button(f"{CHECK}{name}", f"tc:{name}") for name in own[:6]])
        rows += [[_button("Свой город(а)", "in:cities")]]
        return rows + _tail(step, profile, mode)

    return []


def settings_keyboard() -> list[list[dict]]:
    return [
        [_button("Грейд", "ed:grade"), _button("Зарплата", "ed:salary")],
        [_button("Формат работы", "ed:format"), _button("Город", "ed:cities")],
        [_button("Задать заново", "go")],
    ]


def done_keyboard() -> list[list[dict]]:
    return [[_button("Поменять критерии", "ed:menu")]]


def input_keyboard(step: str) -> list[list[dict]]:
    """Из ввода текста надо уметь выйти, ничего не написав."""
    return [[_button("Назад", f"ca:{step}")]]


def render(step: str, profile: dict, mode: str) -> tuple[str, list[list[dict]]]:
    return QUESTIONS[step], keyboard(step, profile, mode)


def render_settings(profile: dict) -> tuple[str, list[list[dict]]]:
    return prof.summary(profile) + "\n\nЧто поменять?", settings_keyboard()


# --------------------------------------------------------------------------
# применение нажатий
# --------------------------------------------------------------------------

CLEARS = {
    "grade": ["grades"],
    "salary": ["salary_min", "salary_max"],
    "format": ["formats"],
    "cities": ["places", "cities"],
}


def _toggle(items: list, value: str) -> None:
    if value in items:
        items.remove(value)
    else:
        items.append(value)


def skip(step: str, profile: dict) -> None:
    """«Пропустить» — стереть критерий этого шага."""
    for field in CLEARS.get(step, []):
        profile[field] = None if field.startswith("salary") else []


def apply(data: str, user: dict) -> None:
    """Применяем нажатие к профилю. Переходы между шагами делает вызывающий."""
    profile = user["profile"]

    if data.startswith("tg:"):
        _toggle(profile.setdefault("grades", []), data[3:])
    elif data.startswith("tf:"):
        _toggle(profile.setdefault("formats", []), data[3:])
    elif data.startswith("tp:"):
        _toggle(profile.setdefault("places", []), data[3:])
    elif data.startswith("tc:"):
        _toggle(profile.setdefault("cities", []), data[3:])
    elif data.startswith("sk:"):
        skip(data[3:], profile)
