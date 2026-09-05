"""Профессии и маршруты к ним: кто что ищет и куда за этим идти.

Раньше ответ на оба вопроса был зашит в коде и означал «дизайн»: общий
фильтр роли в config.json и дизайнерские адреса внутри каждого источника
(sitemap-vacancies-design.xml у hirehi, /collection/ux_ui у careerspace,
специализация product_design у getmatch). Теперь и то, и другое — данные:
roles.json описывает профессии, sources.json — как их спросить у каждой
площадки.

Ключ роли всюду пишется как «направление.роль»: design.product, dev.frontend.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def roles() -> dict:
    return json.loads((ROOT / "roles.json").read_text("utf-8"))["directions"]


@lru_cache(maxsize=1)
def sources() -> dict:
    return json.loads((ROOT / "sources.json").read_text("utf-8"))["sources"]


def all_role_keys() -> list[str]:
    return [f"{d}.{r}" for d, body in roles().items() for r in body["roles"]]


def label(role_key: str) -> str:
    """«dev.frontend» -> «Фронтенд». Для направления целиком — его название."""
    direction, _, role = role_key.partition(".")
    body = roles().get(direction)
    if not body:
        return role_key
    if not role:
        return body["label"]
    return body["roles"].get(role, {}).get("label", role_key)


def rule(role_key: str) -> dict:
    direction, _, role = role_key.partition(".")
    return roles().get(direction, {}).get("roles", {}).get(role, {})


# --------------------------------------------------------------------------
# что кому подходит
# --------------------------------------------------------------------------


def match(title: str, role_keys: list[str]) -> str | None:
    """Первая роль из выбранных, под которую подходит заголовок вакансии.

    Сверяем именно заголовок: описание вакансии мимоходом упоминает и UI,
    и копирайтинг, и фронтенд, поэтому по всему тексту в подборку лезли
    graphic designer'ы и разработчики на Unreal Engine (замер 04.09.2026).

    Порядок ролей в профиле значим: вакансия достаётся первой подошедшей,
    и в дайджесте она окажется в её группе.
    """
    low = (title or "").lower()
    for key in role_keys:
        body = rule(key)
        if not body:
            continue
        if not any(word in low for word in body.get("include", [])):
            continue
        if any(word in low for word in body.get("exclude", [])):
            continue
        return key
    return None


# --------------------------------------------------------------------------
# куда идти
# --------------------------------------------------------------------------


def routes_for(source_key: str, role_keys: list[str]) -> list[str]:
    """Адреса, которые нужно обойти у одного источника ради этих ролей.

    Маршрут ищется сначала по точной роли (dev.frontend), потом по всему
    направлению (design): hh спрашивают запросом на каждую профессию,
    а hirehi отдаёт целую сферу одним sitemap.
    """
    table = sources().get(source_key, {}).get("routes", {})
    found: list[str] = []
    for key in role_keys:
        direction = key.partition(".")[0]
        for route in table.get(key, table.get(direction, [])):
            if route not in found:
                found.append(route)
    return found


def plan(role_keys: list[str], chosen_sources: list[str] | None = None) -> dict[str, list[str]]:
    """Полный маршрутный лист обхода: источник -> его адреса.

    Источники, у которых для этих ролей ничего нет, в план не попадают —
    ходить туда незачем. Пустой список chosen_sources значит «все».
    """
    keys = chosen_sources or list(sources())
    out = {}
    for key in keys:
        if sources().get(key, {}).get("status") != "connected":
            continue
        found = routes_for(key, role_keys)
        if found:
            out[key] = found
    return out


def coverage(role_keys: list[str]) -> dict[str, list[str]]:
    """Что каждый источник даст по выбранным ролям — для экрана «Источники».

    Нужно, чтобы человек не выбрал «фронтенд» вместе с designer.ru и не ждал
    вакансий, которых оттуда не будет никогда: молчащий бот неотличим
    от сломанного.
    """
    out = {}
    for key, body in sources().items():
        table = body.get("routes", {})
        fits = [
            label(role)
            for role in role_keys
            if table.get(role) or table.get(role.partition(".")[0])
        ]
        out[key] = fits
    return out
