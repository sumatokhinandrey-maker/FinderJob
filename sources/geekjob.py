"""Сбор вакансий с geekjob.ru.

Sitemap у них мёртвый: все 499 ссылок помечены 13 октября 2020 года, хотя сайт
живой и вакансии свежие. Поэтому идём по страницам списка — `robots.txt`
разрешает всё, кроме `/json/` и `/rest/`, а поиск как раз работает через них,
так что фильтровать приходится у себя.

Дизайна тут мало: из 254 вакансий дизайнерских двенадцать, и семь из них —
гейм-дизайн, графика и 3D, которые отсеются стоп-словами. Зато пересечения
с hirehi нет вовсе, так что оставшееся — честная прибавка.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from html import unescape

from core import Vacancy

BASE = "https://geekjob.ru"
USER_AGENT = "JobResearchBot/0.1 (personal job digest; +https://github.com/)"

DELAY_SEC = 0.35
MAX_PAGES = 20

_LD_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_CARD_RE = re.compile(r'href="/vacancy/([0-9a-f]+)"[^>]*>\s*([^<]{2,90})<')
_SCRIPT_RE = re.compile(r"(?s)<(script|style)[^>]*>.*?</\1>")
_TAG_RE = re.compile(r"<[^>]+>")

# Что вообще считаем дизайном на входе. Роль потом ещё раз проверит фильтр
# пользователя — здесь задача только не тащить страницы разработчиков.
_DESIGN_RE = re.compile(r"(дизайн|design|\bux\b|\bui\b|интерфейс)", re.I)

# Грейды geekjob пишет по-русски и через точку: «Миддл • Сеньор».
GRADES = {
    "стажер": "intern",
    "стажёр": "intern",
    "джуниор": "junior",
    "миддл": "middle",
    "сеньор": "senior",
    "тимлид": "lead",
    "лид": "lead",
}


def _fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _header(html: str) -> list[str]:
    """Шапка страницы кусками: там лежат город, грейд, формат работы и опыт —
    в JSON-LD у geekjob ничего этого нет."""
    body = _SCRIPT_RE.sub(" ", html)
    flat = " ".join(_TAG_RE.sub(" ¦ ", body).split())
    flat = re.sub(r"(?: ¦)+", " ¦", flat)
    start = flat.find("menu ¦")
    chunk = flat[start:start + 1200] if start > 0 else flat[:1200]
    return [unescape(part).replace("\xa0", " ").strip()
            for part in chunk.split("¦") if part.strip()]


def list_recent(since: str) -> list[str]:
    """Адреса дизайнерских вакансий со страниц списка.

    Заголовок, компания и город видны прямо в списке, поэтому страницы
    разработчиков мы даже не открываем — это экономит две сотни запросов.
    """
    urls: list[str] = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE}/vacancies" if page == 1 else f"{BASE}/vacancies/{page}"
        try:
            html = _fetch(url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  ! страница списка {page} не открылась: {exc}")
            break

        cards: dict[str, list[str]] = {}
        for vacancy_id, text in _CARD_RE.findall(html):
            cards.setdefault(vacancy_id, []).append(" ".join(text.split()))
        if not cards:
            break

        for vacancy_id, texts in cards.items():
            if _DESIGN_RE.search(" ".join(texts)):
                candidate = f"{BASE}/vacancy/{vacancy_id}"
                if candidate not in urls:
                    urls.append(candidate)
        time.sleep(DELAY_SEC)
    return urls


def _job_posting(html: str) -> dict | None:
    for block in _LD_RE.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _text(value: object) -> str:
    return unescape(_TAG_RE.sub(" ", str(value or ""))).strip()


def _salary(posting: dict) -> int | None:
    amount = posting.get("baseSalary") or {}
    if str(amount.get("currency", "")).upper() not in ("RUB", "RUR", ""):
        return None
    value = amount.get("value") or {}
    if str(value.get("unitText", "MONTH")).upper() != "MONTH":
        return None
    raw = value.get("minValue") or value.get("value")
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _grades(parts: list[str]) -> str | None:
    """Все грейды вакансии через запятую: geekjob часто вешает сразу
    «Миддл • Сеньор», и отбрасывать второй значит терять вакансию."""
    found = []
    for part in parts:
        if "•" not in part and part not in GRADES and part.lower() not in GRADES:
            continue
        for token in part.split("•"):
            grade = GRADES.get(token.strip().lower().split("/")[0])
            if grade and grade not in found:
                found.append(grade)
    return ",".join(found) or None


def _place(parts: list[str]) -> tuple[str | None, str | None]:
    """Город и страна из куска вида «Москва, Россия»."""
    for part in parts:
        match = re.fullmatch(r"([А-ЯЁA-Z][^,]{1,30}),\s*([А-ЯЁA-Z][^,]{1,25})", part)
        if match:
            city, country = match.group(1).strip(), match.group(2).strip()
            return city, ("RU" if country.lower().startswith("росси") else country)
    return None, None


def _work_format(parts: list[str]) -> str:
    blob = " ".join(parts).lower()
    if "гибрид" in blob:
        return "hybrid"
    if re.search(r"удал[её]нн", blob):
        return "remote"
    if "офис" in blob:
        return "office"
    # Ничего не сказано — не угадываем: unknown проходит фильтр формата
    # насквозь, решать будет человек.
    return "unknown"


def parse_vacancy(url: str, html: str) -> Vacancy | None:
    posting = _job_posting(html)
    if not posting:
        return None

    parts = _header(html)
    city, country = _place(parts)

    return Vacancy(
        id=f"geekjob:{(posting.get('identifier') or {}).get('value') or url.rsplit('/', 1)[-1]}",
        source="geekjob",
        title=_text(posting.get("title")),
        company=_text((posting.get("hiringOrganization") or {}).get("name")),
        grade=_grades(parts),
        salary=_salary(posting),
        work_format=_work_format(parts),
        city=city,
        country=country,
        url=url,
        posted_at=(posting.get("datePosted") or "")[:10],
        experience_months=None,
        description=_text(posting.get("description")),
        skills="",
    )


def collect(since: str, limit: int | None = None) -> list[Vacancy]:
    urls = list_recent(since)
    if limit:
        urls = urls[:limit]

    vacancies = []
    for index, url in enumerate(urls):
        try:
            html = _fetch(url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  ! не открылась {url}: {exc}")
            continue
        vacancy = parse_vacancy(url, html)
        if vacancy:
            vacancies.append(vacancy)
        if index < len(urls) - 1:
            time.sleep(DELAY_SEC)
    return vacancies
