"""Сбор вакансий с getmatch.ru.

Площадка с редким для рынка качеством данных: формат работы, город, грейд
и вилка проставлены у большинства вакансий, а компании — первого ряда
(VK, Яндекс, Авито). Дизайна там немного, десятки, но почти всё по делу.

ВАЖНАЯ ОГОВОРКА. Открытый JSON лежит по адресу `/api/offers`, а `robots.txt`
getmatch закрывает весь `/api/` (`Disallow: /api/`). Других дверей нет:
страницы-лендинги вроде `/vacancies/product_design` отдают пустую оболочку
и наполняются на стороне браузера, а sitemap перечисляет тридцать три тысячи
вакансий без дат — обойти его ради двух десятков дизайнерских нельзя.
Ситуация того же рода, что с hh.ru: решение подключать принято Андреем
осознанно. Поэтому ходим предельно скромно — два узких запроса в сутки
с паузами, без обхода каталога.

Грейд в списке не приходит, он в детали вакансии полем `seniority`.
Дёргаем деталь только для тех, кто прошёл по дате: это несколько запросов.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from core import Vacancy

API = "https://getmatch.ru/api/offers"
SITE = "https://getmatch.ru"
PAGE = 100
DELAY_SEC = 0.5

USER_AGENT = "Mozilla/5.0 (Macintosh) FinderJob/0.1 (personal job digest)"

# getmatch раскладывает дизайн ровно по двум специализациям.
# Всё остальное у них — разработка, туда не ходим.
DEFAULT_ROUTES = ["product_design", "ux_writer"]

FORMATS = {"remote": "remote", "hybrid": "hybrid", "office": "office"}

# Их грейды совпадают с нашими по названиям, кроме c_level — его сводим к lead.
SENIORITY = {
    "intern": "intern",
    "junior": "junior",
    "middle": "middle",
    "senior": "senior",
    "lead": "lead",
    "head": "lead",
    "c_level": "lead",
}


def _get(url: str, timeout: int = 30, tries: int = 2) -> dict:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(1.5)
    raise last if last else RuntimeError(f"не удалось получить {url}")


def _place(items: list | None) -> tuple[str, str | None]:
    """location_items → формат работы и город.

    Формат берём самый свободный из перечисленных: если вакансию можно вести
    удалённо, человеку неважно, что рядом указан ещё и офис. Город — метка
    первого не-удалённого места, из неё убираем хвост про метро.
    """
    formats: set[str] = set()
    city = None
    for item in items or []:
        if item.get("exclude"):
            continue
        value = FORMATS.get(item.get("format") or "")
        if value:
            formats.add(value)
        label = (item.get("label") or "").split("(")[0].strip(" ,")
        if label and city is None and value != "remote":
            city = label

    for candidate in ("remote", "hybrid", "office"):
        if candidate in formats:
            return candidate, city
    return "unknown", city


def _salary(offer: dict) -> int | None:
    """Нижняя граница вилки в рублях. Валюту, кроме рубля, не берём —
    сравнивать её с рублёвым порогом человека нельзя."""
    if offer.get("salary_hidden"):
        return None
    if (offer.get("salary_currency") or "RUB") != "RUB":
        return None
    value = offer.get("salary_display_from") or offer.get("salary_display_to")
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if 10_000 <= value <= 5_000_000 else None


def _grade(offer_id: str | int) -> str | None:
    """Грейд из детали вакансии. Недоступна — вакансия просто идёт без грейда
    и проходит фильтр насквозь, как у нас заведено."""
    try:
        detail = _get(f"{API}/{offer_id}")
    except Exception:
        return None
    return SENIORITY.get(detail.get("seniority") or "")


def list_recent(since: str, routes: list[str] | None = None) -> list[dict]:
    """Свежие вакансии обеих дизайнерских специализаций."""
    out: list[dict] = []
    seen: set[str] = set()
    for spec in routes or DEFAULT_ROUTES:
        offset = 0
        while True:
            query = urllib.parse.urlencode(
                {"limit": PAGE, "offset": offset, "sp": spec}
            )
            try:
                data = _get(f"{API}?{query}")
            except Exception as exc:
                print(f"  ! getmatch, специализация {spec}: {exc}")
                break

            offers = data.get("offers") or []
            for offer in offers:
                key = str(offer.get("id"))
                if key in seen or not offer.get("is_active", True):
                    continue
                seen.add(key)
                if (offer.get("published_at") or "")[:10] < since:
                    continue
                out.append(offer)

            offset += PAGE
            if offset >= (data.get("meta") or {}).get("total", 0):
                break
            time.sleep(DELAY_SEC)
        time.sleep(DELAY_SEC)
    return out


def collect(since: str, limit: int | None = None,
            routes: list[str] | None = None) -> list[Vacancy]:
    offers = list_recent(since, routes)
    if limit:
        offers = offers[:limit]

    vacancies = []
    for index, offer in enumerate(offers):
        key = str(offer.get("id"))
        work_format, city = _place(offer.get("location_items"))
        url = offer.get("url") or ""
        if not url.startswith("http"):
            url = SITE + url

        skills = ", ".join(
            item.get("name", "") for item in offer.get("skills_objects") or []
        )

        vacancies.append(
            Vacancy(
                id=f"getmatch:{key}",
                source="getmatch",
                title=offer.get("position") or "",
                company=(offer.get("company") or {}).get("name") or "",
                grade=_grade(key),
                salary=_salary(offer),
                work_format=work_format,
                city=city,
                country=None,
                url=url,
                posted_at=(offer.get("published_at") or "")[:10],
                experience_months=None,
                description=(offer.get("offer_description") or "")[:4000],
                skills=skills,
            )
        )
        if index < len(offers) - 1:
            time.sleep(DELAY_SEC)
    return vacancies
