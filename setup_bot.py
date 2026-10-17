#!/usr/bin/env python3
"""Разовая настройка описания бота и меню без сообщений подписчикам.

Описание — то, что человек читает в пустом чате до кнопки «Начать».

Запускать после правки описания или списка команд:
    python3 setup_bot.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from finderjob import store, tg, wizard  # noqa: E402


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
    # ReplyKeyboardMarkup Telegram может скрыть на стороне клиента. Команда
    # в системном меню остаётся доступной без отправки служебных сообщений.
    tg.call("setMyCommands", commands=[
        {"command": "filters", "description": "Открыть фильтры вакансий"},
    ])
    tg.call("setChatMenuButton", menu_button={"type": "commands"})

    users = store.all_users()
    cleared = failed = 0
    for user in users:
        chat_id = user["chat_id"]
        try:
            tg.call(
                "deleteMyCommands",
                scope={"type": "chat", "chat_id": chat_id},
            )
            cleared += 1
        except RuntimeError:
            failed += 1

        time.sleep(0.04)

    print("описание:", tg.call("getMyDescription")["description"])
    print(f"Фильтры добавлены в меню; старые команды очищены в {cleared} чатах; сообщений нет.")
    if failed:
        print(f"Не удалось обновить: {failed} операций.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
