"""Сбор вакансий с career.habr.com.

Хабр Карьера — площадка про разработку, дизайн там боковая ветка: из 1006
вакансий дизайнерских девятнадцать. Зато почти все они не пересекаются
с hirehi — это корпорации вроде Сбера, МТС Банка и Яндекса, которые
на hirehi не публикуются.

Обходить весь sitemap ради двух вакансий на сотню незачем: у Хабра есть
готовые страницы-рубрики без параметров в адресе, и robots.txt их разрешает.
Берём вакансии оттуда, а разметку читаем со страницы — та же schema.org,
что у hirehi.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from html import unescape

from core import Vacancy

BASE = "https://career.habr.com"
USER_AGENT = "JobResearchBot/0.1 (personal job digest; +https://github.com/)"

# Рубрики, которые нас касаются. Графика, полиграфия, 3D и иллюстрация
# у Хабра лежат отдельными рубриками — их не берём вовсе, вместо того
# чтобы потом отсеивать стоп-словами.
DEFAULT_ROUTES = [
    "dizajner",
    "ui_ux_designer",
    "ux_designer",
    "veb_dizajner",
    "digital_dizajner",
    "razrabotchik_interfejsov",
]

DELAY_SEC = 0.4

_LD_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_LINK_RE = re.compile(r'href="/vacancies/(\d+)"')
_TAG_RE = re.compile(r"<[^>]+>")

# В описании Хабр сам выкладывает разбор: «Навыки: … Квалификация: Middle.»
_GRADE_RE = re.compile(r"Квалификация:\s*([A-Za-zА-Яа-я+]+)")
_SKILLS_RE = re.compile(r"Навыки:\s*([^.]+)\.")

GRADES = {
    "intern": "intern",
    "стажер": "intern",
    "стажёр": "intern",
    "junior": "junior",
    "middle": "middle",
    "senior": "senior",
    "lead": "lead",
}


def _fetch(url: str, timeout: int = 30, tries: int = 3) -> str:
    """Хабр время от времени роняет SSL-рукопожатие. Одна неудача не повод
    терять источник целиком, поэтому пробуем несколько раз."""
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError:
            raise
        except Exception as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError(f"не удалось получить {url}")


def list_recent(since: str, routes: list[str] | None = None) -> list[str]:
    """Адреса вакансий из дизайнерских рубрик.

    Даты обновления на страницах рубрик нет, поэтому since здесь не работает —
    отбор по свежести делает память о показанном. Рубрики отдают два-три
    десятка вакансий, так что лишнего не наберётся.
    """
    ids: list[str] = []
    for slug in routes or DEFAULT_ROUTES:
        try:
            html = _fetch(f"{BASE}/vacancies/{slug}")
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  ! рубрика {slug} не открылась: {exc}")
            continue
        for vacancy_id in _LINK_RE.findall(html):
            if vacancy_id not in ids:
                ids.append(vacancy_id)
        time.sleep(DELAY_SEC)
    return [f"{BASE}/vacancies/{vacancy_id}" for vacancy_id in ids]


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
    """Зарплата в рублях за месяц. У Хабра нижняя граница лежит в minValue —
    не в value, как у hirehi."""
    amount = posting.get("baseSalary") or {}
    if str(amount.get("currency", "")).upper() not in ("RUR", "RUB", ""):
        return None
    value = amount.get("value") or {}
    if str(value.get("unitText", "MONTH")).upper() != "MONTH":
        return None
    raw = value.get("minValue") or value.get("value")
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _city(posting: dict) -> str | None:
    places = posting.get("jobLocation")
    if isinstance(places, list) and places:
        address = places[0].get("address")
        if isinstance(address, dict):
            return address.get("addressLocality")
        return _text(address) or None
    return None


def parse_vacancy(url: str, html: str) -> Vacancy | None:
    posting = _job_posting(html)
    if not posting:
        return None

    description = _text(posting.get("description"))
    grade_match = _GRADE_RE.search(description)
    grade = GRADES.get(grade_match.group(1).lower()) if grade_match else None
    skills_match = _SKILLS_RE.search(description)

    # Хабр не различает гибрид: у вакансии либо стоит отметка «можно удалённо»,
    # либо она офисная. Приписывать гибрид по наличию города — гадание.
    remote = posting.get("jobLocationType") == "TELECOMMUTE"

    vacancy_id = (posting.get("identifier") or {}).get("value") or url.rsplit("/", 1)[-1]

    return Vacancy(
        id=f"habr:{vacancy_id}",
        source="habr",
        title=_text(posting.get("title")),
        company=_text((posting.get("hiringOrganization") or {}).get("name")),
        grade=grade,
        salary=_salary(posting),
        work_format="remote" if remote else "office",
        city=_city(posting),
        country=None,
        url=url,
        posted_at=(posting.get("datePosted") or "")[:10],
        experience_months=None,
        description=description,
        skills=_text(skills_match.group(1)) if skills_match else "",
    )


def collect(since: str, limit: int | None = None,
            routes: list[str] | None = None) -> list[Vacancy]:
    urls = list_recent(since, routes)
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
