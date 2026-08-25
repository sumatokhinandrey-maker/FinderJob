"""Модель вакансии, фильтр под профиль, память о показанном и сборка дайджеста."""

from __future__ import annotations

import json
import re
from html import unescape
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

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

# Причины отсева. Нужны, чтобы в дайджесте показать, что бот работал,
# даже когда подходящего нет — иначе молчание неотличимо от поломки.
REASONS = {
    "grade": "грейд",
    "role": "не та роль",
    "stop": "стоп-слово",
    "format": "формат работы",
    "salary": "деньги",
}


def _city_allowed(vacancy: Vacancy, rules: dict) -> bool:
    """Город подходит? Касается только гибрида и офиса — на удалёнку не влияет."""
    if rules.get("any_city", True):
        return True
    city = (vacancy.city or "").strip().lower()
    # Город не указан вовсе — не угадываем, пропускаем дальше,
    # решать будет человек.
    if not city:
        return True
    allowed = [c.lower() for c in rules.get("cities", [])]
    if any(city == c or c in city for c in allowed):
        return True
    if rules.get("abroad") and vacancy.country and vacancy.country != "RU":
        return True
    return False


def check(vacancy: Vacancy, profile: dict) -> str | None:
    """None — вакансия подходит. Иначе код причины отсева."""
    text = vacancy.haystack()

    grades = profile.get("grades") or []
    if grades and vacancy.grade and vacancy.grade not in grades:
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
        if not _city_allowed(vacancy, formats):
            return "format"

    minimum = profile.get("salary_min")
    if minimum and vacancy.salary is not None and vacancy.salary < minimum:
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
        facts.append(vacancy.grade)

    return (
        f"▸ <b>{_escape(vacancy.title)}</b>\n"
        f"   {_escape(' · '.join(f for f in facts if f))}\n"
        f"   {vacancy.url}"
    )


def build_digest(
    vacancies: list[Vacancy], rejected: dict[str, int], scanned: int
) -> str:
    today = date.today()
    header = f"<b>Дайджест {today.day} {MONTHS[today.month - 1]}</b>"

    if not vacancies:
        body = "\nСегодня ничего нового по твоим фильтрам."
    else:
        header += f" · {len(vacancies)} " + _plural(len(vacancies))
        body = "\n\n" + "\n\n".join(_line(v) for v in vacancies)

    parts = [f"{REASONS.get(k, k)} {v}" for k, v in sorted(rejected.items())]
    footer = f"\n\n<i>Просмотрено {scanned}"
    if parts:
        footer += ", отсеяно: " + ", ".join(parts)
    footer += "</i>"

    return header + body + footer


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
