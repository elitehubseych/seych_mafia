from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

import config
from rooms import room_manager

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = {
    "https://vk.com",
    "https://m.vk.com",
    "https://vk.ru",
    "https://m.vk.ru",
    "https://web.vk.com",
    "https://web.vk.ru",
} | {o.rstrip("/") for o in config.APP_CORS_ORIGINS}
if config.APP_BASE_URL:
    ALLOWED_ORIGINS.add(config.APP_BASE_URL.rstrip("/"))


def _json(status: int, data) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def _parse_launch(query) -> dict:
    return {k: v for k, v in query.items() if k.startswith("vk_") or k == "sign"}


def _verify_sign(params: dict) -> bool:
    sign = params.get("sign")
    if not sign:
        return config.APP_ALLOW_UNSIGNED
    if not config.VK_APP_SECRET:
        return False
    vk_keys = sorted(k for k in params if k.startswith("vk_"))
    if not vk_keys:
        return False
    query = urlencode({k: params[k] for k in vk_keys}, doseq=True)
    digest = hmac.new(
        config.VK_APP_SECRET.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return hmac.compare_digest(expected, sign)


def _user_uid(params: dict) -> int | None:
    raw = params.get("vk_user_id")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _cors_headers(origin: str) -> dict:
    if origin.rstrip("/") in ALLOWED_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Vary": "Origin",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    return {}


@web.middleware
async def cors_middleware(request: web.Request, handler) -> web.Response:
    origin = request.headers.get("Origin", "")
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_cors_headers(origin))
    resp = await handler(request)
    headers = _cors_headers(origin)
    if headers:
        resp.headers.update(headers)
    return resp


async def _body_json(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}


async def state_handler(request: web.Request) -> web.Response:
    params = _parse_launch(request.query)
    if not _verify_sign(params):
        return _json(403, {"ok": False, "error": "invalid sign"})
    uid = _user_uid(params)
    if uid is None:
        return _json(400, {"ok": False, "error": "no user"})
    room = room_manager.get(request.query.get("room_id", ""))
    if room is None:
        return _json(404, {"ok": False, "error": "room not found"})
    return _json(200, room.snapshot(uid))


async def action_handler(request: web.Request) -> web.Response:
    data = await _body_json(request)
    params = _parse_launch(data.get("params") or {})
    if not _verify_sign(params):
        return _json(403, {"ok": False, "error": "invalid sign"})
    uid = _user_uid(params)
    if uid is None:
        return _json(400, {"ok": False, "error": "no user"})
    room = room_manager.get(data.get("room_id", ""))
    if room is None:
        return _json(404, {"ok": False, "error": "room not found"})
    action = data.get("action")
    payload = data.get("payload") or {}
    if not isinstance(action, str) or not action:
        return _json(400, {"ok": False, "error": "bad action"})
    try:
        ok, msg = await room.do_action(uid, action, payload)
    except Exception:  # noqa: BLE001
        logger.exception("room action %s failed for %s", action, uid)
        return _json(500, {"ok": False, "error": "server error"})
    return _json(200, {"ok": ok, "msg": msg})


async def ws_handler(request: web.Request) -> web.Response:
    params = _parse_launch(request.query)
    if not _verify_sign(params):
        return web.Response(status=403, text="invalid sign")
    uid = _user_uid(params)
    if uid is None:
        return web.Response(status=400, text="no user")
    room = room_manager.get(request.query.get("room_id", ""))
    if room is None:
        return web.Response(status=404, text="room not found")

    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    room.register_client(uid, ws)
    try:
        await ws.send_str(json.dumps(room.snapshot(uid), ensure_ascii=False))
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT and msg.data == "ping":
                await ws.send_str(json.dumps({"type": "pong"}))
            elif msg.type == aiohttp.WSMsgType.ERROR:
                break
    except Exception:  # noqa: BLE001
        logger.exception("websocket closed unexpectedly for %s", uid)
    finally:
        room.unregister_client(uid, ws)
    return ws


def add_app_routes(app: web.Application) -> None:
    app.middlewares.append(cors_middleware)
    app.router.add_get("/app/ws", ws_handler)
    app.router.add_get("/app/state", state_handler)
    app.router.add_post("/app/action", action_handler)
    logger.info("Mini-app API routes registered (frontend раздаётся с внешнего хостинга)")
