"""Сбор вакансий с LinkedIn через гостевой эндпоинт.

`jobs-guest/jobs/api/seeMoreJobPostings/search` отдаёт HTML-фрагмент с
карточками так, как их видит незалогиненный посетитель: аккаунт не участвует,
ключей нет, по десять вакансий на страницу.

**Что важно знать до того, как включать.**

- **По России индекс пуст.** Проверено 15.10.2026: `location=Russia` отдаёт
  ноль карточек, кириллица почти не индексируется. Поэтому запросы только
  английские, а место — Worldwide.
- **Worldwide значит местный найм за рубежом.** В выдаче Торонто, Сидней,
  Сан-Франциско, Лондон, Бангалор; даже с фильтром «удалённо» (`f_WT=2`)
  это удалёнка внутри своей страны, где нужно разрешение на работу.
  Поэтому источник живёт в `core.OPT_IN_SOURCES` — приходит лишь тому,
  кто выбрал его сам.
- **Их соглашение (п. 8.2) запрещает сбор скриптами**, и LinkedIn, в отличие
  от прочих, судится со скраперами. Андрей предупреждён, решение его.
- **С раннера GitHub Actions может не работать вовсе:** LinkedIn блокирует
  адреса дата-центров. С домашней машины ответ приходит; как будет в Actions,
  покажет первый прогон. Отказ не ломает рассылку — источник просто промолчит.
"""

from __future__ import annotations

import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

from core import Vacancy

API = ("https://www.linkedin.com/jobs-guest/jobs/api/"
       "seeMoreJobPostings/search")
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
PAGE = 10
# Две страницы вместо четырёх: двадцати свежих на запрос хватает, а каждая
# лишняя страница — лишний повод получить 429.
MAX_PAGES = 2
# Адрес раннера LinkedIn не режет — проверено 15.10.2026 прямо из Actions,
# HTTP 200 с первой попытки. Упирались мы только в частоту: дюжина запросов
# подряд с паузой 0,8 секунды давала 429 уже на втором слове. Поэтому ритм
# медленный, он всё равно дешевле, чем молчащий источник.
DELAY_SEC = 3.0
QUERY_PAUSE_SEC = 5.0
# 429 значит «подожди», а не «уходи»: один раз отступаем надолго и пробуем
# снова, и только потом сдаёмся до следующего прогона.
BACKOFF_SEC = 45.0
# Два отступления — предел: если лимит держится и после них, источник
# молчит до следующего прогона. Продавливать ограничение мы не будем.
MAX_BACKOFFS = 2
MAX_QUERIES = 3
LOCATION = "Worldwide"

# Места задаём geoId, а не словами: строковые названия двусмысленны —
# «Georgia» LinkedIn понимает как штат США и отдаёт Атланту, «Armenia»
# как город в Колумбии. Каждый id сверен живой выдачей 15.10.2026; один
# из проверявшихся (104282357, якобы Армения) отдал австрийский Линц
# и в список не попал — ради этого сверка и делалась.
GEO = {
    "Worldwide": "92000000",
    "Грузия": "106315325",
    "Казахстан": "106049128",
    "Сербия": "101855366",
    "Кипр": "106774002",
    "ОАЭ": "104305776",
    "Турция": "102105699",
}

# Разбираем по карточкам-<li>, а не отдельными списками полей: у части
# вакансий нет компании или даты, и при сопоставлении по позициям поля
# разъезжаются — чужая компания приклеивается к чужому заголовку. Способ
# подсмотрен в sasaki-agent, где LinkedIn работает в семи прогонах из десяти.
_LINK_RE = re.compile(r'href="(https://[a-z]{2,3}\.linkedin\.com/jobs/view/[^"?]+)')
_ID_RE = re.compile(r"-(\d+)$")
_TITLE_RE = re.compile(r'base-search-card__title">([\s\S]*?)</h3>')
_COMPANY_RE = re.compile(r'base-search-card__subtitle">([\s\S]*?)</h4>')
_LOCATION_RE = re.compile(r'job-search-card__location">([\s\S]*?)</span>')
_DATE_RE = re.compile(r'datetime="([^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(match: re.Match | None) -> str:
    if not match:
        return ""
    return " ".join(unescape(_TAG_RE.sub("", match.group(1))).split())


def _fetch(query: str, start: int, since_days: int, timeout: int = 25,
           geo_id: str | None = None) -> str:
    where = {"geoId": geo_id} if geo_id else {"location": LOCATION}
    params = urllib.parse.urlencode({
        "keywords": query,
        **where,
        "f_TPR": f"r{since_days * 86400}",
        "start": str(start),
    })
    req = urllib.request.Request(f"{API}?{params}",
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Сколько ждать после отказа.

    Если сервис назвал срок в Retry-After — слушаемся его, своих догадок
    тут быть не должно. Иначе удваиваем задержку и добавляем разброс, чтобы
    несколько запросов не возвращались к нему одной пачкой.
    """
    header = exc.headers.get("Retry-After") if exc.headers else None
    if header:
        try:
            return min(float(header), 120.0)
        except ValueError:
            pass
    return BACKOFF_SEC * (2 ** attempt) * (0.7 + random.random() * 0.6)


def parse_cards(html: str) -> list[Vacancy]:
    out = []
    for block in re.split(r"<li[ >]", html)[1:]:
        link = _LINK_RE.search(block)
        if not link:
            continue
        url = link.group(1)
        found = _ID_RE.search(url)
        title = _clean(_TITLE_RE.search(block))
        if not found or not title:
            continue

        date_match = _DATE_RE.search(block)
        out.append(Vacancy(
            id=f"linkedin:{found.group(1)}",
            source="linkedin",
            title=title,
            company=_clean(_COMPANY_RE.search(block)),
            grade=None,
            salary=None,
            work_format="unknown",
            city=_clean(_LOCATION_RE.search(block)) or None,
            country=None,
            url=url,
            posted_at=(date_match.group(1)[:10] if date_match else ""),
            experience_months=None,
            description="",
            skills="",
        ))
    return out


def collect(since: str, limit: int | None = None,
            routes: list[str] | None = None) -> list[Vacancy]:
    """Во время рассылки берём накопленный запас, а не ходим в LinkedIn.

    Он отвечает на редкие запросы, а рассылка идёт сплошняком и ловит 429 —
    поэтому собирает его отдельный прогон (`linkedin_sync.py`) несколько раз
    в сутки. Если запаса нет, честно возвращаем пусто: лезть в сеть посреди
    рассылки значит получить отказ и подвести всех остальных.
    """
    from core import from_rows
    from finderjob import store

    rows = [row for row in store.load_linkedin()
            if (row.get("posted_at") or "") >= since]
    if not rows:
        print("  ! запас linkedin пуст — его наполняет отдельный прогон")
        return []
    return _sorted(from_rows(rows), limit)


def fetch(since: str, limit: int | None = None,
          routes: list[str] | None = None,
          geo_ids: list[str] | None = None) -> list[Vacancy]:
    """Настоящий обход LinkedIn. Зовётся только отдельным сборщиком.

    routes — английские поисковые запросы: русские LinkedIn ищет плохо,
    зато в странах релокации сам отдаёт вакансии с русскими заголовками.
    """
    from datetime import date

    queries = list(dict.fromkeys(routes or []))[:MAX_QUERIES]
    places = geo_ids or [GEO["Worldwide"]]
    try:
        days = max((date.today() - date.fromisoformat(since)).days, 1)
    except ValueError:
        days = 7

    seen: set[str] = set()
    vacancies = []
    backed_off = 0
    for geo_id in places:
        for query in queries:
            for page in range(MAX_PAGES):
                try:
                    html = _fetch(query, page * PAGE, days, geo_id=geo_id)
                except urllib.error.HTTPError as exc:
                    if exc.code == 429 and backed_off < MAX_BACKOFFS:
                        pause = _retry_after(exc, backed_off)
                        backed_off += 1
                        print(f"  ! linkedin просит подождать на «{query}» — "
                              f"жду {pause:.0f} с")
                        time.sleep(pause)
                        continue
                    note = (" — ограничение устойчивое, прекращаю"
                            if exc.code == 429 else "")
                    print(f"  ! linkedin не отдал «{query}»: HTTP {exc.code}{note}")
                    if exc.code in (429, 999):
                        return _sorted(vacancies, limit)
                    break
                except (urllib.error.URLError, TimeoutError) as exc:
                    print(f"  ! linkedin не отдал «{query}»: {exc}")
                    break

                cards = parse_cards(html)
                for vacancy in cards:
                    if vacancy.id in seen:
                        continue
                    seen.add(vacancy.id)
                    vacancies.append(vacancy)

                if len(cards) < PAGE:
                    break
                time.sleep(DELAY_SEC)
            time.sleep(QUERY_PAUSE_SEC)

    return _sorted(vacancies, limit)


def _sorted(vacancies: list[Vacancy], limit: int | None) -> list[Vacancy]:
    vacancies.sort(key=lambda v: v.posted_at, reverse=True)
    return vacancies[:limit] if limit else vacancies


if __name__ == "__main__":
    html = '''<ul>
    <li>
      <a href="https://ca.linkedin.com/jobs/view/senior-product-designer-at-sharegate-4312345678?refId=x">
      <h3 class="base-search-card__title">
        Senior Product Designer
      </h3>
      <h4 class="base-search-card__subtitle"><a class="x" href="y">
        ShareGate &amp; Co
      </a></h4>
      <span class="job-search-card__location">
        Toronto, Ontario, Canada
      </span>
      <time class="job-search-card__listdate" datetime="2026-10-10">2 days ago</time>
    </li>
    <li>
      <a href="https://www.linkedin.com/jobs/view/no-title-here-4399999999">
      <span class="job-search-card__location">Sydney</span>
    </li>
    </ul>'''
    cards = parse_cards(html)
    # Вторая карточка без заголовка отброшена, и это не сдвинуло поля первой —
    # ради этого разбор и идёт по блокам, а не по позициям.
    assert len(cards) == 1, cards
    card = cards[0]
    assert card.id == "linkedin:4312345678", card.id
    assert card.title == "Senior Product Designer", card.title
    assert card.company == "ShareGate & Co", card.company
    assert card.city == "Toronto, Ontario, Canada", card.city
    assert card.posted_at == "2026-10-10", card.posted_at
    assert card.url.endswith("4312345678"), card.url
    assert parse_cards("<html>пусто</html>") == []
    print("linkedin: проверки прошли")
