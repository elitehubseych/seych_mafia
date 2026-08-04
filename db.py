from __future__ import annotations

import logging

import config

logger = logging.getLogger(__name__)

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]


class Database:
    def __init__(self) -> None:
        self.pool: "asyncpg.Pool" | None = None
        self.connected = False

    async def connect(self) -> None:
        if not config.DATABASE_URL:
            logger.info("DATABASE_URL не задан; используется локальное хранилище")
            return
        if asyncpg is None:
            logger.warning("asyncpg не установлен; нельзя подключиться к базе данных")
            return

        try:
            self.pool = await asyncpg.create_pool(
                config.DATABASE_URL,
                min_size=1,
                max_size=3,
                command_timeout=10,
            )
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    create table if not exists users (
                        user_id bigint primary key,
                        nickname text,
                        registered boolean not null default false
                    )
                    """
                )
            self.connected = True
            logger.info("Подключение к базе данных установлено")
        except Exception as exc:  # noqa: BLE001
            self.connected = False
            logger.warning("Не удалось подключиться к базе данных: %s", exc)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
        self.connected = False

    async def load_users(self) -> tuple[dict[int, str], set[int]]:
        if not self.connected or self.pool is None:
            return {}, set()

        nicknames: dict[int, str] = {}
        registered: set[int] = set()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("select user_id, nickname, registered from users")
            for row in rows:
                uid = row["user_id"]
                if row["nickname"]:
                    nicknames[uid] = row["nickname"]
                if row["registered"]:
                    registered.add(uid)
        return nicknames, registered

    async def set_nickname(self, user_id: int, nickname: str) -> None:
        if not self.connected or self.pool is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                insert into users (user_id, nickname, registered)
                values ($1, $2, true)
                on conflict (user_id) do update set
                    nickname = excluded.nickname,
                    registered = true
                """,
                user_id,
                nickname,
            )

    async def register(self, user_id: int) -> None:
        if not self.connected or self.pool is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                insert into users (user_id, registered)
                values ($1, true)
                on conflict (user_id) do update set
                    registered = true
                """,
                user_id,
            )
