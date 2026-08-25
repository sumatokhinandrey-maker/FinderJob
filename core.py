"""Модель вакансии, фильтр под профиль, память о показанном и сборка дайджеста."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
SEEN_PATH = ROOT / "seen.json"


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


def _hybrid_allowed(vacancy: Vacancy, rules: dict) -> bool:
    """Гибрид разрешён только в перечисленных городах либо за границей."""
    city = (vacancy.city or "").strip().lower()
    allowed = [c.lower() for c in rules.get("hybrid_cities", [])]
    if any(city == c or c in city for c in allowed):
        return True
    if rules.get("hybrid_abroad") and vacancy.country and vacancy.country != "RU":
        return True
    # Город не указан вовсе — не угадываем, пропускаем дальше как подходящий,
    # решать будет человек.
    return not city


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
    if vacancy.work_format == "hybrid":
        if not formats.get("hybrid", True) or not _hybrid_allowed(vacancy, formats):
            return "format"
    if vacancy.work_format == "office" and not formats.get("office", False):
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


def load_seen() -> dict:
    if not SEEN_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_PATH.read_text("utf-8"))
    except json.JSONDecodeError:
        return {}


def save_seen(seen: dict, keep_days: int = 90) -> None:
    """Храним id с датой показа и подчищаем старое, чтобы файл не рос вечно."""
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    fresh = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_PATH.write_text(
        json.dumps(fresh, ensure_ascii=False, indent=1, sort_keys=True), "utf-8"
    )


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
    header = f"🎯 <b>Дайджест {today.day} {MONTHS[today.month - 1]}</b>"

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


def to_json(vacancies: list[Vacancy]) -> str:
    return json.dumps([asdict(v) for v in vacancies], ensure_ascii=False, indent=2)
