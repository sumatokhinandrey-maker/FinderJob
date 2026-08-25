"""Сбор вакансий с hirehi.ru.

Поиск на сайте закрыт для ботов (robots.txt: Disallow: /*?*search=), поэтому
идём законным путём: sitemap отдаёт список всех вакансий с датой обновления,
а на странице каждой вакансии лежит разметка schema.org/JobPosting —
её парсить надёжнее, чем вёрстку, которая поедет при первом редизайне.
"""

from __future__ import annotations

import gzip
import json
import re
import time
import urllib.error
import urllib.request
from html import unescape

from core import Vacancy

SITEMAP = "https://hirehi.ru/sitemap-vacancies-design.xml"
USER_AGENT = "JobResearchBot/0.1 (personal job digest; +https://github.com/)"

# Пауза между запросами к страницам вакансий. Мы ходим по чужому сайту
# раз в сутки за десятком страниц — незачем создавать нагрузку.
DELAY_SEC = 0.5

_LD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)
_META_DESC_RE = re.compile(r'<meta name="description" content="([^"]*)"')
_SITEMAP_RE = re.compile(r"<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>")

# Грейд hirehi отдаёт строкой вида "middle level position".
_GRADE_RE = re.compile(r"\b(junior|middle|senior|lead)\b", re.I)


def _fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def list_recent(since: str) -> list[str]:
    """URL вакансий, обновлённых не раньше даты since (YYYY-MM-DD).

    Sitemap отсортирован по алфавиту, поэтому пересортировываем по дате —
    иначе --limit будет обрезать не самое свежее, а всё на букву «а».
    """
    sitemap = _fetch(SITEMAP)
    recent = [
        (loc, lastmod)
        for loc, lastmod in _SITEMAP_RE.findall(sitemap)
        if lastmod >= since
    ]
    recent.sort(key=lambda pair: pair[1], reverse=True)
    return [loc for loc, _ in recent]


def _text(value: object) -> str:
    """hirehi отдаёт в JSON-LD уже экранированный HTML («Wildberries &amp; Russ»).
    Раскодируем на входе — экранировать перед отправкой умеет core._escape,
    иначе амперсанд уедет в телеграм как «&amp;amp;»."""
    return unescape(str(value or "")).strip()


def _job_posting(html: str) -> dict | None:
    for block in _LD_RE.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _parse_work_format(html: str) -> tuple[str, str | None]:
    """Формат работы и город из meta-description.

    В JSON-LD формата нет, а в meta он выражен стабильными фразами:
    "Удалённая работа", "Гибридная работа Москва", "Работа в офисе Варшава".
    Возвращает (remote|hybrid|office|unknown, город или None).
    """
    match = _META_DESC_RE.search(html)
    if not match:
        return "unknown", None
    desc = unescape(match.group(1))

    if re.search(r"Удал[её]нная работа", desc):
        return "remote", None
    hybrid = re.search(r"Гибридная работа\s*([^.<]*)", desc)
    if hybrid:
        return "hybrid", (hybrid.group(1).strip() or None)
    office = re.search(r"Работа в офисе\s*([^.<]*)", desc)
    if office:
        return "office", (office.group(1).strip() or None)
    return "unknown", None


def _parse_salary(posting: dict) -> int | None:
    """Зарплата в рублях за месяц. hirehi кладёт туда либо число, либо
    строку 'зпнеуказана'."""
    raw = (
        posting.get("baseSalary", {})
        .get("value", {})
        .get("value")
    )
    if raw in (None, "", "зпнеуказана"):
        return None
    try:
        return int(float(str(raw).replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError):
        return None


def parse_vacancy(url: str, html: str) -> Vacancy | None:
    posting = _job_posting(html)
    if not posting:
        return None

    grade_match = _GRADE_RE.search(posting.get("qualifications") or "")
    work_format, fmt_city = _parse_work_format(html)
    address = posting.get("jobLocation", {}).get("address", {})
    experience = posting.get("experienceRequirements", {})

    vacancy_id = posting.get("identifier", {}).get("value") or url.rsplit("-", 1)[-1]

    return Vacancy(
        id=f"hirehi:{vacancy_id}",
        source="hirehi",
        title=_text(posting.get("title")),
        company=_text(posting.get("hiringOrganization", {}).get("name")),
        grade=grade_match.group(1).lower() if grade_match else None,
        salary=_parse_salary(posting),
        work_format=work_format,
        city=_text(address.get("addressLocality")) or fmt_city,
        country=address.get("addressCountry"),
        url=posting.get("url") or url,
        posted_at=(posting.get("datePosted") or "")[:10],
        experience_months=experience.get("monthsOfExperience"),
        description=_text(posting.get("description")),
        skills=_text(posting.get("skills")),
    )


def collect(since: str, limit: int | None = None) -> list[Vacancy]:
    """Вакансии, обновлённые начиная с даты since."""
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
        else:
            print(f"  ! нет JobPosting: {url}")

        if index < len(urls) - 1:
            time.sleep(DELAY_SEC)

    return vacancies
