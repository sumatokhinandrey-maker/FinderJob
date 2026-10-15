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
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
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


def inspect(platform: str, data) -> tuple[int, str | None, str]:
    """(сколько вакансий, имя организации, тексты для сверки)"""
    if platform == "greenhouse":
        jobs = (_get(BOARDS["greenhouse"].format(s=data["_slug"]) + "/jobs") or {}).get("jobs") or []
        return len(jobs), data.get("name"), ""
    if platform == "workable":
        return len(data.get("jobs") or []), data.get("name"), ""
    if platform == "smartrecruiters":
        items = data.get("content") or []
        org = (items[0].get("company") or {}).get("name") if items else None
        return int(data.get("totalFound") or 0), org, ""
    if platform == "recruitee":
        offers = data.get("offers") or []
        return len(offers), (offers[0].get("company_name") if offers else None), ""
    if platform == "lever":
        posts = data if isinstance(data, list) else []
        text = " ".join(f"{p.get('descriptionPlain') or ''} {p.get('additionalPlain') or ''}"
                        for p in posts[:10])
        return len(posts), None, text
    if platform == "ashby":
        jobs = data.get("jobs") or []
        return len(jobs), None, " ".join(j.get("descriptionPlain") or "" for j in jobs[:10])
    return 0, None, ""


def verdict(name: str, org: str | None, text: str) -> tuple[str, str]:
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
    args = parser.parse_args()

    from main import load_env
    load_env()

    registry = json.loads(REGISTRY.read_text("utf-8"))
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
                total, org, text = inspect(platform, data)
                if total <= 0:
                    continue
                status, why = verdict(name, org, text)
                entry = {"name": name, "platform": platform, "slug": slug,
                         "status": status, "confirmed_by": why}
                added.append(entry)
                known.add((platform, slug))
                print(f"  {status:8} {name[:28]:28} → {platform}:{slug} — {total} | {why}")

    print(f"\nНовых записей: {len(added)}, из них подтверждено: "
          f"{sum(1 for e in added if e['status'] == 'verified')}")
    if args.dry_run or not added:
        return 0
    registry["companies"].extend(added)
    registry["checked"] = date.today().isoformat()
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"Реестр обновлён: {REGISTRY.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
