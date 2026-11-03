"""Отправка заранее собранного дайджеста из Redis."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import core
from finderjob import profile as prof
from finderjob import stats, store, tg

MOSCOW = ZoneInfo("Europe/Moscow")
NEWS_DAY = "2026-10-18"
NEWS_URL = "https://t.me/finderjobupdate"


def send_news_invitation(users: list[dict], day: str) -> None:
    """Разовое приглашение после дайджеста; повторный запуск его не дублирует."""
    if day != NEWS_DAY or not NEWS_URL:
        return
    text = (
        '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> '
        'Подпишись на новостной канал Finder Job, чтобы быть в курсе '
        'обновлений и новых функций.'
    )
    keyboard = [[{
        "text": "FinderJob",
        "url": NEWS_URL,
        "icon_custom_emoji_id": "5875465628285931233",
    }]]
    for user in users:
        chat_id = user["chat_id"]
        if not store.delivery_sent(chat_id, day):
            continue
        claim = store.claim_delivery(chat_id, f"{day}:news")
        if not claim:
            continue
        try:
            tg.send(chat_id, text, keyboard)
            store.finish_delivery(chat_id, f"{day}:news", claim, True)
        except Exception as exc:
            print(f"  ! приглашение не доставлено {chat_id}: {exc}")
            store.finish_delivery(chat_id, f"{day}:news", claim, False)


def moscow_day() -> str:
    return datetime.now(MOSCOW).date().isoformat()


# Рассылать можно только в дневном окне по Москве. Замок «раз в день» держится
# на дате по Москве, а она переворачивается в полночь — в 21:00 UTC. Поэтому
# запуск, опоздавший до ночи (у GitHub расписание плавает на часы), попадал
# уже в следующие московские сутки: 23:06 UTC 02.11 — это 02:06 МСК 03.11,
# для замка новый день, и бот слал завтрашний дайджест за семь часов до срока
# (02.11.2026 так и вышло — пришёл в 06:06 по Бангкоку). Окно это закрывает:
# штатная отправка в 09:00, догоняющие до 16:37, всё остальное — не время.
SEND_FROM_HOUR = 9
SEND_TO_HOUR = 20


def send_window_open() -> bool:
    return SEND_FROM_HOUR <= datetime.now(MOSCOW).hour < SEND_TO_HOUR


def send_window_note() -> str:
    now = datetime.now(MOSCOW)
    return (f"Сейчас {now:%H:%M} МСК — вне окна рассылки "
            f"({SEND_FROM_HOUR:02d}:00–{SEND_TO_HOUR:02d}:00), не отправляю.")


def deliver(user: dict, vacancies: list, cap: int = 0,
            dry_run: bool = False, claim: str | None = None,
            track: bool = True) -> int:
    """Применить текущий профиль и отправить одному подписчику."""
    prof.migrate(user["profile"])
    passed, rejected = core.apply_filters(vacancies, prof.to_filters(user["profile"]))
    seen = user.get("seen") or {}
    fresh = core.only_new(passed, seen)
    tail = 0
    if cap and len(fresh) > cap:
        fresh.sort(key=lambda v: v.posted_at, reverse=True)
        fresh, tail = fresh[:cap], len(fresh) - cap

    pages = core.build_pages(fresh, prof.roles_of(user["profile"]))
    if tail:
        pages[-1] += (
            f"\n\nЕщё {tail} " + core._plural(tail)
            + " — пришлю в следующие дни, чтобы не заваливать одним сообщением."
        )
    digest = pages[0]
    label = user.get("username") or user["chat_id"]
    breakdown = ", ".join(
        f"{core.REASONS.get(k, k)} {v}" for k, v in sorted(rejected.items())
    )
    print(f"\n--- {label}: подошло {len(passed)}, из них новых {len(fresh)}")
    if breakdown:
        print(f"    отсеяно: {breakdown}")
    print(core.to_plain(digest))
    if dry_run:
        return len(fresh)

    store.save_digest(user["chat_id"], pages)
    tg.send(user["chat_id"], digest, core.digest_keyboard(0, len(pages)))
    if claim:
        # Ставим отметку сразу после ответа Telegram: ошибки статистики или
        # сохранения seen не должны превращаться в повторное сообщение.
        store.finish_delivery(user["chat_id"], moscow_day(), claim, True)
    if track:
        stats.track("digest_sent")
        stats.track("vacancies_sent", len(fresh))

    for vacancy in fresh:
        seen[vacancy.id] = moscow_day()
    user["seen"] = core.trim_seen(seen)
    store.save_user(user)
    return len(fresh)


def send_prepared() -> tuple[int, int]:
    """Отправить только сегодняшний запас; старый запас не рассылать."""
    if not send_window_open():
        print(send_window_note())
        return 0, 0
    today = moscow_day()
    rows, collected_at = store.load_pool()
    if collected_at != today or not rows:
        raise RuntimeError(f"Нет подготовленных вакансий за {today}")
    vacancies = core.fill_grades(core.from_rows(rows))
    users = store.all_users()
    import json
    from pathlib import Path
    config = json.loads((Path(__file__).parent.parent / "config.json").read_text("utf-8"))
    sent_users = sent_vacancies = 0
    for user in users:
        chat_id = user["chat_id"]
        claim = store.claim_delivery(chat_id, today)
        if not claim:
            continue
        try:
            count = deliver(user, vacancies, config.get("max_per_digest", 0),
                            claim=claim, track=False)
        except Exception as exc:
            print(f"  ! не доставлено {chat_id}: {exc}")
            store.finish_delivery(chat_id, today, claim, False)
            continue
        sent_users += 1
        sent_vacancies += count
    if sent_users:
        stats.track("digest_sent", sent_users, today)
        stats.track("vacancies_sent", sent_vacancies, today)
    store.mark_sent(today)
    send_news_invitation(users, today)
    print(f"Отправлено подписчикам: {sent_users}; вакансий: {sent_vacancies}")
    return sent_users, sent_vacancies
