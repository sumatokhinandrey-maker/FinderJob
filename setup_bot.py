#!/usr/bin/env python3
"""Разовая настройка витрины бота и меню для активных пользователей.

Описание — то, что человек читает в пустом чате до кнопки «Начать».
Клавиатура «Фильтры» прикрепляется к сообщению и остаётся внизу чата.

Запускать после правки текстов или клавиатуры в finderjob/wizard.py:
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
    tg.call("deleteMyCommands")

    users = store.all_users()
    cleared = installed = failed = 0
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

        if not user.get("menu_installed"):
            try:
                tg.send(chat_id, wizard.MENU_PROMPT, wizard.menu_keyboard())
                user["menu_installed"] = True
                store.save_user(user)
                installed += 1
            except RuntimeError:
                failed += 1
        time.sleep(0.04)

    print("описание:", tg.call("getMyDescription")["description"])
    print(f"Меню команд очищено в {cleared} чатах; клавиатура добавлена в {installed}.")
    if failed:
        print(f"Не удалось обновить: {failed} операций.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
