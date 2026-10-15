"""Сбор вакансий с finder.work.

Агрегатор с открытым JSON: `api.finder.work/api/v1/vacancies/` принимает
полнотекстовый `search` и отдаёт по сотне записей на страницу. Маршрут
источника — поисковый запрос, как у hh.

**Зачем он, если hh уже подключён.** Лента hh отдаёт только 20 свежих на
запрос, и это наш потолок по объёму. Finder по тому же слову отдаёт тысячу,
а в каждой записи лежит `external_url` на оригинал — у всех проверенных ста
это hh. То есть он даёт тот же рынок, но без лимита, и ссылка ведёт туда,
где человек откликается как обычно.

**Правовая оговорка.** `api.finder.work/robots.txt` — это `Disallow: *`,
то есть закрыто всё. Случай того же рода, что hh.ru и getmatch; Андрей
предупреждён 15.10.2026, решение подключать — его.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from core import Vacancy

API = "https://api.finder.work/api/v1/vacancies/"
USER_AGENT = "Mozilla/5.0 (Macintosh) JobResearchBot/0.1 (personal job digest)"
PAGE = 100
MAX_PAGES = 2
DELAY_SEC = 0.5

# Опыт приходит перечислением, переводим в месяцы. Грейд из него НЕ выводим:
# в проекте заголовок считается точнее поля источника.
_EXPERIENCE = {
    "no_experience": 0,
    "year_minimum": 12,
    "three_years_more": 36,
    "five_years_more": 60,
}

_HH_ID_RE = re.compile(r"vacancyId=(\d+)")


def _fetch(query: str, offset: int, timeout: int = 25, tries: int = 2) -> dict:
    params = urllib.parse.urlencode(
        {"search": query, "limit": str(PAGE), "offset": str(offset)}
    )
    req = urllib.request.Request(
        f"{API}?{params}", headers={"User-Agent": USER_AGENT}
    )
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == tries - 1:
                raise
            time.sleep(1.5)
    return {}


def _origin(item: dict) -> str | None:
    """Ссылка на оригинал без рекламных хвостов.

    Finder отдаёт адрес формы отклика с utm-метками; человеку нужнее сама
    вакансия, а её номер стоит прямо в адресе.
    """
    raw = (item.get("external_url") or {}).get("value") or ""
    found = _HH_ID_RE.search(raw)
    return f"https://hh.ru/vacancy/{found.group(1)}" if found else None


def parse_vacancy(item: dict) -> Vacancy | None:
    vacancy_id = item.get("id")
    title = (item.get("title") or "").strip()
    if not vacancy_id or not title:
        return None

    locations = item.get("locations") or []
    city = (locations[0].get("name") if locations else None) or (
        (item.get("place") or {}).get("city")
    )

    return Vacancy(
        id=f"finder:{vacancy_id}",
        source="finder",
        title=title,
        company=((item.get("company") or {}).get("title") or "").strip(),
        grade=None,
        # Ноль у них значит «не указана», а не «платим ноль».
        salary=item.get("salary_from") or None,
        # Про офис и гибрид лента молчит: известна только удалёнка.
        work_format="remote" if item.get("distant_work") else "unknown",
        city=city,
        country=None,
        url=_origin(item) or f"https://finder.work/vacancies/{vacancy_id}",
        posted_at=str(item.get("publication_at") or "")[:10],
        experience_months=_EXPERIENCE.get(item.get("experience")),
        description=(item.get("short_description") or "").strip(),
        skills="",
    )


def collect(since: str, limit: int | None = None,
            routes: list[str] | None = None) -> list[Vacancy]:
    """Вакансии по поисковым запросам routes, опубликованные с даты since.

    Выдача идёт по релевантности, а не по дате, поэтому окно режем сами.
    """
    queries = list(dict.fromkeys(routes or []))
    seen: set[str] = set()
    vacancies = []

    for query in queries:
        for page in range(MAX_PAGES):
            try:
                data = _fetch(query, page * PAGE)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                print(f"  ! finder не отдал «{query}»: {exc}")
                break

            items = data.get("items") or []
            for item in items:
                if item.get("archived_at"):
                    continue
                vacancy = parse_vacancy(item)
                if not vacancy or vacancy.id in seen:
                    continue
                if vacancy.posted_at and vacancy.posted_at < since:
                    continue
                seen.add(vacancy.id)
                vacancies.append(vacancy)

            if len(items) < PAGE:
                break
            time.sleep(DELAY_SEC)
        time.sleep(DELAY_SEC)

    vacancies.sort(key=lambda v: v.posted_at, reverse=True)
    return vacancies[:limit] if limit else vacancies


if __name__ == "__main__":
    sample = {
        "id": 36958008,
        "title": "Продуктовый дизайнер",
        "company": {"title": "Лайфстайл"},
        "salary_from": 0,
        "salary_to": 0,
        "experience": "three_years_more",
        "publication_at": "2026-10-14T23:49:55.149250Z",
        "distant_work": False,
        "archived_at": None,
        "external_url": {
            "label": "hh.ru",
            "value": "https://hh.ru/applicant/vacancy_response?vacancyId=138471147&utm_source=finder.work",
        },
        "locations": [{"name": "Москва"}],
        "short_description": "Проектировать сценарии",
    }
    v = parse_vacancy(sample)
    assert v.id == "finder:36958008", v.id
    assert v.company == "Лайфстайл", v.company
    assert v.salary is None, v.salary
    assert v.city == "Москва", v.city
    assert v.posted_at == "2026-10-14", v.posted_at
    assert v.experience_months == 36, v.experience_months
    # Рекламный хвост отброшен, остался адрес самой вакансии.
    assert v.url == "https://hh.ru/vacancy/138471147", v.url
    assert v.work_format == "unknown", v.work_format

    assert parse_vacancy(dict(sample, distant_work=True)).work_format == "remote"
    assert parse_vacancy(dict(sample, salary_from=150000)).salary == 150000
    # Без разбираемого номера остаётся страница самого finder.
    no_origin = parse_vacancy(dict(sample, external_url={"value": "https://example.com/x"}))
    assert no_origin.url == "https://finder.work/vacancies/36958008", no_origin.url
    print("finder: проверки прошли")
