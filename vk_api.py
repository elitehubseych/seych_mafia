from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl

import aiohttp

import config

logger = logging.getLogger(__name__)

VK_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=8, sock_read=10)
VK_REQUEST_TIMEOUT = 12.0


def _make_connector() -> aiohttp.TCPConnector:
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
        return aiohttp.TCPConnector(ssl=context)
    except Exception:  # noqa: BLE001
        return aiohttp.TCPConnector(ssl=False)


class VKAPI:
    def __init__(self, token: str, api_version: str = "5.199") -> None:
        self.token = token
        self.api_version = api_version
        self._session: aiohttp.ClientSession | None = None
        self._names: dict[int, str] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=_make_connector(), timeout=VK_TIMEOUT
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            try:
                await asyncio.wait_for(self._session.close(), timeout=5)
            except Exception:  # noqa: BLE001
                pass
        self._session = None

    async def _reset_session(self) -> None:
        if self._session is not None and not self._session.closed:
            try:
                await asyncio.wait_for(self._session.close(), timeout=5)
            except Exception:  # noqa: BLE001
                pass
        self._session = None

    async def call(self, method: str, **params):
        session = await self._get_session()
        params["access_token"] = self.token
        params["v"] = self.api_version

        async def _do():
            async with session.get(
                f"https://api.vk.com/method/{method}", params=params
            ) as resp:
                return await resp.json(content_type=None)

        try:
            data = await asyncio.wait_for(_do(), timeout=VK_REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("VK API request timed out %s; resetting session", method)
            await self._reset_session()
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("VK API transport error %s: %s; resetting session", method, e)
            await self._reset_session()
            return None
        if "error" in data:
            err = data["error"]
            logger.error(
                "VK API error %s [%s]: %s", method, err.get("error_code"), err.get("error_msg")
            )
            return None
        return data.get("response")

    async def send(self, peer_id: int, text: str, keyboard=None, **extra) -> int | None:
        params = {
            "peer_ids": str(peer_id),
            "message": text,
            "random_id": random.randint(1, 2**31),
        }
        if keyboard is not None:
            params["keyboard"] = self._to_json(keyboard)
        params.update(extra)
        resp = await self.call("messages.send", **params)
        if isinstance(resp, list):
            if not resp:
                return None
            item = resp[0]
            if "error" in item:
                logger.error("VK messages.send error for %s: %s", peer_id, item)
                return None
            return item.get("message_id") or item.get("conversation_message_id")
        if isinstance(resp, dict):
            return resp.get("message_id") or resp.get("conversation_message_id")
        if isinstance(resp, int):
            return resp
        return None

    async def edit(
        self,
        peer_id: int,
        message_id: int | None = None,
        text: str | None = None,
        keyboard=None,
        conversation_message_id: int | None = None,
    ) -> bool:
        params = {"peer_id": peer_id}
        if text is not None:
            params["message"] = text
        if conversation_message_id is not None:
            params["conversation_message_id"] = conversation_message_id
        elif message_id is not None:
            params["message_id"] = message_id
        if keyboard is not None:
            params["keyboard"] = self._to_json(keyboard)
        resp = await self.call("messages.edit", **params)
        return resp is not None

    async def is_dm_allowed(self, user_id: int) -> bool:
        try:
            group_id = int(config.VK_GROUP_ID)
        except (TypeError, ValueError):
            return False
        resp = await self.call(
            "messages.isMessagesFromGroupAllowed",
            group_id=group_id,
            user_id=user_id,
        )
        return bool(resp and resp.get("is_allowed"))

    async def answer_event(self, event_id: int, user_id: int, peer_id: int, text: str) -> None:
        event_data = json.dumps({"type": "show_snackbar", "text": text}, ensure_ascii=False)
        await self.call(
            "messages.sendMessageEventAnswer",
            event_id=event_id,
            user_id=user_id,
            peer_id=peer_id,
            event_data=event_data,
        )

    async def is_chat_admin(self, peer_id: int, user_id: int) -> bool:
        resp = await self.call("messages.getConversationMembers", peer_id=peer_id)
        if not resp:
            return False
        for item in resp.get("items", []):
            if item.get("member_id") == user_id:
                return bool(item.get("is_admin")) or item.get("role") in {"admin", "creator"}
        return False

    async def get_chat_title(self, peer_id: int) -> str | None:
        resp = await self.call("messages.getConversationsById", peer_ids=str(peer_id))
        if not resp:
            return None
        items = resp.get("items")
        if not items:
            return None
        conv = items[0]
        chat_settings = conv.get("chat_settings") or {}
        title = chat_settings.get("title")
        if title:
            return title
        peer = conv.get("peer") or {}
        return peer.get("local_id") and f"чат {peer.get('local_id')}"

    async def get_user_name(self, user_id: int) -> str:
        if user_id in self._names:
            return self._names[user_id]
        resp = await self.call("users.get", user_ids=user_id)
        if resp:
            u = resp[0]
            name = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
            if name:
                self._names[user_id] = name
                return name
        return f"Игрок{user_id}"

    @staticmethod
    def _to_json(keyboard) -> str:
        if isinstance(keyboard, str):
            return keyboard
        return json.dumps(keyboard, ensure_ascii=False)


vk = VKAPI(config.VK_TOKEN, config.VK_API_VERSION)
