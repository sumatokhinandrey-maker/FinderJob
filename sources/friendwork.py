"""Сбор вакансий с карьерных порталов на платформе FriendWork.

Портал — это SPA, но за ней открытый JSON: один запрос отдаёт сразу весь
портал целиком, без ключа и постраничной выдачи. Маршрут источника — token
портала (`skillbox`), он же первый сегмент адреса jobs.friend.work/skillbox/.

Чем ценен: «Центр развития карьеры Skillbox» публикует стажировки и практики,
которых в остальных источниках почти нет — на замере 28.08.2026 во всём
запасе из 349 вакансий стажировок было пять, здесь они идут потоком.

Правовая сторона чистая: robots.txt у api.friend.work нет вовсе, запрос
проходит и с нашим честным User-Agent, а портал публично адресован
соискателям — ровно наш сценарий.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from html import unescape

from core import Vacancy

API = "https://api.friend.work/Publication/CareerPortal?token="
PORTAL = "https://jobs.friend.work"
USER_AGENT = "JobResearchBot/0.1 (personal job digest; +https://github.com/)"
DELAY_SEC = 1.0

_FORMATS = {"remote": "remote", "office": "office", "hybrid": "hybrid"}

# Компания в отдельном поле пустая у всех публикаций, зато стоит в заголовке:
# «Игровой UX/UI дизайнер в IMT (удаленно, РФ)». Берём то, что между « в »
# и скобкой; без скобки — до конца строки.
_COMPANY_RE = re.compile(r"\sв\s+([^(]+?)\s*(?:\(|$)")
_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""")

# Отклик на портале рассматривают только с резюме по шаблону Skillbox,
# поэтому ищем в описании сайт работодателя — второй путь, написать напрямую.
# Мимо идут облака с самими шаблонами, сам Skillbox и почтовые ссылки.
_NOT_EMPLOYER = ("cloud.mail.ru", "drive.google.com", "docs.google.com",
                 "skillbox", "friend.work", "t.me", "mailto")


def _fetch(url: str, timeout: int = 30, tries: int = 2) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == tries - 1:
                raise
            time.sleep(1.5)
    return {}


def _plain(html: str) -> str:
    """Описание приходит разметкой — в фильтры и дайджест нужен текст."""
    return " ".join(unescape(_TAG_RE.sub(" ", html or "")).split())


def _employer_site(html: str) -> str | None:
    for url in _HREF_RE.findall(html or ""):
        url = unescape(url).strip()
        if url.startswith("http") and not any(bad in url.lower() for bad in _NOT_EMPLOYER):
            return url
    return None


def parse_vacancy(token: str, item: dict) -> Vacancy | None:
    job_id = item.get("publishedJobId")
    title = (item.get("title") or "").strip()
    if not job_id or not title:
        return None

    company = item.get("company") or ""
    if not company:
        match = _COMPANY_RE.search(title)
        company = match.group(1).strip() if match else ""

    # Вилка приходит нулями, когда её не указали: ноль — это «не указано»,
    # а не «платим ноль», иначе фильтр по зарплате отсечёт всё подряд.
    salary = item.get("salaryFrom") or None

    years = item.get("expYearsMin")

    return Vacancy(
        id=f"friendwork:{job_id}",
        source="friendwork",
        title=title,
        company=company,
        grade=None,  # грейд вычитает core.fill_grades из заголовка
        salary=salary,
        work_format=_FORMATS.get(str(item.get("workplaceType") or "").lower(), "unknown"),
        city=(item.get("city") or "").strip() or None,
        country=None,
        url=f"{PORTAL}/{token}/{job_id}",
        posted_at=str(item.get("publishDateTime") or "")[:10],
        experience_months=years * 12 if isinstance(years, int) else None,
        description=_plain(item.get("description") or ""),
        skills="",
        company_url=_employer_site(item.get("description") or ""),
    )


def collect(since: str, limit: int | None = None,
            routes: list[str] | None = None) -> list[Vacancy]:
    """Вакансии порталов из routes, опубликованные начиная с даты since.

    Один запрос отдаёт портал целиком, поэтому повторяющиеся токены
    (их даёт маршрутный лист, когда подписчики ищут несколько направлений)
    схлопываем — иначе сходим за одними и теми же данными трижды.
    """
    tokens = list(dict.fromkeys(routes or []))

    vacancies = []
    for index, token in enumerate(tokens):
        try:
            data = _fetch(API + token)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"  ! friend.work не отдал портал {token}: {exc}")
            continue

        for item in data.get("publications") or []:
            vacancy = parse_vacancy(token, item)
            if vacancy and vacancy.posted_at >= since:
                vacancies.append(vacancy)

        if index < len(tokens) - 1:
            time.sleep(DELAY_SEC)

    vacancies.sort(key=lambda v: v.posted_at, reverse=True)
    return vacancies[:limit] if limit else vacancies


if __name__ == "__main__":
    sample = {
        "publishedJobId": 125798,
        "company": None,
        "title": "Игровой UX/UI дизайнер в IMT (удаленно, РФ)",
        "city": "",
        "salaryFrom": 0,
        "salaryTo": 0,
        "expYearsMin": None,
        "workplaceType": "Remote",
        "publishDateTime": "2026-10-08T17:56:56.993",
        "description": (
            "<div>Шаблон резюме <a href='https://cloud.mail.ru/public/x'>тут</a>. "
            "Разработчик игр <a href='https://imt.games'>IMT</a>&nbsp;ищет</div>"
        ),
    }
    v = parse_vacancy("skillbox", sample)
    assert v.id == "friendwork:125798", v.id
    assert v.company == "IMT", v.company
    assert v.salary is None, v.salary
    assert v.work_format == "remote", v.work_format
    assert v.posted_at == "2026-10-08", v.posted_at
    assert v.url == "https://jobs.friend.work/skillbox/125798", v.url
    assert v.description.endswith("Разработчик игр IMT ищет"), repr(v.description)
    # Шаблон резюме в облаке стоит в описании раньше сайта компании —
    # берём именно сайт.
    assert v.company_url == "https://imt.games", v.company_url
    assert parse_vacancy("skillbox", dict(sample, description="нет ссылок")).company_url is None

    no_brackets = dict(sample, title="Стажировка для дизайнеров в Виноу")
    assert parse_vacancy("skillbox", no_brackets).company == "Виноу"
    assert parse_vacancy("skillbox", dict(sample, title="Дизайнер карточек")).company == ""
    assert parse_vacancy("skillbox", dict(sample, salaryFrom=65000)).salary == 65000
    print("friendwork: проверки прошли")
