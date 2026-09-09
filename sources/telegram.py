"""Вакансии из публичных телеграм-каналов.

Читаем веб-ленту `t.me/s/<канал>` — она отдаётся обычным HTML без аккаунта,
ключей и Telegram API. Это тот же публичный предпросмотр, что открывается
у любого в браузере.

**Не всякий канал так читается.** Если владелец выключил предпросмотр,
страница отдаёт заглушку с нулём постов — такой канал доступен только
через авторизованный клиент, и мы его не берём (см. `_ALLOWED` ниже).

Пост — не структурированная вакансия, а свободный текст. Разбираем по
устоявшемуся в этих каналах порядку строк:

    #удаленно                          <- хештег с форматом или городом
    Principal Product Designer в 2ГИС  <- заголовок: роль и компания
    Ребята, привет! Есть интересная…    <- описание

Заголовком берём первую содержательную строку: по ней работает фильтр роли.
"""

from __future__ import annotations

import html as _html
import re
import time
import urllib.error
import urllib.request

from core import Vacancy

BASE = "https://t.me/s/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DELAY_SEC = 0.5

# Каналы с открытым веб-предпросмотром. Проверено 09.09.2026:
# @designoffers даёт 20 постов, @designhunters — 17.
DEFAULT_ROUTES = ["designoffers", "designhunters"]

_POST_RE = re.compile(
    r'data-post="([^"]+)".*?'
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>.*?'
    r'<time datetime="([^"]+)"',
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)

# Пост может быть анонсом эфира, рекламой курса или статьёй. Вакансию узнаём
# по словам, которыми её объявляют: без этого в дайджест поедет всё подряд.
_JOB_RE = re.compile(
    r"\b(ищем|ищу|вакансия|вакансии|требуется|в команду|ищется|"
    r"hiring|we are looking|открыта позиция)\b",
    re.I,
)

_REMOTE_RE = re.compile(r"#?удал[её]нк?\w*|#?remote|удал[её]нно", re.I)
_HYBRID_RE = re.compile(r"#?гибрид", re.I)
_OFFICE_RE = re.compile(r"#?офис", re.I)

# Первая строка часто состоит из одних хештегов и эмодзи — заголовком
# она не годится.
_ONLY_TAGS_RE = re.compile(r"^[\s#\w]*$")


def _fetch(url: str, timeout: int = 25) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "ignore")


def _lines(fragment: str) -> list[str]:
    text = _html.unescape(_TAG_RE.sub("", _BR_RE.sub("\n", fragment)))
    return [line.strip() for line in text.split("\n") if line.strip()]


# Слова, по которым узнаём строку с названием роли. Первая строка поста
# бывает болтовнёй («опять вакансия... не устали?»), поэтому берём не её,
# а первую строку, где роль действительно названа.
_ROLE_RE = re.compile(
    r"(дизайн\w*|designer|разработчик|developer|аналитик|analyst|"
    r"копирайт\w*|редактор|маркетолог|менеджер|manager|тестировщик|qa)",
    re.I,
)

def _title_of(lines: list[str]) -> str | None:
    """Название вакансии из поста.

    Берём первую строку, где названа роль, а не просто первую непустую:
    посты часто начинаются с приветствия, хештегов или болтовни
    («опять вакансия... не устали?»).

    Длинную строку не режем с начала, а берём окно вокруг слова роли —
    иначе у «Дизайн-система Ozon Банка растёт и я ищу…» отрывается голова
    и остаётся бессмыслица. Идеального заголовка тут не выйдет: посты
    пишут люди свободным текстом, и это цена источника.
    """
    candidates = [l.lstrip("#•—–-✨🚀🔥💚🎮 ").strip() for l in lines[:6]]
    candidates = [c for c in candidates if len(c) >= 6 and not c.startswith("#")]
    if not candidates:
        return None

    line = next((c for c in candidates if _ROLE_RE.search(c)), candidates[0])
    line = re.sub(r"^(ищем|ищу|ищется|требуется|нужен|нужна|открыта вакансия|вакансия)\s+",
                  "", line, flags=re.I)

    if len(line) > 70:
        words = line.split()
        at = next((i for i, w in enumerate(words) if _ROLE_RE.search(w)), 0)
        window = words[max(0, at - 4):at + 5]
        line = " ".join(window)
        if max(0, at - 4) > 0:
            line = "…" + line
    return line.strip(" .,;—–-") or None


# «Yope ищет Product Designer», «Principal Designer в 2ГИС» — имя компании
# в постах стоит либо перед глаголом, либо после предлога «в».
_CO_BEFORE_RE = re.compile(r"^([A-ZА-ЯЁ][\w&.\- ]{1,24}?)\s+(?:ищет|ищут|в поиске)\b")
_CO_AFTER_RE = re.compile(r"\bв\s+([A-ZА-ЯЁ][\w&.\-]{2,24}(?:\s[A-ZА-ЯЁ][\w&.\-]{2,24})?)\s*$")


# Оборот «Сейчас мы в поиске» ловится тем же правилом, что и «Yope ищет»,
# поэтому служебные начала отсеиваем отдельно.
_NOT_A_COMPANY = {
    "сейчас мы", "мы", "сегодня мы", "команда", "привет", "друзья",
    "коллеги", "срочно", "новая", "горячая", "наша команда",
}


def _company_of(title: str, channel: str) -> str:
    """Компания из заголовка, если её видно. Иначе — имя канала: честнее,
    чем выдумывать, и в дайджесте сразу понятно, откуда вакансия."""
    for pattern in (_CO_BEFORE_RE, _CO_AFTER_RE):
        found = pattern.search(title)
        if found:
            name = found.group(1).strip()
            if name.lower() not in _NOT_A_COMPANY and len(name) > 1:
                return name
    return f"@{channel}"


def _format_of(text: str) -> str:
    if _REMOTE_RE.search(text):
        return "remote"
    if _HYBRID_RE.search(text):
        return "hybrid"
    if _OFFICE_RE.search(text):
        return "office"
    return "unknown"


def collect(since: str, limit: int | None = None,
            routes: list[str] | None = None) -> list[Vacancy]:
    """Посты-вакансии из каналов, опубликованные начиная с даты since."""
    out: list[Vacancy] = []
    for channel in routes or DEFAULT_ROUTES:
        try:
            page = _fetch(BASE + channel)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  ! канал @{channel} не открылся: {exc}")
            continue

        found = _POST_RE.findall(page)
        if not found:
            # Владелец выключил веб-предпросмотр — без авторизации не прочитать.
            print(f"  ! у @{channel} нет публичного предпросмотра")
            continue

        for post_id, fragment, posted in found:
            if posted[:10] < since:
                continue
            lines = _lines(fragment)
            if not lines:
                continue
            full = " ".join(lines)
            if not _JOB_RE.search(full):
                continue          # анонс, реклама курса или статья
            title = _title_of(lines)
            if not title:
                continue
            out.append(Vacancy(
                id=f"telegram:{post_id}",
                source="telegram",
                title=title,
                company=_company_of(title, channel),
                grade=None,
                salary=None,
                work_format=_format_of(full),
                city=None,
                country=None,
                url=f"https://t.me/{post_id}",
                posted_at=posted[:10],
                experience_months=None,
                description=full[:2000],
                skills="",
            ))
        time.sleep(DELAY_SEC)
    return out[:limit] if limit else out
