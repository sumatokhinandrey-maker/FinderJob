"""Модель вакансии, фильтр под профиль, память о показанном и сборка дайджеста."""

from __future__ import annotations

import json
import re
from html import unescape
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path

from finderjob import geo, taxonomy

ROOT = Path(__file__).parent


@dataclass
class Vacancy:
    id: str
    source: str
    title: str
    company: str
    grade: str | None
    salary: int | None
    work_format: str  # remote | hybrid | office | unknown
    city: str | None
    country: str | None
    url: str
    posted_at: str
    experience_months: int | None
    description: str
    skills: str

    def haystack(self) -> str:
        """Всё текстовое одной строкой — по нему ищем стоп- и ключевые слова."""
        return " ".join(
            [self.title, self.company, self.description, self.skills]
        ).lower()


# --------------------------------------------------------------------------
# грейд из заголовка
# --------------------------------------------------------------------------

# Часть источников грейд отдельным полем не отдаёт вовсе — карьерные сайты
# компаний, например. Раньше такие вакансии считались «грейд неизвестен»
# и проходили любой фильтр насквозь: человеку, выбравшему стажировки,
# прилетал «Ведущий UX/UI-дизайнер». Заголовок про грейд почти всегда
# говорит прямо, поэтому вычитываем его оттуда.
TITLE_GRADES = [
    (re.compile(r"\b(intern|trainee|стажёр|стажер|стажир\w*)\b", re.I), "intern"),
    (re.compile(r"\b(junior|джуниор|джун|младший)\b", re.I), "junior"),
    (re.compile(r"\b(middle|мидл|миддл)\b", re.I), "middle"),
    (re.compile(r"\b(senior|сеньор|синьор|старший|страший)\b", re.I), "senior"),
    (re.compile(
        r"\b(lead|head|principal|staff|тимлид|ведущий|главный|руководитель|директор)\b",
        re.I,
    ), "lead"),
]


def grade_from_title(title: str) -> str | None:
    """Грейд, названный в заголовке. Ничего не нашли — None, и вакансия
    по-прежнему проходит фильтр: угадывать по описанию мы не беремся."""
    found = [name for pattern, name in TITLE_GRADES if pattern.search(title or "")]
    return ",".join(found) or None


def fill_grades(vacancies: list[Vacancy]) -> list[Vacancy]:
    """Грейд из заголовка. Он главнее поля источника, а не только затыкает
    пустоту.

    Замер 04.09.2026 по 449 вакансиям: поле и заголовок разошлись шесть раз,
    и все шесть — в пользу заголовка. «Staff Designer» у Сбера getmatch отдал
    как middle, «Ведущий дизайнер продукта» у VK — как senior; человек с
    фильтром «мидл» получал вакансии на лида. Обратное расхождение —
    когда поле точнее заголовка — не встретилось ни разу.

    Отдельно чинится случай «Middle+/Senior UX Designer»: источник пишет
    только middle, заголовок называет оба грейда, и сеньор такую вакансию
    не видел."""
    out = []
    for vacancy in vacancies:
        guess = grade_from_title(vacancy.title)
        if guess and guess != vacancy.grade:
            vacancy = replace(vacancy, grade=guess)
        out.append(vacancy)
    return out


# --------------------------------------------------------------------------
# фильтр
# --------------------------------------------------------------------------

# Причины отсева. В сообщение не идут — человеку они не нужны;
# остаются в логах рассылки, чтобы было видно, как работают фильтры.
REASONS = {
    "grade": "грейд",
    "grade_unknown": "грейд не указан",
    "role": "не та роль",
    "source": "другая площадка",
    "stop": "стоп-слово",
    "format": "формат работы",
    "salary": "деньги",
}


# Грейды «входа в профессию»: для них действует обратное правило про
# неизвестный грейд — см. check().
ENTRY_GRADES = {"intern", "junior"}


# Карьерные сайты компаний: отклик идёт напрямую работодателю, без посредников
# и регистраций, поэтому при склейке дублей они выигрывают у всех.
COMPANY_SITES = {"yandex", "magnit", "mts", "tbank", "rwb", "vk", "sberdevices"}

# Площадки, которые приходят только тем, кто выбрал их сам. Обычная площадка
# при пустом списке источников считается подходящей, но портал Skillbox
# рассматривает отклик лишь с резюме по их шаблону: человеку со стороны такая
# вакансия — обещание, которое бот выполнить не может.
OPT_IN_SOURCES = {"friendwork"}


def _place_allowed(vacancy: Vacancy, rules: dict) -> bool:
    """Регион подходит? Касается только гибрида и офиса — на удалёнку не влияет.

    Три корзины: Россия, СНГ, зарубежные. Пусто — шаг пропущен, подходит всё.
    """
    regions = rules.get("regions") or []
    if not regions:
        return True

    if not vacancy.city:
        # Город не указан вовсе — не угадываем, решать будет человек.
        return True

    where = geo.where(vacancy.city)
    if where is None:
        # Незнакомый город: в какой он корзине, мы не знаем, поэтому
        # не отсекаем. Лучше лишняя строка, чем потерянная вакансия.
        return True
    return where in regions


def check(vacancy: Vacancy, profile: dict) -> str | None:
    """None — вакансия подходит. Иначе код причины отсева."""
    text = vacancy.haystack()

    # У geekjob вакансия может быть сразу на несколько грейдов
    # («Миддл • Сеньор»), поэтому храним их через запятую и сверяем пересечение.
    grades = profile.get("grades") or []
    if grades:
        if vacancy.grade:
            if not set(vacancy.grade.split(",")) & set(grades):
                return "grade"

    # Роль ищем по заголовку, а не по всему тексту: описание рекрутёра,
    # фронтендера или геймдев-программиста тоже упоминает «UI»/«UX»/
    # «интерфейс» мимоходом, и по haystack такая вакансия ложно проходила.
    #
    # У каждой профессии свои правила (roles.json), поэтому «графический
    # дизайн» больше не глобальное стоп-слово, а признак другой роли:
    # кто выбрал графдизайн — тот его и получит.
    roles = profile.get("roles") or []
    if roles and not taxonomy.match(vacancy.title, roles):
        return "role"

    # Пустой список площадок значит «подходят все». Обход при этом остаётся
    # общим: вакансии собираются один раз на всех подписчиков, а выбор
    # источника — это то, что человек готов видеть у себя в подборке.
    sources = profile.get("sources") or []
    if vacancy.source in OPT_IN_SOURCES and vacancy.source not in sources:
        return "source"
    if sources:
        # Карьерные сайты выбираются одной кнопкой «Сайты компаний»,
        # а в вакансии стоит конкретная площадка: yandex, vk, mts.
        allowed = set(sources)
        if "companies" in allowed:
            allowed |= COMPANY_SITES
        if vacancy.source not in allowed:
            return "source"

    formats = profile.get("work_formats", {})
    if vacancy.work_format == "remote" and not formats.get("remote", True):
        return "format"
    if vacancy.work_format in ("hybrid", "office"):
        if not formats.get(vacancy.work_format, True):
            return "format"
        if not _place_allowed(vacancy, formats):
            return "format"

    # Вилку указывает примерно каждая десятая вакансия. У остальных
    # salary is None, и они проходят — иначе отсечётся почти всё.
    if vacancy.salary is not None:
        minimum = profile.get("salary_min")
        maximum = profile.get("salary_max")
        if minimum and vacancy.salary < minimum:
            return "salary"
        if maximum and vacancy.salary > maximum:
            return "salary"


    # Общее правило «грейд не назван — вакансия проходит» верно для мидлов
    # и сеньоров: безымянная масса как раз их уровня. Для входа в профессию
    # оно переворачивается и топит человека. Замер 28.08.2026 по профилю
    # «только стажёр»: проходило 17 вакансий, из них 16 без грейда и с вилками
    # под мидла, и лишь одна настоящая стажировка. Распознать уровень нечем:
    # опыт у безгрейдовых не указан ни разу (0 из 121), вилка есть у трети.
    #
    # Проверка стоит последней нарочно: так в счётчик «скрыто» попадают
    # только те вакансии, которые прошли всё остальное. Стой она первой,
    # человеку показали бы число вчетверо больше — вместе с теми, что
    # всё равно отсеялись бы по роли.
    if grades and not vacancy.grade and set(grades) <= ENTRY_GRADES:
        return "grade_unknown"

    return None


def apply_filters(
    vacancies: list[Vacancy], profile: dict
) -> tuple[list[Vacancy], dict[str, int]]:
    passed, rejected = [], {}
    for vacancy in vacancies:
        reason = check(vacancy, profile)
        if reason is None:
            passed.append(vacancy)
        else:
            rejected[reason] = rejected.get(reason, 0) + 1
    return passed, rejected


# --------------------------------------------------------------------------
# память
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# склейка источников
# --------------------------------------------------------------------------

# Грейд в заголовке пишут по-разному, а вакансия одна и та же.
_NOISE_RE = re.compile(
    r"\b(junior|middle|senior|lead|джуниор|мидл|синьор|ведущий|старший|младший)\b",
    re.I,
)


# UX/UI и UI/UX — одно и то же, но для машины разные строки. Пока не свели
# к общему виду, дубли между источниками не склеивались вовсе.
_UXUI_RE = re.compile(r"\bu[ix]\s*[/\-]?\s*u[ix]\b", re.I)


def _fingerprint(vacancy: Vacancy) -> tuple:
    """Отпечаток вакансии: компания, роль и город.

    Город в ключе нужен, чтобы не склеить три разные вакансии одного
    работодателя в Москве, Питере и Краснодаре — это разные места работы,
    а не дубли.
    """
    def flatten(text: str) -> str:
        text = (text or "").lower().replace("ё", "е")
        text = _UXUI_RE.sub(" uxui ", text)
        text = _NOISE_RE.sub(" ", text)
        return re.sub(r"[^a-zа-я0-9]", "", text)

    return flatten(vacancy.company), flatten(vacancy.title), flatten(vacancy.city or "")


# Куда человек может откликнуться сразу, а где упрётся в стену. На hirehi
# отклик закрыт регистрацией и лимитом ради подписки, поэтому при равных
# данных ведём на другую площадку.
OPENNESS = {
    # Сайт самой компании — лучшее, куда можно привести: отклик напрямую.
    **{key: 0 for key in ("yandex", "magnit", "mts", "tbank", "rwb", "vk", "sberdevices")},
    "hh": 1,
    "habr": 1,
    "careerspace": 1,
    "getmatch": 1,
    "designer": 1,
    "friendwork": 1,
    "hirehi": 2,
    "telegram": 1,
}


def _richness(vacancy: Vacancy) -> int:
    return sum(
        1
        for field in (vacancy.salary, vacancy.grade, vacancy.city)
        if field is not None
    ) + (1 if vacancy.work_format != "unknown" else 0)


def dedupe(vacancies: list[Vacancy]) -> list[Vacancy]:
    """Одна вакансия часто висит сразу на нескольких площадках.

    Берём данные оттуда, где их больше, а ссылку — оттуда, откуда человек
    сможет откликнуться. Это разные площадки чаще, чем кажется: hirehi
    подробнее всех, но прячет отклик за регистрацией.
    """
    groups: dict[tuple, list[Vacancy]] = {}
    order: list[tuple] = []
    for vacancy in vacancies:
        key = _fingerprint(vacancy)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(vacancy)

    merged = []
    for key in order:
        items = groups[key]
        best = max(items, key=_richness)
        if len(items) > 1:
            reachable = min(items, key=lambda v: OPENNESS.get(v.source, 1))
            if OPENNESS.get(reachable.source, 1) < OPENNESS.get(best.source, 1):
                # Данные оставляем, ссылку и источник берём у открытой площадки.
                best = replace(best, url=reachable.url, source=reachable.source)
        merged.append(best)
    return merged


def trim_seen(seen: dict, keep_days: int = 90) -> dict:
    """Подчищаем старое, чтобы память пользователя не росла вечно.
    Полгода спустя вакансия всё равно закрыта — показать её повторно не страшно."""
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}


def only_new(vacancies: list[Vacancy], seen: dict) -> list[Vacancy]:
    return [v for v in vacancies if v.id not in seen]


# --------------------------------------------------------------------------
# дайджест
# --------------------------------------------------------------------------

MONTHS = (
    "января февраля марта апреля мая июня июля"
    " августа сентября октября ноября декабря"
).split()

# Как подписываем источник в ссылке. Длинный адрес в дайджесте занимает
# строку и ничего не сообщает — короткая подпись говорит то же самое.
# Подпись ссылки — имя площадки, куда человек попадёт.
SOURCE_LABEL = {
    "hirehi": "hirehi",
    "habr": "habr",
    "careerspace": "careerspace",
    "hh": "hh.ru",
    "getmatch": "getmatch",
    "designer": "designer.ru",
    "friendwork": "Skillbox",
    "yandex": "Yandex",
    "vk": "VK",
    "tbank": "Т-Банк",
    "mts": "МТС",
    "magnit": "Магнит",
    "rwb": "RWB",
    "sberdevices": "SberDevices",
    "telegram": "Telegram",
}


FORMAT_LABEL = {
    "remote": "удалёнка",
    "hybrid": "гибрид",
    "office": "офис",
    "unknown": "формат не указан",
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _line(vacancy: Vacancy) -> str:
    facts = [vacancy.company]

    place = FORMAT_LABEL.get(vacancy.work_format, vacancy.work_format)
    if vacancy.work_format in ("hybrid", "office") and vacancy.city:
        place = f"{place} {vacancy.city}"
    facts.append(place)

    if vacancy.salary:
        facts.append(f"от {vacancy.salary:,} ₽".replace(",", " "))
    if vacancy.grade:
        facts.append(vacancy.grade.replace(",", ", "))

    label = SOURCE_LABEL.get(vacancy.source, vacancy.source)
    link = f'<a href="{_escape(vacancy.url)}">{label} ↗</a>'
    line = _escape(" · ".join(f for f in facts if f))
    return f"▸ <b>{_escape(vacancy.title)}</b>\n   {line} · {link}"


def _grouped(vacancies: list[Vacancy], roles: list[str]) -> str:
    """Вакансии по профессиям, в порядке, в каком человек их выбирал.

    Когда ролей несколько, сплошной список читается как каша: фронтенд
    вперемешку с графдизайном. Заголовок группы отвечает на вопрос
    «почему эта вакансия здесь».
    """
    groups: dict[str, list[Vacancy]] = {}
    for vacancy in vacancies:
        key = taxonomy.match(vacancy.title, roles) or "other"
        groups.setdefault(key, []).append(vacancy)

    if len(groups) < 2:
        return ""          # одна группа — разбивать не на что

    blocks = []
    for key in list(roles) + ["other"]:
        items = groups.get(key)
        if not items:
            continue
        title = taxonomy.label(key) if key != "other" else "Прочее"
        blocks.append(f"<b>{title}</b>\n\n" + "\n\n".join(_line(v) for v in items))
    return "\n\n".join(blocks)


def build_digest(vacancies: list[Vacancy], held_back: int = 0,
                 roles: list[str] | None = None) -> str:
    """held_back — сколько вакансий скрыто из-за неназванного грейда.
    Молчать об этом нельзя: у тех, кто ищет стажировку, подборка выходит
    короткой, и без объяснения это неотличимо от поломки бота.

    roles — профессии человека: если их больше одной, подборка разбивается
    на группы по профессиям."""
    today = date.today()
    header = f"<b>Дайджест {today.day} {MONTHS[today.month - 1]}</b>"

    note = ""
    if held_back:
        note = (
            f"\n\nСкрыто ещё {held_back} "
            + _plural_hidden(held_back)
            + ": в них уровень не назван, а такие вакансии почти всегда "
            "на мидла. Чтобы видеть и их, добавь в /settings нужный грейд."
        )

    if not vacancies:
        return header + "\nСегодня ничего нового по твоим фильтрам." + note

    header += f" · {len(vacancies)} " + _plural(len(vacancies))
    # Заголовки групп нужны, только когда групп правда несколько: над
    # единственной пачкой заголовок — лишний шум.
    body = ""
    if roles and len(roles) > 1:
        body = _grouped(vacancies, roles)
    if not body:
        body = "\n\n".join(_line(v) for v in vacancies)
    return header + "\n\n" + body + note


def _plural_hidden(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "вакансия"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "вакансии"
    return "вакансий"


def _plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "новая"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "новые"
    return "новых"


_TAG_RE = re.compile(r"</?[a-z]+(?:\s[^>]*)?>")


def to_plain(digest: str) -> str:
    """Дайджест как его увидит человек в телеграме — без тегов и без сущностей.
    Нужно для --dry-run: иначе в консоли мелькает «&amp;» и непонятно, баг это
    или нормальное экранирование."""
    return unescape(_TAG_RE.sub("", digest))


# Описание вакансии режем: по нему ищутся ключевые и стоп-слова, но целиком
# оно весит килобайты, а в хранилище летит одним HTTP-запросом на весь список.
DESCRIPTION_LIMIT = 1200


def to_rows(vacancies: list[Vacancy]) -> list[dict]:
    rows = []
    for vacancy in vacancies:
        row = asdict(vacancy)
        row["description"] = row["description"][:DESCRIPTION_LIMIT]
        rows.append(row)
    return rows


def from_rows(rows: list[dict]) -> list[Vacancy]:
    fields = {f for f in Vacancy.__dataclass_fields__}
    return [Vacancy(**{k: v for k, v in row.items() if k in fields}) for row in rows]


def to_json(vacancies: list[Vacancy]) -> str:
    return json.dumps([asdict(v) for v in vacancies], ensure_ascii=False, indent=2)
