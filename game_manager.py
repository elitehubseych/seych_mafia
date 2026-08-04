from __future__ import annotations

import asyncio
import json
import logging
import os

import config
from db import Database

logger = logging.getLogger(__name__)


class GameManager:
    def __init__(self) -> None:
        self.games: dict[int, object] = {}
        self.db = Database()
        self.nicknames: dict[int, str] = {}
        self.registered: set[int] = set()
        self._load()

    async def connect_db(self) -> None:
        await self.db.connect()
        if self.db.connected:
            nicknames, registered = await self.db.load_users()
            self.nicknames = nicknames
            self.registered = registered

    def _load(self) -> None:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        try:
            with open(config.NICKNAMES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            self.nicknames = {int(k): v for k, v in data.get("nicknames", {}).items()}
            self.registered = {int(k) for k in data.get("registered", [])}
        except (FileNotFoundError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(config.NICKNAMES_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "nicknames": self.nicknames,
                        "registered": list(self.registered),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError as e:
            logger.warning("nickname save failed: %s", e)

    def game_for_chat(self, chat_id: int) -> object | None:
        return self.games.get(chat_id)

    def game_for_user(self, user_id: int) -> object | None:
        for g in self.games.values():
            if g.state != "ended" and user_id in g.players:
                return g
        return None

    def register(self, user_id: int) -> bool:
        if user_id not in self.registered:
            self.registered.add(user_id)
            self._save()
            if self.db.connected:
                try:
                    asyncio.create_task(self.db.register(user_id))
                except Exception:  # noqa: BLE001
                    logger.exception("DB register task failed")
            return True
        return False

    def is_registered(self, user_id: int) -> bool:
        return user_id in self.registered

    def nickname(self, user_id: int, fallback: str) -> str:
        return self.nicknames.get(user_id) or fallback

    def set_nickname(self, user_id: int, nick: str) -> str:
        nick = nick.strip()
        self.nicknames[user_id] = nick
        self.registered.add(user_id)
        self._save()
        if self.db.connected:
            try:
                asyncio.create_task(self.db.set_nickname(user_id, nick))
            except Exception:  # noqa: BLE001
                logger.exception("DB set_nickname task failed")
        return nick


manager = GameManager()
