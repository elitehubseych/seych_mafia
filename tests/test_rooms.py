"""Проверка комнат мини-аппа (rooms.py) и подписи launch-params (app_api.py).

Запуск:
    .venv\\Scripts\\python -m pytest tests -q -o asyncio_mode=auto
"""

import base64
import hashlib
import hmac
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("VK_TOKEN", "vk1.a.test")
os.environ.setdefault("VK_CONFIRMATION", "test_confirmation")

import config

config.NIGHT_SECONDS = 3600
config.VOTE_SECONDS = 3600
config.DISCUSSION_SECONDS = 0
config.CONFIRM_SECONDS = 3600
config.MORNING_DELAY = 0
config.LYNCH_DELAY = 0

from app_api import _verify_sign  # noqa: E402
from roles import MAFIA_SIDE, Role  # noqa: E402
from rooms import room_manager  # noqa: E402


class FakeVK:
    def __init__(self):
        self.sent = []
        self.edited = []

    async def get_user_name(self, user_id: int) -> str:
        return f"Игрок{user_id}"

    async def get_user_avatar(self, user_id: int) -> str:
        return ""

    async def send(self, peer_id, text, keyboard=None, **kw):
        self.sent.append((peer_id, text))
        return len(self.sent)

    async def edit(self, peer_id, message_id, text, keyboard=None, conversation_message_id=None):
        self.edited.append(text)
        return True


def _make_room(chat_id: int, admin: int = 1, uids=(1, 2, 3, 4)):
    vk = FakeVK()
    room = room_manager.create(chat_id, admin, vk)
    return room


async def test_room_create_and_join():
    room = _make_room(-1001)
    assert len(room.room_id) == 6
    assert room.room_id.isalnum()
    assert room.game.state == "waiting"

    ok, msg = await room.do_action(1, "join", {})
    assert ok, msg
    assert 1 in room.game.players
    assert any(e["vis"] == "all" and "присоединился" in e["text"] for e in room.timeline)

    snap = room.snapshot(1)
    assert snap["state"] == "waiting"
    assert snap["you"]["in_game"] is True
    assert snap["you"]["awaiting"] == "wait"

    # второй раз — «уже в игре»
    ok, _ = await room.do_action(1, "join", {})
    assert ok

    # не хватает игроков для старта
    ok, _ = await room.do_action(1, "start", {})
    assert not ok

    room_manager.remove(room.room_id)


async def test_room_admin_start_and_roles():
    room = _make_room(-1002, admin=1)
    for uid in (1, 2, 3, 4):
        await room.do_action(uid, "join", {})
    ok, _ = await room.do_action(2, "start", {})
    assert not ok, "стартовать может только создатель"

    ok, _ = await room.do_action(1, "start", {})
    assert ok
    g = room.game
    assert g.state == "night"
    assert g.night_number == 1
    assert all(p.role is not None for p in g.players.values())

    # свою роль видно
    snap = room.snapshot(1)
    me = next(p for p in snap["players"] if p["uid"] == 1)
    assert me["role"] is not None

    # мирный не видит роли живого соседа
    civ = next(p for p in g.players.values() if p.role == Role.CITIZEN)
    other = next(p for p in g.players.values() if p.user_id != civ.user_id and p.alive)
    snap_civ = room.snapshot(civ.user_id)
    row = next(p for p in snap_civ["players"] if p["uid"] == other.user_id)
    assert row["role"] is None, "роль живого игрока не должна быть видна"

    room_manager.remove(room.room_id)


async def test_room_mafia_chat_visibility():
    room = _make_room(-1003, admin=1)
    for uid in range(1, 7):
        await room.do_action(uid, "join", {})
    await room.do_action(1, "start", {})
    g = room.game

    mafia = [p for p in g.players.values() if p.role in MAFIA_SIDE]
    civ = next(p for p in g.players.values() if p.role == Role.CITIZEN)
    assert len(mafia) >= 2, "при 6 игроках должно быть 2 мафии"

    author = mafia[0]
    ally = next(m for m in mafia if m.user_id != author.user_id)
    before = len(room.timeline)
    await g.mafia_chat_message(author, "секретное слово")
    new = room.timeline[before:]
    assert new, "сообщение мафии попадает в таймлайн"
    assert all(e["vis"].startswith("user:") for e in new)

    # гражданин не видит переписку мафии
    snap_civ = room.snapshot(civ.user_id)
    assert not any("секретное слово" in e["text"] for e in snap_civ["timeline"])

    # союзник видит
    snap_ally = room.snapshot(ally.user_id)
    assert any("секретное слово" in e["text"] for e in snap_ally["timeline"])

    room_manager.remove(room.room_id)


async def test_room_closed_after_end():
    room = _make_room(-1004, admin=1)
    for uid in (1, 2, 3, 4):
        await room.do_action(uid, "join", {})
    await room.do_action(1, "start", {})
    ok, _ = await room.do_action(1, "stop", {})
    assert ok
    assert room.game.state == "ended"

    ok, _ = await room.do_action(5, "join", {})
    assert not ok, "в завершённую игру нельзя войти"

    room_manager.remove(room.room_id)


async def test_room_actions_and_timeline():
    room = _make_room(-1005, admin=1)
    for uid in range(1, 7):
        await room.do_action(uid, "join", {})
    await room.do_action(1, "start", {})
    g = room.game

    mafia = next(p for p in g.players.values() if p.role in MAFIA_SIDE)
    civ = next(p for p in g.players.values() if p.role == Role.CITIZEN)
    ally = next(p for p in g.players.values() if p.user_id != mafia.user_id and p.role in MAFIA_SIDE)

    # ночью гражданин спать не может писать
    ok, _ = await room.do_action(civ.user_id, "chat", {"text": "привет"})
    assert not ok

    # мафия ночью пишет в секретный чат
    before = len(room.timeline)
    ok, msg = await room.do_action(mafia.user_id, "chat", {"text": "привет всем"})
    assert ok, msg
    new = room.timeline[before:]
    assert any(e["vis"] == f"user:{ally.user_id}" and "привет всем" in e["text"] for e in new)

    # пустое сообщение
    ok, _ = await room.do_action(mafia.user_id, "chat", {"text": "   "})
    assert not ok

    # неизвестное действие
    ok, _ = await room.do_action(mafia.user_id, "hack", {})
    assert not ok

    room_manager.remove(room.room_id)


async def test_verify_sign():
    config.VK_APP_SECRET = "SECRET"
    params = {"vk_user_id": "42", "vk_app_id": "123"}
    vk_keys = sorted(k for k in params if k.startswith("vk_"))
    query = urlencode({k: params[k] for k in vk_keys}, doseq=True)
    digest = hmac.new(
        config.VK_APP_SECRET.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    params["sign"] = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    assert _verify_sign(params)

    params["sign"] = "deadbeef"
    assert not _verify_sign(params)

    # без подписи — по умолчанию разрешено (режим отладки)
    assert _verify_sign({"vk_user_id": "42"})
