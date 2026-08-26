"""Модель вакансии, фильтр под профиль, память о показанном и сборка дайджеста."""

from __future__ import annotations

import json
import re
from html import unescape
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from finderjob import geo

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
# фильтр
# --------------------------------------------------------------------------

# Причины отсева. В сообщение не идут — человеку они не нужны;
# остаются в логах рассылки, чтобы было видно, как работают фильтры.
REASONS = {
    "grade": "грейд",
    "role": "не та роль",
    "stop": "стоп-слово",
    "format": "формат работы",
    "salary": "деньги",
}


def _place_allowed(vacancy: Vacancy, rules: dict) -> bool:
    """Место подходит? Касается только гибрида и офиса — на удалёнку не влияет.

    Три источника ответа: корзина «города России», корзина «за рубежом»
    и список конкретных городов. Пусто везде — шаг пропущен, подходит всё.
    """
    places = rules.get("places") or []
    cities = rules.get("cities") or []
    if not places and not cities:
        return True

    if not vacancy.city:
        # Город не указан вовсе — не угадываем, решать будет человек.
        return True

    if any(geo.same(vacancy.city, name) for name in cities):
        return True

    where = geo.where(vacancy.city)
    if where is None:
        # Незнакомый город: в какой он корзине, мы не знаем, поэтому
        # не отсекаем. Лучше лишняя строка, чем потерянная вакансия.
        return True
    return ("russia" if where == "ru" else "abroad") in places


def check(vacancy: Vacancy, profile: dict) -> str | None:
    """None — вакансия подходит. Иначе код причины отсева."""
    text = vacancy.haystack()

    # У geekjob вакансия может быть сразу на несколько грейдов
    # («Миддл • Сеньор»), поэтому храним их через запятую и сверяем пересечение.
    grades = profile.get("grades") or []
    if grades and vacancy.grade:
        if not set(vacancy.grade.split(",")) & set(grades):
            return "grade"

    include = [w.lower() for w in profile.get("include_any", [])]
    if include and not any(word in text for word in include):
        return "role"

    for word in profile.get("exclude_any", []):
        if re.search(rf"\b{re.escape(word.lower())}", text):
            return "stop"

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


def dedupe(vacancies: list[Vacancy]) -> list[Vacancy]:
    """Одна вакансия часто висит сразу на нескольких площадках. Оставляем
    ту запись, где больше данных — с указанной вилкой полезнее, чем без."""
    best: dict[tuple, Vacancy] = {}
    order: list[tuple] = []
    for vacancy in vacancies:
        key = _fingerprint(vacancy)
        current = best.get(key)
        if current is None:
            best[key] = vacancy
            order.append(key)
            continue
        richer = (vacancy.salary is not None and current.salary is None) or (
            vacancy.grade is not None and current.grade is None
        )
        if richer:
            best[key] = vacancy
    return [best[key] for key in order]


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

    return (
        f"▸ <b>{_escape(vacancy.title)}</b>\n"
        f"   {_escape(' · '.join(f for f in facts if f))}\n"
        f"   {vacancy.url}"
    )


def build_digest(vacancies: list[Vacancy]) -> str:
    today = date.today()
    header = f"<b>Дайджест {today.day} {MONTHS[today.month - 1]}</b>"

    if not vacancies:
        return header + "\nСегодня ничего нового по твоим фильтрам."

    header += f" · {len(vacancies)} " + _plural(len(vacancies))
    return header + "\n\n" + "\n\n".join(_line(v) for v in vacancies)


def _plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "новая"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "новые"
    return "новых"


_TAG_RE = re.compile(r"</?[a-z]+>")


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
