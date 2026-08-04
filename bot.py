from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys

import aiohttp
from aiohttp import web

import config
from game_manager import manager
from handlers import handle_message_event, handle_message_new
from vk_api import vk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
_fh = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.getLogger().addHandler(_fh)
logger = logging.getLogger(__name__)


async def _validate(event: dict) -> bool:
    if not config.VK_SECRET:
        return True
    return hmac.compare_digest(event.get("secret") or "", config.VK_SECRET)


async def callback_handler(request: web.Request) -> web.Response:
    try:
        event = await request.json()
    except json.JSONDecodeError:
        return web.Response(status=400)
    if not await _validate(event):
        logger.warning("Callback with invalid secret ignored")
        return web.Response(status=401)

    event_type = event.get("type")
    if event_type == "confirmation":
        return web.Response(text=config.VK_CONFIRMATION)

    loop = asyncio.get_running_loop()
    if event_type == "message_new":
        loop.create_task(_safe(handle_message_new(vk, event.get("object") or {})))
    elif event_type == "message_event":
        loop.create_task(_safe(handle_message_event(vk, event.get("object") or {})))

    return web.Response(text="ok")


async def _safe(coro) -> None:
    try:
        await coro
    except Exception:  # noqa: BLE001
        logger.exception("Error while processing VK event")


async def _keepalive_loop(url: str) -> None:
    interval = config.KEEPALIVE_INTERVAL
    ping_url = url.rstrip("/") + "/health"
    session: aiohttp.ClientSession | None = None
    while True:
        try:
            await asyncio.sleep(interval)
            if session is None or session.closed:
                session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
            async with session.get(ping_url) as resp:
                await resp.read()
            logger.info("keepalive ping %s -> %s", ping_url, resp.status)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("keepalive ping failed: %s", e)


async def _app_main() -> None:
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_loop_exception_handler)
    app = web.Application()

    async def _close_resources(_app: web.Application) -> None:
        await vk.close()
        await manager.db.close()

    app.on_cleanup.append(_close_resources)
    app.router.add_post(config.CALLBACK_PATH, callback_handler)
    app.router.add_post("/{tail:.*}", callback_handler)
    app.router.add_get("/health", lambda r: web.Response(text="ok"))
    keepalive_url = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("KEEPALIVE_URL") or "").strip()
    if keepalive_url:
        loop.create_task(_keepalive_loop(keepalive_url))
        logger.info(
            "keepalive enabled: %s/health every %s s",
            keepalive_url.rstrip("/"),
            config.KEEPALIVE_INTERVAL,
        )
    logger.info("DATABASE_URL: %s", "set" if config.DATABASE_URL else "not set")
    logger.info("SUPABASE_URL: %s", "set" if config.SUPABASE_URL else "not set")
    logger.info("SUPABASE_PASSWORD: %s", "set" if config.SUPABASE_PASSWORD else "not set")
    logger.info(
        "SUPABASE_PUBLISHABLE_KEY: %s",
        "set" if config.SUPABASE_PUBLISHABLE_KEY else "not set",
    )
    logger.info(
        "SUPABASE_SECRET_KEY: %s",
        "set" if config.SUPABASE_SECRET_KEY else "not set",
    )
    logger.info("SUPABASE_JWKS_URL: %s", "set" if config.SUPABASE_JWKS_URL else "not set")
    await manager.connect_db()
    if not manager.db.connected:
        logger.error(
            "Database connection failed. Set DATABASE_URL or SUPABASE_URL/SUPABASE_PASSWORD correctly."
        )
        raise RuntimeError("Database connection failed; aborting startup.")
    logger.info("Database connection state: connected")
    logger.info(
        "VK Callback server listening on %s:%s%s",
        config.WEBAPP_HOST,
        config.WEBAPP_PORT,
        config.CALLBACK_PATH,
    )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=config.WEBAPP_HOST, port=config.WEBAPP_PORT)
    await site.start()

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main() -> None:
    asyncio.run(_app_main())


def _loop_exception_handler(loop, context: dict) -> None:
    logger.error("Unhandled exception in event loop: %s", context.get("message"))
    exc = context.get("exception")
    if exc is not None:
        logger.exception(
            "Loop exception details",
            exc_info=(type(exc), exc, exc.__traceback__),
        )


if __name__ == "__main__":
    main()
