from __future__ import annotations

import logging

import config
from db import Database

logger = logging.getLogger(__name__)


class GameManager:
    def __init__(self) -> None:
        self.games: dict[int, object] = {}
        self.db = Database()
        self.nicknames: dict[int, str] = {}
        self.registered: set[int] = set()

    async def connect_db(self) -> None:
        await self.db.connect()
        if self.db.connected:
            nicknames, registered = await self.db.load_users()
            self.nicknames = nicknames
            self.registered = registered

    def game_for_chat(self, chat_id: int) -> object | None:
        return self.games.get(chat_id)

    def game_for_user(self, user_id: int) -> object | None:
        for g in self.games.values():
            if g.state != "ended" and user_id in g.players:
                return g
        return None

    async def register(self, user_id: int) -> bool:
        if user_id not in self.registered:
            self.registered.add(user_id)
            if self.db.connected:
                try:
                    await self.db.register(user_id)
                except Exception:  # noqa: BLE001
                    logger.exception("DB register failed")
            else:
                logger.warning("DB not connected: user %s registered in memory only", user_id)
            return True
        return False

    def is_registered(self, user_id: int) -> bool:
        return user_id in self.registered

    def nickname(self, user_id: int, fallback: str) -> str:
        return self.nicknames.get(user_id) or fallback

    async def set_nickname(self, user_id: int, nick: str) -> str:
        nick = nick.strip()
        self.nicknames[user_id] = nick
        self.registered.add(user_id)
        if self.db.connected:
            try:
                await self.db.set_nickname(user_id, nick)
            except Exception:  # noqa: BLE001
                logger.exception("DB set_nickname failed")
        else:
            logger.warning("DB not connected: nickname for user %s saved in memory only", user_id)
        return nick


manager = GameManager()
