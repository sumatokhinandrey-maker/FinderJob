#!/usr/bin/env python3
"""Проверка источников с того адреса, где живёт рассылка.

Нужна там, где источник смотрит не на наш код, а на наш адрес: LinkedIn
режет дата-центры, и с домашней машины ответ ничего не доказывает. Сетью
здесь всё и ограничивается — ни хранилища, ни отправки.

Запуск: python3 probe_sources.py (локально) либо кнопкой «probe» у workflow.
"""

from __future__ import annotations

import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")


def linkedin(attempt: int) -> str:
    params = urllib.parse.urlencode({
        "keywords": "product designer",
        "location": "Worldwide",
        "f_TPR": "r604800",
        "start": "0",
    })
    url = ("https://www.linkedin.com/jobs-guest/jobs/api/"
           "seeMoreJobPostings/search?" + params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as resp:
        html = resp.read().decode("utf-8", "replace")
    cards = len(re.findall(r"base-search-card__title", html))
    return f"HTTP {resp.status}, карточек {cards} (попытка {attempt})"


def main() -> int:
    print("=== откуда смотрим ===", flush=True)
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=15) as resp:
            print("адрес раннера:", resp.read().decode().strip(), flush=True)
    except Exception as exc:
        print("адрес узнать не вышло:", exc, flush=True)

    print("\n=== LinkedIn: настоящий сбор нашим источником ===", flush=True)
    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
        from datetime import date, timedelta

        from sources import linkedin as source

        started = time.time()
        found = source.collect(
            (date.today() - timedelta(days=7)).isoformat(),
            routes=["product designer", "web designer", "product analyst"],
        )
        print(f"   собрано {len(found)} за {time.time() - started:.0f} с", flush=True)
        for vacancy in found[:5]:
            print(f"     {vacancy.posted_at} | {vacancy.title[:38]} | {vacancy.company[:20]}",
                  flush=True)
    except Exception as exc:
        print(f"   источник упал: {type(exc).__name__}: {exc}", flush=True)

    print("\n=== LinkedIn: голый запрос ===", flush=True)
    # Три попытки с нарастающей паузой: 429 по частоте проходит сам,
    # а блок по адресу — нет, и это ровно то, что нужно различить.
    for attempt in range(1, 4):
        try:
            print("  ", linkedin(attempt), flush=True)
            return 0
        except urllib.error.HTTPError as exc:
            print(f"   HTTP {exc.code} — {exc.reason} (попытка {attempt})", flush=True)
            if exc.code not in (429, 999):
                return 1
        except Exception as exc:
            print(f"   {type(exc).__name__}: {exc} (попытка {attempt})", flush=True)
        if attempt < 3:
            pause = 20 * attempt
            print(f"   жду {pause} с", flush=True)
            time.sleep(pause)
    return 1


if __name__ == "__main__":
    sys.exit(main())
