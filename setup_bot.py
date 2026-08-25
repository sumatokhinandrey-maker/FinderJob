#!/usr/bin/env python3
"""Разовая настройка витрины бота: описание и меню команд.

Описание — то, что человек читает в пустом чате до кнопки «Начать».
Меню команд — список, который телеграм показывает по кнопке слева от поля ввода.

Запускать после правки текстов в finderjob/wizard.py:
    python3 setup_bot.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from finderjob import tg, wizard  # noqa: E402


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main() -> int:
    load_env()

    tg.call("setMyDescription", description=wizard.DESCRIPTION)
    tg.call("setMyShortDescription", short_description=wizard.DESCRIPTION)
    tg.call(
        "setMyCommands",
        commands=[
            {"command": name, "description": text} for name, text in wizard.COMMANDS
        ],
    )

    print("описание:", tg.call("getMyDescription")["description"])
    print("меню:")
    for item in tg.call("getMyCommands"):
        print(f"  /{item['command']} — {item['description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
