"""Сбор вакансий с careerspace.app.

Кураторская подборка, а не поток: в `/collection/ux_ui` ровно десять вакансий,
пагинации нет. Зато компании сильные — Яндекс, Positive Technologies, Kaspi —
и формат работы указан прямо в карточке.

Всё остальное у них живёт за адресами с параметрами и за `/api`, а `robots.txt`
закрывает и то, и другое. Так что десять — это весь законный доступ.

Есть ещё `/collection/graphic_design`, но графдизайн отсекается общим фильтром
роли, и ходить туда значит тратить десять запросов впустую.

ЗАРПЛАТУ ОТСЮДА НЕ БЕРЁМ. Она проставлена у всех десяти вакансий, чего
не бывает: у hirehi вилку указывает каждая четвёртая. Одна и та же вилка
«206 000 - 268 000 ₽» встречается у разных компаний и в разных профессиях —
это оценка по грейду, а не то, что написал работодатель. Фильтровать людей
по выдуманным числам нельзя.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from html import unescape

from core import Vacancy

BASE = "https://careerspace.app"
COLLECTIONS = ["ux_ui"]

# Сайт не отвечает на нестандартные User-Agent, поэтому представляемся браузером.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

DELAY_SEC = 0.4

_CARD_RE = re.compile(r'<a[^>]+href="(/job/\d+)"[^>]*>(.*?)</a>', re.S)
_SCRIPT_RE = re.compile(r"(?s)<(script|style)[^>]*>.*?</\1>")
_TAG_RE = re.compile(r"<[^>]+>")
_SALARY_RE = re.compile(r"[\d\s]{4,}[-–][\d\s]{4,}₽|^(?:от|до)\s[\d\s]+₽")

FORMATS = {"удаленно": "remote", "удалённо": "remote",
           "гибрид": "hybrid", "офис": "office"}

# Грейд careerspace отдельным полем не даёт — вытаскиваем из заголовка.
GRADES = [
    (re.compile(r"\b(intern|стажёр|стажер)\b", re.I), "intern"),
    (re.compile(r"\b(junior|джуниор|младший)\b", re.I), "junior"),
    (re.compile(r"\b(middle|мидл)\b", re.I), "middle"),
    (re.compile(r"\b(senior|сеньор|старший|ведущий)\b", re.I), "senior"),
    (re.compile(r"\b(lead|head|тимлид|руководитель)\b", re.I), "lead"),
]


def _fetch(url: str, timeout: int = 30, tries: int = 3) -> str:
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


def _plain(html: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", _SCRIPT_RE.sub(" ", html))).split())


def _card_parts(inner: str) -> list[str]:
    """Карточка выглядит как «Компания | Роль | Город | Формат | Вилка»,
    причём формат и вилка бывают не у всех."""
    text = unescape(_TAG_RE.sub("|", inner))
    return [part.strip() for part in text.split("|") if part.strip()]


def _grade(title: str) -> str | None:
    found = [name for pattern, name in GRADES if pattern.search(title)]
    return ",".join(found) or None


def list_recent(since: str) -> list[tuple[str, list[str]]]:
    """Адреса вакансий вместе с разобранной карточкой: город и формат работы
    есть только в списке, на самой странице вакансии их нет."""
    out: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for slug in COLLECTIONS:
        try:
            html = _fetch(f"{BASE}/collection/{slug}")
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  ! подборка {slug} не открылась: {exc}")
            continue
        for href, inner in _CARD_RE.findall(html):
            if href in seen:
                continue
            seen.add(href)
            out.append((BASE + href, _card_parts(inner)))
        time.sleep(DELAY_SEC)
    return out


def parse_vacancy(url: str, parts: list[str], html: str) -> Vacancy | None:
    if len(parts) < 2:
        return None
    company, title = parts[0], parts[1]
    tail = parts[2:]

    city = None
    work_format = "unknown"
    for part in tail:
        low = part.lower()
        if low in FORMATS:
            work_format = FORMATS[low]
        elif _SALARY_RE.search(part):
            continue  # вилка у них оценочная, не берём
        elif city is None:
            city = part

    return Vacancy(
        id=f"careerspace:{url.rsplit('/', 1)[-1]}",
        source="careerspace",
        title=title,
        company=company,
        grade=_grade(title),
        salary=None,
        work_format=work_format,
        city=city,
        country=None,
        url=url,
        posted_at="",
        experience_months=None,
        description=_plain(html)[:4000],
        skills="",
    )


def collect(since: str, limit: int | None = None) -> list[Vacancy]:
    cards = list_recent(since)
    if limit:
        cards = cards[:limit]

    vacancies = []
    for index, (url, parts) in enumerate(cards):
        try:
            html = _fetch(url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  ! не открылась {url}: {exc}")
            continue
        vacancy = parse_vacancy(url, parts, html)
        if vacancy:
            vacancies.append(vacancy)
        if index < len(cards) - 1:
            time.sleep(DELAY_SEC)
    return vacancies
