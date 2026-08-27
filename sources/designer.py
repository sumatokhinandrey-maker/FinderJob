"""Сбор вакансий с designer.ru (раздел `/u/`).

Доска для дизайнеров на Bitrix, отдаёт готовый HTML — headless-браузер
не нужен. `robots.txt` раздел `/u/` разрешает явно (`Allow: /u/`), поэтому
ходим спокойно, как обычный читатель.

Доска общая: рядом с продуктовым дизайном лежат интерьер, полиграфия и моушн.
Отсеивает их наш общий фильтр роли и стоп-слов, здесь ничего не режем.

Карточка устроена своеобразно: заголовок живёт в `alt` картинки строкой
«Компания ищет роль», а дата, формат работы и город — отдельными текстовыми
узлами. Из `alt` данные брать надёжнее: узлы у разных карточек перемешаны.

Грейд и вилка в ленте не показываются вовсе — они на странице вакансии,
полями «Уровень дизайнера» и «Зарплата». Ходим туда только за теми, кто
прошёл по дате: за сутки это несколько карточек, а не вся лента из ста
восьмидесяти.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from html import unescape

from core import Vacancy

BASE = "https://designer.ru/u/"
MAX_PAGES = 3
DELAY_SEC = 0.4

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

_CARD_RE = re.compile(r'id="bx_\d+_\d+"')
_SLUG_RE = re.compile(r'href="/u/([a-z0-9-]+)/"')
_ALT_RE = re.compile(r'alt="([^"]*)"')
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"(?s)<(script|style)[^>]*>.*?</\1>")

# «Компания ищет UX/UI-дизайнера» — слева от глагола компания, справа роль.
_SPLIT_RE = re.compile(r"^(.*?)\s+(?:ищет|ищут|в поиске|в поисках)\s+(.+)$", re.I)

# Заголовок карточки склоняется — «ищет UX/UI-дизайнера». В дайджесте это
# читается коряво, поэтому возвращаем должность в именительный падеж.
# Список короткий и покрывает всё, что встречается на доске.
_CASE_RE = re.compile(
    r"\b(дизайнер|директор|иллюстратор|аниматор|редактор|художник|"
    r"разработчик|менеджер)(?:а|ов|у|ам|ами)\b",
    re.I,
)
_CASE_SOFT_RE = re.compile(r"\b(исследовател|руководител)(?:я|ей|ю|ям)\b", re.I)

_DATE_RE = re.compile(r"^(\d{1,2})\s+([а-яё]{3})\.?$", re.I)
_CITY_RE = re.compile(r"\(([^)]+)\)")

MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5,
          "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}

# Формат пишут по-разному: «Удаленка», «Удаленка или Офис», «Офис (Ростов)».
# Удалёнка в паре с офисом — это гибрид, как его ни назови.
_REMOTE_RE = re.compile(r"удал[её]нк|remote", re.I)
_OFFICE_RE = re.compile(r"\bофис", re.I)
_HYBRID_RE = re.compile(r"гибрид", re.I)

GRADES = [
    (re.compile(r"\b(intern|стажёр|стажер|trainee)\b", re.I), "intern"),
    (re.compile(r"\b(junior|джуниор|джун|младший)\b", re.I), "junior"),
    (re.compile(r"\b(middle|мидл|миддл)\b", re.I), "middle"),
    (re.compile(r"\b(senior|сеньор|синьор|старший)\b", re.I), "senior"),
    (re.compile(r"\b(lead|head|тимлид|ведущий|руководитель|директор)\b", re.I), "lead"),
]

_SALARY_RE = re.compile(r"\d[\d\s ]{3,}")


def _fetch(url: str, timeout: int = 30, tries: int = 2) -> str:
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
                time.sleep(1.5)
    raise last if last else RuntimeError(f"не удалось получить {url}")


def _texts(block: str) -> list[str]:
    """Текстовые узлы карточки в порядке появления, без повторов."""
    out: list[str] = []
    plain = _TAG_RE.sub("\n", _SCRIPT_RE.sub(" ", block))
    for chunk in plain.split("\n"):
        value = " ".join(unescape(chunk).split())
        if value and value not in out:
            out.append(value)
    return out


def _parse_date(value: str) -> str:
    """«сегодня», «вчера» или «25 авг» → YYYY-MM-DD. Год в ленте не пишут:
    если вышло «в будущем» (декабрь, когда на дворе январь) — это прошлый год."""
    today = date.today()
    low = value.lower()
    if "сегодня" in low:
        return today.isoformat()
    if "вчера" in low:
        return (today - timedelta(days=1)).isoformat()
    match = _DATE_RE.match(value)
    if not match:
        return ""
    month = MONTHS.get(match.group(2).lower())
    if not month:
        return ""
    day = int(match.group(1))
    try:
        posted = date(today.year, month, day)
    except ValueError:
        return ""
    if posted > today:
        posted = date(today.year - 1, month, day)
    return posted.isoformat()


def _grade(title: str) -> str | None:
    found = [name for pattern, name in GRADES if pattern.search(title)]
    return ",".join(found) or None


def _salary(text: str) -> int | None:
    """Нижняя граница вилки. «от 150 000 ₽», «150 000 - 250 000» — берём меньшее."""
    numbers = [
        int(re.sub(r"\D", "", raw))
        for raw in _SALARY_RE.findall(text or "")
        if len(re.sub(r"\D", "", raw)) >= 4
    ]
    numbers = [n for n in numbers if 10_000 <= n <= 2_000_000]
    return min(numbers) if numbers else None


def _field(html: str, label: str) -> str:
    """Значение поля со страницы вакансии: первый непустой текст после метки."""
    index = html.find(label)
    if index < 0:
        return ""
    tail = _TAG_RE.sub("\n", html[index + len(label): index + len(label) + 300])
    for chunk in tail.split("\n"):
        value = " ".join(unescape(chunk).split()).lstrip(": ").strip()
        if value:
            return value
    return ""


def _parse_card(block: str) -> dict | None:
    match = _SLUG_RE.search(block)
    if not match:
        return None
    slug = match.group(1)

    alt = " ".join(unescape((_ALT_RE.search(block) or [None, ""])[1]).split())
    parts = _SPLIT_RE.match(alt)
    company, title = ("", alt)
    if parts:
        company = parts.group(1).strip()
        title = _CASE_RE.sub(r"\1", parts.group(2).strip())
        title = _CASE_SOFT_RE.sub(r"\1ь", title)
        title = title[:1].upper() + title[1:]

    texts = _texts(block)
    posted_at = ""
    work_format = "unknown"
    city = None
    for text in texts:
        if not posted_at:
            posted_at = _parse_date(text)
        if len(text) > 70:
            continue  # это уже описание, а не подпись под карточкой
        if _HYBRID_RE.search(text) or (_REMOTE_RE.search(text) and _OFFICE_RE.search(text)):
            work_format = "hybrid"
        elif _REMOTE_RE.search(text):
            work_format = "remote"
        elif _OFFICE_RE.search(text):
            work_format = "office"
        found = _CITY_RE.search(text)
        if found and city is None:
            city = found.group(1).strip()

    description = " ".join(text for text in texts if len(text) > 70)
    return {
        "slug": slug,
        "title": title,
        "company": company,
        "city": city,
        "work_format": work_format,
        "posted_at": posted_at,
        "description": description[:4000],
    }


def list_recent(since: str) -> list[dict]:
    """Карточки со всех страниц ленты, свежее даты since.

    Лента отсортирована новыми вперёд, поэтому как только страница целиком
    оказалась старее нужной даты — дальше не идём."""
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        url = BASE if page == 1 else f"{BASE}?PAGEN_1={page}"
        try:
            html = _fetch(url)
        except (urllib.error.URLError, TimeoutError, urllib.error.HTTPError) as exc:
            print(f"  ! страница {page} designer.ru не открылась: {exc}")
            break

        fresh_on_page = 0
        for block in _CARD_RE.split(html)[1:]:
            card = _parse_card(block)
            if not card or card["slug"] in seen:
                continue
            seen.add(card["slug"])
            # Дата бывает нечитаемой — такую карточку не выбрасываем,
            # пусть решает фильтр: лучше лишняя строка, чем потерянная вакансия.
            if card["posted_at"] and card["posted_at"] < since:
                continue
            fresh_on_page += 1
            out.append(card)

        if not fresh_on_page:
            break
        time.sleep(DELAY_SEC)
    return out


def _enrich(card: dict) -> tuple[str | None, int | None]:
    """Грейд и вилка со страницы вакансии. Ошибка страницы не должна
    ронять источник — возвращаем то, что есть."""
    try:
        html = _fetch(f"{BASE}{card['slug']}/")
    except Exception as exc:
        print(f"  ! карточка {card['slug']} не открылась: {exc}")
        return None, None

    grade = None
    level = _field(html, "Уровень дизайнера")
    if level and not re.search(r"не важно|любой|не указан", level, re.I):
        grade = _grade(level)

    salary = None
    money = _field(html, "Зарплата")
    if money and "не указан" not in money.lower():
        salary = _salary(money)
    return grade, salary


def collect(since: str, limit: int | None = None) -> list[Vacancy]:
    cards = list_recent(since)
    if limit:
        cards = cards[:limit]

    vacancies = []
    for index, card in enumerate(cards):
        grade = _grade(card["title"])
        salary = None
        if grade is None:
            grade, salary = _enrich(card)
            if index < len(cards) - 1:
                time.sleep(DELAY_SEC)

        vacancies.append(
            Vacancy(
                id=f"designer:{card['slug']}",
                source="designer",
                title=card["title"],
                company=card["company"],
                grade=grade,
                salary=salary,
                work_format=card["work_format"],
                city=card["city"],
                country=None,
                url=f"{BASE}{card['slug']}/",
                posted_at=card["posted_at"],
                experience_months=None,
                description=card["description"],
                skills="",
            )
        )
    return vacancies
