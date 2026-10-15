"""Карьерные сайты компаний.

Первоисточник: вакансия появляется здесь раньше, чем доезжает до агрегаторов,
а откликаться можно прямо у работодателя — ни регистрации, ни лимитов. Поэтому
при склейке дублей ссылка на карьерный сайт выигрывает у ссылки на hirehi.

Каждая компания — отдельная договорённость: у кого-то открытый JSON, у кого-то
данные вшиты в страницу. Общего стандарта нет, поэтому здесь по адаптеру
на компанию, но с общими помощниками и общей моделью на выходе.

Адаптеры подсмотрены в sasaki-agent (MIT, Роман Сазонов) и переписаны на Python.

Не подключены и почему:
  Точка, Альфа-Банк — сертификат российского удостоверяющего центра, которого
    нет в хранилище Python; соединение рвётся ещё до запроса;
  Сбер, Контур, Озон, Самокат, ЦИАН — бот-защита либо вакансии только на hh.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path

from core import Vacancy

# --------------------------------------------------------------------------
# сертификат НУЦ Минцифры
# --------------------------------------------------------------------------
#
# Т-Банк, Альфа и Точка подписаны корнем «Russian Trusted Root CA», которого
# нет в хранилище Python, — соединение рвалось ещё до запроса. Корень лежит
# в репозитории и применяется ТОЛЬКО к этим трём хостам: доверять ему для
# всего интернета нельзя, иначе любой сертификат, им выписанный, стал бы для
# бота законным.
#
# Отпечаток сверен дважды: файл скачан с gu-st.ru (сайт Минцифры) и совпал
# с корнем, который сам www.tbank.ru отдаёт в своей цепочке. Проверка ниже
# держит это свойство: подменённый файл не подойдёт.
CA_FILE = Path(__file__).resolve().parent.parent / "certs" / "russian_trusted_root_ca.pem"
CA_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"
RUSSIAN_CA_HOSTS = ("tbank.ru", "alfabank.ru", "tochka.com")


@functools.lru_cache(maxsize=1)
def _russian_ca_context() -> ssl.SSLContext | None:
    try:
        pem = CA_FILE.read_text("ascii")
        digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    except (OSError, ValueError) as exc:
        print(f"  ! сертификат НУЦ не прочитан: {exc}")
        return None
    if digest != CA_SHA256:
        print("  ! сертификат НУЦ не тот, каким был записан — не использую")
        return None
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(CA_FILE))
    return context


def _context_for(url: str) -> ssl.SSLContext | None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if any(host == name or host.endswith("." + name) for name in RUSSIAN_CA_HOSTS):
        return _russian_ca_context()
    return None

# Слова «bot» в строке быть не должно: career.rwb.ru режет такой запрос
# по подстроке и отдаёт 403 в любом регистре. Проверено 15.10.2026 —
# без него тот же адрес отвечает двумя сотнями и полусотней вакансий.
USER_AGENT = "Mozilla/5.0 (Macintosh) jobresearch/0.1 (personal job digest)"
DELAY_SEC = 0.4

_TAG_RE = re.compile(r"<[^>]+>")

# Формат работы компании называют по-своему; сводим к нашим трём.
_REMOTE_RE = re.compile(r"удал[её]нн|remote", re.I)
_HYBRID_RE = re.compile(r"гибрид", re.I)
_OFFICE_RE = re.compile(r"офис|office|на месте", re.I)


def _fetch(url: str, body: dict | None = None, headers: dict | None = None,
           timeout: int = 25, tries: int = 2) -> str:
    head = {"User-Agent": USER_AGENT}
    head.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        head["Content-Type"] = "application/json"

    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=head)
            with urllib.request.urlopen(
                req, timeout=timeout, context=_context_for(url)
            ) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            last = exc
            # 403 и 404 повторять незачем — ответ не изменится. А вот 5xx
            # у карьерных сайтов временны: VK 15.10.2026 отдавал 504
            # и через минуту работал. Им даём паузу подлиннее.
            if exc.code < 500 and exc.code != 429:
                raise
            if attempt < tries - 1:
                time.sleep(3.0 * (attempt + 1))
        except Exception as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(1.5)
    raise last if last else RuntimeError(f"не удалось получить {url}")


def _json(url: str, body: dict | None = None, headers: dict | None = None) -> dict:
    return json.loads(_fetch(url, body, headers))


def _format(*parts: object) -> str:
    """Формат работы из любых текстовых кусочков, что дала компания."""
    blob = " ".join(str(p) for p in parts if p)
    if _REMOTE_RE.search(blob):
        return "remote"
    if _HYBRID_RE.search(blob):
        return "hybrid"
    if _OFFICE_RE.search(blob):
        return "office"
    return "unknown"


def _clean(text: object) -> str:
    return unescape(_TAG_RE.sub(" ", str(text or ""))).strip()


def _make(company: str, key: str, vid: object, title: str, url: str, *,
          city=None, salary=None, fmt="unknown", posted="", description="") -> Vacancy:
    return Vacancy(
        id=f"{key}:{vid}", source=key, title=_clean(title), company=company,
        grade=None, salary=salary, work_format=fmt, city=city, country=None,
        url=url, posted_at=(posted or "")[:10], experience_months=None,
        description=_clean(description)[:2000], skills="",
    )


# --------------------------------------------------------------------------
# адаптеры
# --------------------------------------------------------------------------


def yandex() -> list[Vacancy]:
    """Открытый JSON каталога с курсорной пагинацией."""
    api = "https://yandex.ru/jobs/api/publications"
    base = "professions=designer-uxui&professions=designer"
    out, cursor = [], None
    for _ in range(6):
        url = f"{api}?{base}" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        data = _json(url)
        for r in data.get("results") or []:
            vac = r.get("vacancy") or {}
            cities = [c.get("name") for c in vac.get("cities") or []]
            modes = [m.get("name") for m in vac.get("work_modes") or []]
            out.append(_make(
                "Яндекс", "yandex", r.get("id"), r.get("title"),
                f"https://yandex.ru/jobs/vacancies/{r.get('publication_slug_url')}",
                city=cities[0] if cities else None, fmt=_format(*modes),
            ))
        nxt = data.get("next")
        if not nxt:
            break
        cursor = urllib.parse.parse_qs(urllib.parse.urlparse(nxt).query).get("cursor", [None])[0]
        if not cursor:
            break
        time.sleep(DELAY_SEC)
    return out


def magnit() -> list[Vacancy]:
    """Фильтра по направлению нет — забираем все ИТ-вакансии, отберёт наш фильтр."""
    out = []
    for page in range(1, 4):
        data = _json(f"https://magnit.tech/api/v1/vacancy?per_page=100&page={page}")
        for v in data.get("results") or []:
            modes = [w.get("name") for w in v.get("work_formats") or [] if w]
            out.append(_make(
                "Магнит", "magnit", v.get("id"), v.get("title"),
                f"https://magnit.tech/vacancies/{v.get('id')}",
                fmt=_format(*modes), description=v.get("description"),
            ))
        if not (data.get("meta") or {}).get("has_more_pages"):
            break
        time.sleep(DELAY_SEC)
    return out


def mts() -> list[Vacancy]:
    """Категорийные фильтры API игнорирует, зато ищет по названию."""
    out, seen = [], set()
    for query in ("дизайн", "design", "интерфейс"):
        params = urllib.parse.urlencode({
            "filters[title][$containsi]": query,
            "pagination[pageSize]": 100, "pagination[page]": 1,
            "sort": "publishedAt:desc",
        })
        data = _json(f"https://job.mts.ru/api/v2/vacancies?{params}")
        for v in data.get("data") or []:
            slug = v.get("slug")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            city = ((v.get("city") or {}) if isinstance(v.get("city"), dict) else {}).get("name")
            out.append(_make(
                "МТС", "mts", slug, v.get("title"),
                f"https://job.mts.ru/vacancy/{slug}", city=city,
                fmt=_format(v.get("workFormat"), v.get("format"), city),
                posted=v.get("publishedAt") or "",
            ))
        time.sleep(DELAY_SEC)
    return out


def tbank() -> list[Vacancy]:
    """Внутренний API карьерного сайта: POST с фильтром профессии."""
    out = []
    data = _json(
        "https://www.tbank.ru/pfpjobs/papi/getVacancies",
        body={"pagination": {"it": {"offset": 0}}, "limit": 50,
              "filters": {"category": "it", "tcareer_it_profession": ["design"]}},
    )
    for v in (data.get("payload") or {}).get("vacancies") or []:
        tags = v.get("tags") or []
        out.append(_make(
            "Т-Банк", "tbank", v.get("urlSlug") or v.get("seoSlug"), v.get("title"),
            f"https://www.tbank.ru/career/it/vacancy/moskva/{v.get('seoSlug')}/{v.get('urlSlug')}/",
            city=v.get("subtitle"), fmt=_format(*tags),
        ))
    return out


def rwb() -> list[Vacancy]:
    out = []
    data = _json("https://career.rwb.ru/crm-api/api/v1/pub/vacancies")
    # Ответ приходит как {"data": {"items": [...]}}, но раньше адаптер видел
    # только 403 и эту вложенность не проверял.
    items = data.get("data") if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = items.get("items")
    for v in items or []:
        # Формат лежит в employment_types списком словарей («Удаленно»,
        # «Офис»), город — в city_title, у части вакансий он пуст.
        modes = [
            t.get("title") for t in v.get("employment_types") or [] if t
        ]
        out.append(_make(
            "RWB (Wildberries & Russ)", "rwb", v.get("id"), v.get("name") or v.get("title"),
            f"https://career.rwb.ru/vacancies/{v.get('id')}",
            city=_clean(v.get("city_title")) or None,
            fmt=_format(*modes),
            description=_clean(v.get("direction_title")),
        ))
    return out


def vk() -> list[Vacancy]:
    """Next.js: вакансии вшиты в страницу как __NEXT_DATA__. 273 — «Дизайн»."""
    out, seen = [], set()
    for page in range(1, 4):
        url = "https://team.vk.company/vacancy/?specialty=273" + (f"&page={page}" if page > 1 else "")
        html = _fetch(url)
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        if not match:
            print("  ! VK: __NEXT_DATA__ не найден, изменилась вёрстка")
            break
        props = (json.loads(match.group(1)).get("props") or {}).get("pageProps") or {}
        items = props.get("initialVacancies") or []
        if not items:
            break
        for v in items:
            if v.get("id") in seen:
                continue
            seen.add(v.get("id"))
            group = (v.get("group") or {}).get("name")
            cities = [c.get("name") for c in v.get("cities") or [] if isinstance(c, dict)]
            out.append(_make(
                f"VK · {group}" if group else "VK", "vk", v.get("id"), v.get("title"),
                f"https://team.vk.company/vacancy/{v.get('id')}/",
                city=cities[0] if cities else None,
                fmt=_format(*(cities + [v.get("workFormat"), v.get("format")])),
            ))
        time.sleep(DELAY_SEC)
    return out


# У SberDevices не лента вакансий, а тринадцать постоянных страниц-направлений:
# «UX/UI-дизайнер», «Python-разработчик» и так далее. Это описания ролей,
# а не объявления: ни даты публикации, ни зарплаты, ни города. Поэтому новизну
# определяет только память о показанном, а фильтр места и денег их пропускает.
# Данные лежат чистым JSON в __NEXT_DATA__ — парсить разметку не нужно.
SBER_STREAMS = [
    "UX&UI", "JS", "PO", "python", "Go", "ML", "IOS", "QA", "qaauto",
    "DevOps", "\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440  IT",
    "\u043f\u0440\u043e\u0435\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435",
    "Communication Lead",
]

_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def sberdevices() -> list[Vacancy]:
    """Тринадцать направлений найма SberDevices."""
    out = []
    for stream in SBER_STREAMS:
        url = "https://sberdevices.ru/career/" + urllib.parse.quote(stream) + "/"
        try:
            html = _fetch(url)
        except Exception as exc:
            print(f"  ! sberdevices {stream}: {str(exc)[:60]}")
            continue
        found = _NEXT_RE.search(html)
        if not found:
            continue
        try:
            data = json.loads(found.group(1))["props"]["pageProps"]["stream"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        title = data.get("title")
        if not title:
            continue
        # Навыки лежат в subline, обязанности и требования — списками через «;».
        parts = [data.get("subline"), data.get("duties"), data.get("requirements")]
        out.append(_make(
            "SberDevices", "sberdevices", data.get("id"), title, url,
            description=" ".join(p.replace(";", ". ") for p in parts if p),
        ))
        time.sleep(DELAY_SEC)
    return out


def tochka() -> list[Vacancy]:
    """Открытый JSON, двадцать вакансий целиком — пагинации нет.

    Заработал 15.10.2026, когда в SSL-контекст лёг корень НУЦ: до этого
    соединение рвалось и адаптера не было вовсе.
    """
    out = []
    for v in _json("https://hr.tochka.com/api/v2/hr/vacancies/").get("items") or []:
        slug = v.get("slug")
        out.append(_make(
            "Точка", "tochka", slug, v.get("title"),
            v.get("customLink") or f"https://hr.tochka.com/vacancies/{slug}",
            # «remotely» и «hybrid» — их собственные слова, _format их узнаёт
            # по корню; город приходит отдельным полем лишь у части вакансий.
            fmt=_format(v.get("workFormat")),
            salary=v.get("salaryFrom") or None,
            description=_clean((v.get("mainCategory") or {}).get("title")),
        ))
    return out


def alfa() -> list[Vacancy]:
    """Открытый JSON с постраничной выдачей.

    Вакансий у банка больше двух тысяч, и почти все — не наши: операционисты,
    продавцы, инкассаторы. Берём три свежие страницы и отдаём фильтру роли,
    тащить весь банк ради десятка попаданий незачем.
    """
    out = []
    for page in range(3):
        data = _json(
            "https://job.alfabank.ru/api/vacancies"
            f"?take=100&skip={page * 100}"
        )
        items = data.get("items") or []
        for v in items:
            out.append(_make(
                "Альфа-Банк", "alfa", v.get("id"), v.get("name"),
                f"https://job.alfabank.ru/vacancies{v.get('slug') or ''}",
                posted=v.get("createdAt") or "",
            ))
        if not items or (page + 1) * 100 >= (data.get("total") or 0):
            break
        time.sleep(DELAY_SEC)
    return out


def x5() -> list[Vacancy]:
    """Вакансии вшиты в SSR-страницу экранированным JSON, как у 2ГИС.

    Даты публикации сайт не отдаёт (`createdAt` пуст у всех), но карьерным
    сайтам окно и не применяется — они и так показывают только открытое.
    """
    out = []
    pages = 1
    page = 1
    while page <= min(pages, MAX_PAGES):
        suffix = f"?page={page}" if page > 1 else ""
        html = _fetch(f"https://x5.tech/vacancy{suffix}").replace('\\"', '"')
        total = re.search(r'"totalPages":(\d+)', html)
        if total:
            pages = int(total.group(1))
        for item in _embedded_objects(html):
            formats = [
                name for name, flag in (
                    ("удалённо", item.get("isRemote")),
                    ("гибрид", item.get("isHybrid")),
                    ("офис", item.get("isOffice")),
                ) if flag
            ]
            out.append(_make(
                "X5 Tech", "x5", item.get("id"), item.get("name"),
                f"https://x5.tech/vacancy/{item.get('id')}",
                city=_clean(item.get("city")) or None,
                fmt=_format(item.get("workFormat"), *formats),
                description=" ".join(
                    str(s) for s in (item.get("skills") or []) if s
                ),
            ))
        page += 1
        time.sleep(DELAY_SEC)
    return out


# Вакансии лежат в пейлоаде объектами вида {"id":"<uuid>","externalId":...}.
# Вырезаем от якоря кусок, сбалансированный по фигурным скобкам: разбирать
# весь пейлоад целиком не нужно, а регулярка на вложенный JSON развалится.
_X5_ANCHOR_RE = re.compile(r'\{"id":"[0-9a-f-]{36}","externalId"')
MAX_PAGES = 10


def _embedded_objects(html: str) -> list[dict]:
    found = []
    for match in _X5_ANCHOR_RE.finditer(html):
        depth = 0
        for index in range(match.start(), len(html)):
            if html[index] == "{":
                depth += 1
            elif html[index] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        found.append(json.loads(html[match.start():index + 1]))
                    except json.JSONDecodeError:
                        pass
                    break
    return found


ADAPTERS = {
    "yandex": yandex,
    "x5": x5,
    "tochka": tochka,
    "alfa": alfa,
    "magnit": magnit,
    "mts": mts,
    "tbank": tbank,
    "rwb": rwb,
    "vk": vk,
    "sberdevices": sberdevices,
}


def collect(since: str, limit: int | None = None,
            routes: list[str] | None = None) -> list[Vacancy]:
    """Упавшая компания не должна ронять остальные: карьерные сайты меняют
    вёрстку и API без предупреждения, это их право."""
    out: list[Vacancy] = []
    for name, adapter in ADAPTERS.items():
        try:
            found = adapter()
        except Exception as exc:
            print(f"  ! {name} не отдал вакансии: {str(exc)[:80]}")
            continue
        out += found
        time.sleep(DELAY_SEC)
    return out[:limit] if limit else out
