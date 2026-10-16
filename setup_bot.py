#!/usr/bin/env python3
"""Разовая настройка витрины бота и меню для активных пользователей.

Описание — то, что человек читает в пустом чате до кнопки «Начать».
Клавиатура «Фильтры» прикрепляется к сообщению и остаётся внизу чата.

Запускать после правки текстов или клавиатуры в finderjob/wizard.py:
    python3 setup_bot.py
"""

from __future__ import annotations

import argparse
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


def restore_menu() -> int:
    """Один раз возвращаем клавиатуру всем подписчикам, включая старые чаты.

    Успешные отправки помечаем: повторный запуск не засорит их переписку.
    Заблокировавших бота повторно попробует следующий ежедневный дайджест.
    """
    load_env()
    try:
        users = store.all_users()
    except store.StoreError:
        print("Хранилище недоступно; сообщения не отправлены.")
        return 1
    restored = skipped = deferred = failed = 0
    for user in users:
        if user.get("menu_version") == wizard.MENU_VERSION:
            skipped += 1
            continue
        try:
            tg.send(user["chat_id"], wizard.MENU_PROMPT, wizard.menu_keyboard())
        except RuntimeError as exc:
            if "403" in str(exc):
                deferred += 1
            else:
                failed += 1
                print(f"Не удалось отправить клавиатуру: {type(exc).__name__}")
            continue
        except OSError:
            print("Telegram недоступен; повтори запуск позже.")
            return 1
        user["menu_installed"] = True
        user["menu_version"] = wizard.MENU_VERSION
        try:
            store.save_user(user)
        except store.StoreError:
            print("Кнопка отправлена, но хранилище не сохранило отметку; повтори запуск позже.")
            return 1
        restored += 1
        time.sleep(0.04)

    print(
        f"Подписчиков: {len(users)}; кнопка отправлена: {restored}; "
        f"уже обновлены: {skipped}; заблокировали бота: {deferred}; "
        f"другие ошибки: {failed}."
    )
    return 1 if failed else 0


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
                user["menu_version"] = wizard.MENU_VERSION
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore-menu", action="store_true",
                        help="повторно показать кнопку «Фильтры» всем подписчикам")
    args = parser.parse_args()
    raise SystemExit(restore_menu() if args.restore_menu else main())
