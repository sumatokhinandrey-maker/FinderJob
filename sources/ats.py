"""Вакансии с собственных досок компаний на публичных ATS-платформах.

Идея Андрея от 15.10.2026: у нас уже есть названия компаний — значит,
у них есть свои карьерные страницы, и вакансии можно брать у самого
работодателя. Отклик тогда идёт напрямую, без посредников и подписок.

Свой сайт у каждой компании — это свой парсер на каждую, дорого. Но многие
держат вакансии на готовых платформах (Greenhouse, Lever, Ashby, Workable,
SmartRecruiters, Recruitee), и у тех есть официальные открытые API — ровно
для того, чтобы вакансии показывали где угодно. Поэтому здесь один адаптер
на платформу, а список компаний лежит в `ats_registry.json`.

В реестр попадает только подтверждённый работодатель: совпадение адреса
доски с названием ещё ничего не доказывает (проверка 15.10.2026: доска
«revelrobotics» ни разу не называла Revel Robotics). Пополняет реестр
`discover_ats.py`.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from core import Vacancy
from finderjob import geo, taxonomy

REGISTRY = Path(__file__).resolve().parent.parent / "ats_registry.json"
USER_AGENT = "Mozilla/5.0 (Macintosh) jobresearch/0.1 (personal job digest)"
DELAY_SEC = 0.5

# Большие компании (у Accor тысячи вакансий) SmartRecruiters ищет сам:
# спрашиваем по слову на направление, а не тянем всё подряд.
SR_QUERY = {"design": "designer", "dev": "developer", "product": "product", "content": "content"}


def _get(url: str, timeout: int = 20) -> object:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _date(value: object) -> str:
    """ISO-строка или миллисекунды эпохи — к YYYY-MM-DD."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return str(value or "")[:10]


def _format(remote: object, text: str) -> str:
    low = (text or "").lower()
    if remote is True or "remote" in low or "удал" in low:
        return "remote"
    if "hybrid" in low or "гибрид" in low:
        return "hybrid"
    if remote is False or "onsite" in low or "on-site" in low or "office" in low:
        return "office"
    return "unknown"


# --------------------------------------------------------------------------
# адаптеры: (заголовок, место, ссылка, дата, формат, id)
# --------------------------------------------------------------------------


def greenhouse(slug: str, directions: list[str]) -> list[tuple]:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    out = []
    for j in data.get("jobs") or []:
        place = (j.get("location") or {}).get("name") or ""
        out.append((j.get("title"), place, j.get("absolute_url"),
                    _date(j.get("first_published") or j.get("updated_at")),
                    _format(None, place), j.get("id")))
    return out


def lever(slug: str, directions: list[str]) -> list[tuple]:
    out = []
    for p in _get(f"https://api.lever.co/v0/postings/{slug}?mode=json"):
        cat = p.get("categories") or {}
        place = cat.get("location") or ""
        out.append((p.get("text"), place, p.get("hostedUrl"), _date(p.get("createdAt")),
                    _format(None, f"{p.get('workplaceType') or ''} {place}"), p.get("id")))
    return out


def ashby(slug: str, directions: list[str]) -> list[tuple]:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    out = []
    for j in data.get("jobs") or []:
        place = j.get("location") or ""
        out.append((j.get("title"), place, j.get("jobUrl"), _date(j.get("publishedAt")),
                    _format(j.get("isRemote"), f"{j.get('workplaceType') or ''} {place}"),
                    j.get("id")))
    return out


def workable(slug: str, directions: list[str]) -> list[tuple]:
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    out = []
    for j in data.get("jobs") or []:
        place = j.get("city") or j.get("country") or ""
        out.append((j.get("title"), place, j.get("url"), _date(j.get("published_on")),
                    _format(j.get("telecommuting"), place), j.get("shortcode") or j.get("url")))
    return out


def smartrecruiters(slug: str, directions: list[str]) -> list[tuple]:
    out, seen = [], set()
    for direction in directions or ["design"]:
        query = urllib.parse.quote(SR_QUERY.get(direction, direction))
        data = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}"
                    f"/postings?limit=100&q={query}")
        for j in data.get("content") or []:
            if j.get("id") in seen:
                continue
            seen.add(j.get("id"))
            loc = j.get("location") or {}
            place = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
            out.append((j.get("name"), place,
                        f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                        _date(j.get("releasedDate")), _format(loc.get("remote"), place),
                        j.get("id")))
        time.sleep(DELAY_SEC)
    return out


def recruitee(slug: str, directions: list[str]) -> list[tuple]:
    data = _get(f"https://{slug}.recruitee.com/api/offers/")
    out = []
    for o in data.get("offers") or []:
        place = o.get("location") or ""
        out.append((o.get("title"), place, o.get("careers_url"), _date(o.get("published_at")),
                    _format(o.get("remote"), place), o.get("id")))
    return out


PLATFORMS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workable": workable,
    "smartrecruiters": smartrecruiters,
    "recruitee": recruitee,
}


# --------------------------------------------------------------------------
# отбор
# --------------------------------------------------------------------------


def worth_it(work_format: str, place: str) -> bool:
    """Удалёнка — откуда угодно; очно и гибрид — только в знакомых городах.

    Знакомые — это Россия, СНГ и хабы релокации из справочника geo (Тбилиси,
    Белград, Лимассол…). Очная вакансия в Джакарте или Сан-Франциско человеку
    из СНГ недоступна без визы — это шум, а не находка.
    """
    # ponytail: очно за рубежом не берём вовсе; если подписчикам понадобится
    # релокация с визовой поддержкой — заводить отдельную галочку в фильтрах.
    if work_format == "remote":
        return True
    first = (place or "").split(",")[0].strip()
    return geo.where(first) is not None


def load_registry() -> list[dict]:
    try:
        data = json.loads(REGISTRY.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [c for c in data.get("companies") or [] if c.get("status") == "verified"]


def collect(since: str, limit: int | None = None,
            routes: list[str] | None = None) -> list[Vacancy]:
    """routes — направления (design, dev…): берём вакансии только этих ролей."""
    directions = list(dict.fromkeys(routes or ["design"]))
    roles = [r for r in taxonomy.all_role_keys() if r.partition(".")[0] in directions]

    vacancies = []
    # Одну удалённую вакансию компании публикуют по копии на страну: EverAI
    # выложила рекрутёра десятью записями — Германия, Испания, Польша… Для
    # удалёнки страна ничего не меняет, поэтому оставляем первую копию.
    remote_seen: set[tuple[str, str]] = set()
    for company in load_registry():
        adapter = PLATFORMS.get(company["platform"])
        if not adapter:
            continue
        try:
            jobs = adapter(company["slug"], directions)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            print(f"  ! доска {company['name']} ({company['platform']}) не ответила: {exc}")
            continue

        for title, place, url, posted, work_format, job_id in jobs:
            if not title or not url or (posted and posted < since):
                continue
            if not taxonomy.match(title, roles):
                continue
            if not worth_it(work_format, place):
                continue
            if work_format == "remote":
                # Страну часто пишут прямо в заголовке: «(Full Remote - Germany)».
                bare = re.sub(r"\s*\([^)]*\)", "", str(title)).strip().lower()
                twin = (company["name"], bare)
                if twin in remote_seen:
                    continue
                remote_seen.add(twin)
            vacancies.append(Vacancy(
                id=f"ats:{company['platform']}:{company['slug']}:{job_id}",
                source="ats",
                title=str(title).strip(),
                company=company["name"],
                grade=None,
                salary=None,
                work_format=work_format,
                city=(place or "").split(",")[0].strip() or None,
                country=None,
                url=url,
                posted_at=posted,
                experience_months=None,
                description="",
                skills="",
            ))
        time.sleep(DELAY_SEC)

    vacancies.sort(key=lambda v: v.posted_at, reverse=True)
    return vacancies[:limit] if limit else vacancies


if __name__ == "__main__":
    assert _date(1760000000000) == "2025-10-09", _date(1760000000000)
    assert _date("2026-10-14T09:00:00Z") == "2026-10-14"
    assert _format(True, "") == "remote"
    assert _format(None, "Remote - Europe") == "remote"
    assert _format(None, "Hybrid, Berlin") == "hybrid"
    assert _format(False, "Prague") == "office"
    assert worth_it("remote", "Jakarta")
    assert worth_it("office", "Tbilisi, Georgia")
    assert worth_it("hybrid", "Almaty")
    assert not worth_it("office", "Jakarta, Indonesia")
    assert not worth_it("unknown", "San Francisco, CA")
    print("ats: проверки прошли")
