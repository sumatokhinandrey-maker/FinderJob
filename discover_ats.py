#!/usr/bin/env python3
"""Пополнение ats_registry.json: у каких компаний из запаса есть своя доска.

Берём названия компаний, которые уже встречались в наших источниках, и
спрашиваем публичные ATS-платформы, есть ли у них доска с таким адресом.
Найденное сверяем: адрес доски, совпавший с названием, — ещё не работодатель.

- Greenhouse, Workable, SmartRecruiters, Recruitee отдают имя организации —
  сверяем его с названием.
- Lever и Ashby имени не отдают — требуем, чтобы полное название компании
  встретилось в текстах её вакансий хотя бы MIN_MENTIONS раз.

Подтверждённое ложится со status=verified и сразу идёт в обход, остальное —
status=review, руками. Запуск: python3 discover_ats.py [--limit N] [--dry-run].
Российские названия не проверяем: на этих платформах их почти нет (из 60
проверенных на FriendWork нашлась одна), они живут на hh и своих сайтах.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from finderjob import store  # noqa: E402
from sources.ats import REGISTRY, USER_AGENT  # noqa: E402

PAUSE_SEC = 0.25
MIN_MENTIONS = 3
# Имя организации пишет сам владелец аккаунта, поэтому оно подделывается:
# 15.10.2026 «google» и «accenture» на Recruitee оказались пробными
# аккаунтами с шаблонной вакансией «Senior Marketer (Sample)». Такие доски
# маленькие и полны образцов — их на ручную проверку или сразу мимо.
MIN_JOBS = 3
# Только явные образцы: «(Sample)» в скобках или заголовок целиком из слова
# «test»/«demo». Слово test внутри заголовка — это тестировщики («Software
# Development Engineer In Test»), и первая версия правила ошибочно отбросила
# из-за него Bybit со 159 настоящими вакансиями.
_SAMPLE = re.compile(
    r"\((sample|demo|example|template)\)|^\s*(test|demo|sample)(\s+(job|vacancy|position))?\s*$",
    re.I,
)

BOARDS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=20",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{s}",
    "recruitee": "https://{s}.recruitee.com/api/offers/",
}
_JUNK = re.compile(r"\b(llc|ltd|inc|gmbh|ojsc|group|company|corp|co|plc|ag|bv|s\.a\.)\b", re.I)


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    # OSError, а не TimeoutError: на Python 3.9 таймаут сокета ещё не входит
    # в TimeoutError, и поиск падал на первом же подвисшем ответе.
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def slugs(name: str) -> list[str]:
    words = _words(_JUNK.sub(" ", re.sub(r"\(.*?\)", " ", name)))
    if not words:
        return []
    found = ["".join(words)]
    if len(words) > 1:
        found.append("-".join(words))
    return [s for s in dict.fromkeys(found) if len(s) >= 3]


def inspect(platform: str, data) -> tuple[int, str | None, str, list[str]]:
    """(сколько вакансий, имя организации, тексты для сверки, заголовки)"""
    if platform == "greenhouse":
        jobs = (_get(BOARDS["greenhouse"].format(s=data["_slug"]) + "/jobs") or {}).get("jobs") or []
        return len(jobs), data.get("name"), "", [j.get("title") or "" for j in jobs]
    if platform == "workable":
        jobs = data.get("jobs") or []
        return len(jobs), data.get("name"), "", [j.get("title") or "" for j in jobs]
    if platform == "smartrecruiters":
        items = data.get("content") or []
        org = (items[0].get("company") or {}).get("name") if items else None
        return int(data.get("totalFound") or 0), org, "", [j.get("name") or "" for j in items]
    if platform == "recruitee":
        offers = data.get("offers") or []
        return (len(offers), (offers[0].get("company_name") if offers else None), "",
                [o.get("title") or "" for o in offers])
    if platform == "lever":
        posts = data if isinstance(data, list) else []
        text = " ".join(f"{p.get('descriptionPlain') or ''} {p.get('additionalPlain') or ''}"
                        for p in posts[:10])
        return len(posts), None, text, [p.get("text") or "" for p in posts]
    if platform == "ashby":
        jobs = data.get("jobs") or []
        return (len(jobs), None, " ".join(j.get("descriptionPlain") or "" for j in jobs[:10]),
                [j.get("title") or "" for j in jobs])
    return 0, None, "", []


def verdict(name: str, org: str | None, text: str, total: int = MIN_JOBS,
            titles: list[str] | None = None) -> tuple[str, str]:
    samples = [t for t in titles or [] if _SAMPLE.search(t)]
    if samples:
        return "rejected", f"шаблонная доска: «{samples[0][:40]}»"
    if total < MIN_JOBS:
        return "review", f"всего {total} вакансий — похоже на пробный аккаунт, проверить руками"
    full = " ".join(_words(name))
    if org:
        same = " ".join(_words(org))
        ok = bool(full) and (full in same or same in full)
        return ("verified" if ok else "review",
                f"имя организации на платформе: {org}" + ("" if ok else " — НЕ совпадает"))
    low = " ".join(_words(text))
    hits = low.count(full) + (low.count(full.replace(" ", "")) if " " in full else 0)
    status = "verified" if hits >= MIN_MENTIONS else "review"
    return status, f"полное название в описаниях: {hits} раз"


def main() -> int:
    parser = argparse.ArgumentParser(description="Поиск досок компаний на публичных ATS")
    parser.add_argument("--limit", type=int, default=0, help="сколько компаний проверить")
    parser.add_argument("--dry-run", action="store_true", help="не записывать реестр")
    parser.add_argument("--recheck", metavar="STATUS",
                        help="перепроверить записи с этим статусом по текущим правилам")
    args = parser.parse_args()

    from main import load_env
    load_env()

    registry = json.loads(REGISTRY.read_text("utf-8"))

    if args.recheck:
        # Правила сверки меняются — уже записанное надо уметь перепроверить,
        # а не только искать новое.
        changed = 0
        for entry in registry["companies"]:
            if entry.get("status") != args.recheck:
                continue
            data = _get(BOARDS[entry["platform"]].format(s=entry["slug"]))
            time.sleep(PAUSE_SEC)
            if not data:
                print(f"  ? {entry['name']}: доска не ответила, оставляю как есть")
                continue
            if isinstance(data, dict):
                data["_slug"] = entry["slug"]
            total, org, text, titles = inspect(entry["platform"], data)
            status, why = verdict(entry["name"], org, text, total, titles)
            mark = "→" if status != entry["status"] else "="
            print(f"  {entry['status']:8} {mark} {status:8} {entry['name'][:26]:26} "
                  f"{entry['platform']}:{entry['slug']} — {total} | {why}")
            if status != entry["status"]:
                entry["status"], entry["confirmed_by"] = status, why
                changed += 1
        if changed and not args.dry_run:
            REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"Изменено записей: {changed}")
        return 0
    known = {(c["platform"], c["slug"]) for c in registry["companies"]}
    known_names = {c["name"].lower() for c in registry["companies"]}

    rows, _ = store.load_pool()
    names = sorted({
        v.company.strip() for v in core.from_rows(rows)
        if v.company.strip() and v.company.strip().upper() != "NDA"
        and not re.search(r"[А-Яа-яЁё]", v.company)
        and v.company.strip().lower() not in known_names
    })
    if args.limit:
        names = names[:args.limit]
    print(f"Проверяю {len(names)} компаний")

    added = []
    for name in names:
        for slug in slugs(name):
            for platform, url in BOARDS.items():
                if (platform, slug) in known:
                    continue
                data = _get(url.format(s=slug))
                time.sleep(PAUSE_SEC)
                if not data:
                    continue
                if isinstance(data, dict):
                    data["_slug"] = slug
                total, org, text, titles = inspect(platform, data)
                if total <= 0:
                    continue
                status, why = verdict(name, org, text, total, titles)
                entry = {"name": name, "platform": platform, "slug": slug,
                         "status": status, "confirmed_by": why}
                added.append(entry)
                known.add((platform, slug))
                print(f"  {status:8} {name[:28]:28} → {platform}:{slug} — {total} | {why}",
                      flush=True)
                if not args.dry_run:
                    # Пишем сразу: прогон долгий, и сбой на середине не должен
                    # стирать уже найденное.
                    registry["companies"].append(entry)
                    registry["checked"] = date.today().isoformat()
                    REGISTRY.write_text(
                        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", "utf-8")

    print(f"\nНовых записей: {len(added)}, из них подтверждено: "
          f"{sum(1 for e in added if e['status'] == 'verified')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
