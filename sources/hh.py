"""Сбор вакансий с hh.ru через публичную RSS-ленту поиска.

Почему именно RSS. Анонимный доступ к api.hh.ru закрыт (403 при любых
заголовках), а приложение регистрируют только под сценарии работодателя —
поддержка API для соискателей прекращена 15 декабря 2025. RSS же отдаётся
без токена и без логина.

Оговорка, которую надо помнить: пункт 8.8 пользовательского соглашения hh
запрещает читать информацию с сайта программными средствами, и исключения
для RSS там нет. Решение подключать принято Андреем осознанно 27.08.2026.
Поэтому ходим предельно скромно: несколько запросов в сутки с паузами.

Формат работы в ленте не указан вовсе — как и у SuperJob, из-за чего тот
источник мы отвергли. Здесь обходим иначе: не вытаскиваем формат из данных,
а спрашиваем у hh отдельно удалёнку и отдельно гибрид. Тогда формат известен
точно, потому что мы сами его задали.
"""

from __future__ import annotations

import html as html_mod
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from core import Vacancy

RSS = "https://hh.ru/search/vacancy/rss"
USER_AGENT = "JobResearchBot/0.1 (personal job digest; +https://github.com/)"

# hh отвечает 451 на частые запросы и отпускает через несколько секунд.
DELAY_SEC = 5
RETRIES = 2
AREA_RUSSIA = 113

# Что и в каком формате спрашиваем. Лента отдаёт 20 самых свежих на запрос
# и не листается, поэтому запросов немного и каждый узкий.
QUERIES = ["продуктовый дизайнер", "ux/ui дизайнер", "веб-дизайнер"]
FORMATS = {"REMOTE": "remote", "HYBRID": "hybrid"}

_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")

_COMPANY_RE = re.compile(r"Вакансия компании:\s*([^|]+)")
_CITY_RE = re.compile(r"Регион:\s*([^|]+)")
_SALARY_RE = re.compile(r"дохода:\s*([^|]+)")

GRADES = [
    (re.compile(r"\b(стажёр|стажер|intern)\b", re.I), "intern"),
    (re.compile(r"\b(junior|джуниор|младший)\b", re.I), "junior"),
    (re.compile(r"\b(middle|мидл)\b", re.I), "middle"),
    (re.compile(r"\b(senior|сеньор|старший)\b", re.I), "senior"),
    (re.compile(r"\b(lead|тимлид|руководитель|ведущий)\b", re.I), "lead"),
]


def _fetch(url: str, timeout: int = 25) -> str:
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            last = exc
            # 451 — «слишком часто», отпускает через несколько секунд.
            if exc.code != 451 or attempt == RETRIES:
                raise
            time.sleep(DELAY_SEC * (attempt + 2))
        except Exception as exc:
            last = exc
            if attempt == RETRIES:
                raise
            time.sleep(DELAY_SEC)
    raise last if last else RuntimeError(f"не удалось получить {url}")


def _tag(item: str, name: str) -> str:
    match = re.search(rf"<{name}>(.*?)</{name}>", item, re.S)
    return html_mod.unescape(match.group(1)).strip() if match else ""


def _salary(text: str) -> int | None:
    """«от 150 000 до 200 000 ₽» → 150000. «не указан» → None."""
    if not text or "не указан" in text:
        return None
    numbers = re.findall(r"\d[\d\s ]*", text)
    if not numbers:
        return None
    try:
        return int(numbers[0].replace(" ", "").replace(" ", ""))
    except ValueError:
        return None


def _grade(title: str) -> str | None:
    found = [name for pattern, name in GRADES if pattern.search(title)]
    return ",".join(found) or None


def parse_item(item: str, work_format: str) -> Vacancy | None:
    link = _tag(item, "link")
    if not link:
        return None
    description = html_mod.unescape(_TAG_RE.sub(" | ", _tag(item, "description")))
    title = _tag(item, "title")

    def field(pattern):
        match = pattern.search(description)
        return match.group(1).strip() if match else None

    published = _tag(item, "pubDate")[:10]
    try:
        datetime.strptime(published, "%Y-%m-%d")
    except ValueError:
        published = ""

    return Vacancy(
        id=f"hh:{link.rsplit('/', 1)[-1]}",
        source="hh",
        title=title,
        company=field(_COMPANY_RE) or "",
        grade=_grade(title),
        salary=_salary(field(_SALARY_RE) or ""),
        work_format=work_format,
        city=field(_CITY_RE),
        country=None,
        url=link,
        posted_at=published,
        experience_months=None,
        description=description,
        skills="",
    )


def collect(since: str, limit: int | None = None) -> list[Vacancy]:
    """Лента отдаёт 20 свежих на запрос и не листается, поэтому since здесь
    не работает — новизну определяет память о показанном."""
    seen: set[str] = set()
    vacancies: list[Vacancy] = []
    requests = [(q, code, fmt) for q in QUERIES for code, fmt in FORMATS.items()]

    for index, (query, code, work_format) in enumerate(requests):
        params = urllib.parse.urlencode(
            {"text": query, "area": AREA_RUSSIA, "work_format": code}
        )
        try:
            body = _fetch(f"{RSS}?{params}")
        except Exception as exc:
            print(f"  ! hh не отдал «{query}» ({work_format}): {exc}")
            continue

        for item in _ITEM_RE.findall(body):
            vacancy = parse_item(item, work_format)
            if vacancy and vacancy.id not in seen:
                seen.add(vacancy.id)
                vacancies.append(vacancy)

        if index < len(requests) - 1:
            time.sleep(DELAY_SEC)

    return vacancies[:limit] if limit else vacancies
