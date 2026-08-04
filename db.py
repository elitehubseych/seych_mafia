from __future__ import annotations

import logging
import secrets
import socket
import ssl
import urllib.parse

import config

logger = logging.getLogger(__name__)

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]


def _build_supabase_postgres_url() -> str | None:
    if not config.SUPABASE_URL or not config.SUPABASE_PASSWORD:
        return None

    url = config.SUPABASE_URL.strip()
    if url.startswith("postgres://") or url.startswith("postgresql://"):
        return url

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or url
    if not host:
        return None

    if host.endswith(".supabase.co") and not host.startswith("db."):
        host = f"db.{host}"

    password = urllib.parse.quote_plus(config.SUPABASE_PASSWORD)
    user = "postgres"
    dbname = "postgres"
    return f"postgres://{user}:{password}@{host}:5432/{dbname}"

def _resolve_ipv4_host(host: str, port: int) -> str | None:
    try:
        infos = socket.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
        for family, socktype, proto, canonname, sockaddr in infos:
            if family == socket.AF_INET:
                return sockaddr[0]
    except OSError:
        return None
    return None


class Database:
    def __init__(self) -> None:
        self.pool: "asyncpg.Pool" | None = None
        self.connected = False

    async def connect(self) -> None:
        database_url = config.DATABASE_URL
        if not database_url:
            database_url = _build_supabase_postgres_url()
            if database_url:
                logger.info("DATABASE_URL не задан; использую Supabase SUPABASE_URL/SUPABASE_PASSWORD")

        if not database_url:
            logger.info("DATABASE_URL не задан; используется локальное хранилище")
            return
        if asyncpg is None:
            logger.warning("asyncpg не установлен; нельзя подключиться к базе данных")
            return

        safe_url = database_url
        try:
            parsed = urllib.parse.urlparse(database_url)
            if parsed.password:
                safe_url = database_url.replace(parsed.password, "*****")
        except Exception:
            safe_url = "[masked]"

        logger.info("Подключаюсь к базе данных: %s", safe_url)
        parsed = urllib.parse.urlparse(database_url)
        use_ssl = parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        create_pool_kwargs = {
            "min_size": 1,
            "max_size": 3,
            "command_timeout": 10,
        }
        if use_ssl:
            create_pool_kwargs["ssl"] = "require"

        try:
            self.pool = await asyncpg.create_pool(database_url, **create_pool_kwargs)
        except (OSError, asyncpg.PostgresError) as exc:
            if isinstance(exc, OSError):
                parsed = urllib.parse.urlparse(database_url)
                if parsed.hostname and exc.errno in {101, 110, 113}:
                    ipv4 = _resolve_ipv4_host(parsed.hostname, parsed.port or 5432)
                    if ipv4:
                        logger.info(
                            "IPv6 не работает; пробую подключение к IPv4 %s",
                            ipv4,
                        )
                        self.pool = await asyncpg.create_pool(
                            user=parsed.username,
                            password=parsed.password,
                            database=(parsed.path or "/").lstrip("/") or "postgres",
                            host=ipv4,
                            port=parsed.port or 5432,
                            ssl="require" if use_ssl else None,
                            min_size=1,
                            max_size=3,
                            command_timeout=10,
                        )
                    else:
                        raise
                else:
                    raise
            else:
                raise

        try:
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
