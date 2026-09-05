"""Мастер настройки: тексты, кнопки и переходы между шагами.

Весь диалог — одна карточка, которая переписывается на месте. Человек видит
не простыню из десяти сообщений, а один экран, который меняется под нажатия.

Кнопка «Любая»/«Любой» стирает критерий и ведёт дальше: незаполненное поле
ничего не отсекает. У грейда её нет — там выбор обязателен, потому что
неназванный уровень был главным источником нерелевантных вакансий.
"""

from __future__ import annotations

from finderjob import profile as prof
from finderjob import taxonomy

STEPS = ["direction", "role", "grade", "salary", "format"]

# Слово согласуется с существительным шага: «Зарплата — любая», но
# «Формат работы — любой». У grade и direction кнопки нет вовсе.
SKIP_LABEL = {
    "role": "Все роли",
    "salary": "Любая",
    "format": "Любой",
    "region": "Любой",
    "sources": "Все",
}

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
    "каждый день в 9 утра по Москве.\n\n"
    "Поменять критерии — /settings."
)

EMPTY_POOL = (
    "Критерии сохранены.\n\n"
    "Свежих вакансий пока нет под рукой — первая подборка придёт "
    "завтра в 9 утра по Москве."
)

HELP = (
    "<b>FinderJob</b> присылает ежедневный дайджест вакансий для дизайнеров.\n\n"
    "/start — начать заново\n"
    "/settings — посмотреть и поменять критерии\n"
    "/stop — отписаться от дайджеста\n\n"
    "Вакансии беру с hirehi, hh.ru, Хабр Карьеры, getmatch, careerspace, "
    "designer.ru и карьерных сайтов компаний. Профессию выбираешь сам — "
    "дизайн, контент, разработка или продукт."
)

# Пороги зарплаты. Нижняя граница, а не вилка: верхняя отсекала бы и тех,
# кто платит больше потолка, — на этом уже обжигались.
SALARY_STEPS = [100_000, 150_000, 200_000, 250_000]

QUESTIONS = {
    "direction": "<b>Шаг 1 из 5. Направление</b>\n\nЧем занимаешься.",
    "role": "<b>Шаг 2 из 5. Роль</b>",
    "grade": "<b>Шаг 3 из 5. Грейд</b>",
    "salary": (
        "<b>Шаг 4 из 5. Зарплата</b>\n\n"
        "Нижняя граница. Вакансии без указанной вилки не отсекаю — "
        "их большинство."
    ),
    "format": "<b>Шаг 5 из 5. Формат работы</b>",
    "region": (
        "<b>Регион</b>\n\n"
        "Где готов ездить в офис. На удалённые вакансии не влияет."
    ),
    "sources": "<b>Источники</b>\n\nОткуда брать вакансии.",
}


def _button(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _rows(buttons: list[dict], per_row: int = 2) -> list[list[dict]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


# --------------------------------------------------------------------------
# переходы между шагами
# --------------------------------------------------------------------------


def next_step(step: str, profile: dict) -> str | None:
    order = STEPS[STEPS.index(step) + 1 :] if step in STEPS else []
    return order[0] if order else None


def prev_step(step: str, profile: dict) -> str | None:
    if step not in STEPS:
        return None
    order = STEPS[: STEPS.index(step)]
    return order[-1] if order else None


# --------------------------------------------------------------------------
# клавиатуры
# --------------------------------------------------------------------------


def _tail(step: str, profile: dict, mode: str) -> list[list[dict]]:
    """Хвост любого шага: пропустить, назад и далее.

    «Далее» появляется только когда на шаге что-то выбрано: на пустом шаге
    оно означало бы ровно то же, что «Неважно».

    На первом шаге назад некуда. В режиме правки из /settings назад ведёт
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
        return _rows(buttons) + _tail(step, profile, mode)

    if step == "region":
        chosen = profile.get("regions") or []
        buttons = [
            _button(f"{CHECK if value in chosen else ''}{label}", f"trg:{value}")
            for value, label in prof.REGIONS
        ]
        return _rows(buttons) + _tail(step, profile, mode)

    if step == "sources":
        # Рядом с каждой площадкой — что она даст именно по твоим профессиям.
        # Без этого можно выбрать фронтенд вместе с designer.ru и ждать
        # вакансий, которых оттуда не будет никогда.
        chosen = profile.get("sources") or []
        fits = taxonomy.coverage(prof.roles_of(profile))
        buttons = []
        for key, body in taxonomy.sources().items():
            if body.get("status") != "connected":
                continue
            covers = fits.get(key) or []
            mark = CHECK if key in chosen else ""
            note = "" if covers else " (не даёт)"
            buttons.append(_button(f"{mark}{body['label']}{note}", f"tsrc:{key}"))
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
    """Мастер отвечает на «кого ищем», фильтры — на «где и откуда».
    Регион и источники в мастер не выносим: их тронут единицы, а каждый
    лишний шаг теряет людей на входе."""
    return [
        [_button("Направление", "ed:direction"), _button("Роль", "ed:role")],
        [_button("Грейд", "ed:grade"), _button("Зарплата", "ed:salary")],
        [_button("Формат работы", "ed:format")],
        [_button("Регион", "ed:region"), _button("Источники", "ed:sources")],
        [_button("Сбросить все фильтры", "go")],
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
    """«Любая»/«Любой» — стереть критерий этого шага.

    На шаге ролей это значит «все роли выбранных направлений»: пустой
    список ролей означал бы поиск вообще без профессии.
    """
    if step == "role":
        profile["roles"] = [
            f"{direction}.{role}"
            for direction in prof.directions_of(profile)
            for role in taxonomy.roles()[direction]["roles"]
        ]
        return
    for field in CLEARS.get(step, []):
        profile[field] = None if field.startswith("salary") else []


def apply(data: str, user: dict) -> None:
    """Применяем нажатие к профилю. Переходы между шагами делает вызывающий."""
    profile = user["profile"]

    if data.startswith("td:"):
        # Направление хранится не отдельно, а через роли: сняли направление —
        # ушли и его роли, иначе выбор разъезжается сам с собой.
        direction = data[3:]
        roles = profile.setdefault("roles", [])
        mine = [r for r in roles if r.startswith(f"{direction}.")]
        if mine:
            for role in mine:
                roles.remove(role)
        else:
            roles.extend(
                f"{direction}.{role}" for role in taxonomy.roles()[direction]["roles"]
            )
    elif data.startswith("trg:"):
        _toggle(profile.setdefault("regions", []), data[4:])
    elif data.startswith("tsrc:"):
        _toggle(profile.setdefault("sources", []), data[5:])
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
