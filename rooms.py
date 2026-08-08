from __future__ import annotations

import asyncio
import json
import logging
import secrets
import string
import time

import aiohttp

import config
from game import Game
from game_manager import manager
from roles import MAFIA_SIDE, ROLE_EMOJI, ROLE_RU, Role

logger = logging.getLogger(__name__)

ID_ALPHABET = string.ascii_uppercase + string.digits
TIMELINE_LIMIT = 300


def _new_room_id() -> str:
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(6))


class RoomBot:
    """Заглушка-отправитель для комнатной игры.

    Перехватывает send/edit движка, пишет события в таймлайн комнаты и не шлёт
    ничего в VK (кроме правки сообщения регистрации в чате).
    """

    def __init__(self, room: "Room") -> None:
        self.room = room

    async def send(self, chat_id: int, text: str, keyboard=None) -> int | None:
        self.room.add_event(
            text,
            all_=chat_id == self.room.chat_id,
            user_id=None if chat_id == self.room.chat_id else chat_id,
        )
        return None

    async def edit(
        self,
        chat_id: int,
        message_id: int | None,
        text: str,
        keyboard=None,
        conversation_message_id: int | None = None,
    ) -> bool:
        game = self.room.game
        if (
            game is not None
            and message_id is not None
            and message_id == game.registration_message_id
        ):
            return await self.room.vk.edit(
                chat_id,
                message_id,
                text,
                keyboard=keyboard,
                conversation_message_id=conversation_message_id,
            )
        self.room.add_event(
            text,
            all_=chat_id == self.room.chat_id,
            user_id=None if chat_id == self.room.chat_id else chat_id,
        )
        return True


class Room:
    def __init__(self, room_id: str, chat_id: int, admin_uid: int, vk) -> None:
        self.room_id = room_id
        self.chat_id = chat_id
        self.admin_uid = admin_uid
        self.vk = vk
        self.created_at = time.time()
        self.closed_at: float | None = None
        self.bot = RoomBot(self)
        self.game = Game(chat_id, self.bot)
        self.timeline: list[dict] = []
        self._evt = 0
        self.clients: dict[int, list[aiohttp.web.WebSocketResponse]] = {}
        self._avatars: dict[int, str] = {}
        self._pushing = False
        self._push_pending = False

    # ---------------------------------------------------------------- events
    def add_event(self, text: str, *, all_: bool = False, user_id: int | None = None) -> None:
        self._evt += 1
        self.timeline.append(
            {
                "id": self._evt,
                "vis": "all" if all_ else f"user:{user_id}",
                "text": text,
            }
        )
        if len(self.timeline) > TIMELINE_LIMIT:
            del self.timeline[:-TIMELINE_LIMIT]
        self.notify()

    def notify(self) -> None:
        """Пометить, что нужно разослать состояние клиентам (с дедупликацией)."""
        if self._pushing:
            self._push_pending = True
            return
        asyncio.create_task(self._push_worker())

    async def _push_worker(self) -> None:
        self._pushing = True
        try:
            while True:
                await self.push_state()
                if not self._push_pending:
                    break
                self._push_pending = False
        except Exception:  # noqa: BLE001
            logger.exception("push_state crashed")
        finally:
            self._pushing = False

    async def push_state(self) -> None:
        clients = list(self.clients.items())
        for uid, sockets in clients:
            try:
                snap = json.dumps(self.snapshot(uid), ensure_ascii=False)
            except Exception:  # noqa: BLE001
                logger.exception("snapshot failed for %s", uid)
                continue
            for ws in list(sockets):
                try:
                    await ws.send_str(snap)
                except Exception:  # noqa: BLE001
                    self.unregister_client(uid, ws)

    def register_client(self, uid: int, ws) -> None:
        self.clients.setdefault(uid, []).append(ws)

    def unregister_client(self, uid: int, ws) -> None:
        sockets = self.clients.get(uid)
        if not sockets:
            return
        if ws in sockets:
            sockets.remove(ws)
        if not sockets:
            self.clients.pop(uid, None)

    # -------------------------------------------------------------- snapshots
    def _you_block(self, uid: int) -> dict:
        g = self.game
        p = g.players.get(uid)
        alive = bool(p and p.alive)
        role = p.role if p else None
        is_mafia = alive and role in MAFIA_SIDE
        you: dict = {
            "in_game": bool(p),
            "alive": alive if p else None,
            "role": role.value if role else None,
            "role_ru": ROLE_RU[role] if role else None,
            "role_emoji": ROLE_EMOJI[role] if role else None,
            "awaiting": None,
            "can_skip": False,
            "can_chat": False,
            "chat_hint": None,
            "targets": [],
            "vote_targets": [],
            "last_words_open": False,
            "blocked_vote": bool(p and p.blocked_vote),
        }

        if g.state == "night":
            need = g.night.awaiting_target.get(uid)
            if need is not None:
                if need == Role.COMMISSAR and uid not in g.night.commissar_mode:
                    you["awaiting"] = "mode"
                else:
                    you["awaiting"] = "target"
                    you["targets"] = self._targets_for(uid)
                you["can_skip"] = g.check_skip(uid)
            if p and alive:
                if is_mafia:
                    you["can_chat"] = True
                    you["chat_hint"] = "Секретный чат мафии"
                elif role == Role.COMMISSAR:
                    you["can_chat"] = True
                    you["chat_hint"] = "Секретный чат с сержантом"
        elif g.state == "voting":
            if p and alive and g.day_open and not p.blocked_vote and uid not in g.votes:
                you["awaiting"] = "vote"
                you["vote_targets"] = [
                    q.user_id for q in g.alive_players if q.user_id != uid
                ]
            if p and alive:
                you["can_chat"] = True
                you["chat_hint"] = "Общий чат города"
        elif g.state == "confirm":
            if p and alive and not p.blocked_vote and uid not in g.confirm_likes and uid not in g.confirm_dislikes:
                you["awaiting"] = "confirm"
            if p and alive:
                you["can_chat"] = True
                you["chat_hint"] = "Общий чат города"
        elif g.state == "waiting":
            you["awaiting"] = "join" if not p else "wait"
        elif g.state == "ended":
            you["awaiting"] = None

        if p and uid in g.last_words_open:
            you["last_words_open"] = True
        return you

    def _targets_for(self, uid: int) -> list[int]:
        g = self.game
        if g.state != "night":
            return []
        if uid not in g.night.awaiting_target:
            return []
        return [tuid for tuid in g.alive_players if g.check_target(uid, tuid.user_id)]

    def snapshot(self, uid: int) -> dict:
        g = self.game
        p = g.players.get(uid)
        alive = bool(p and p.alive)
        is_mafia = alive and p.role in MAFIA_SIDE

        players = []
        for pl in sorted(g.players.values(), key=lambda x: x.number):
            reveal = (
                pl.user_id == uid
                or not pl.alive
                or (is_mafia and pl.alive and pl.role in MAFIA_SIDE)
                or g.state == "ended"
            )
            players.append(
                {
                    "uid": pl.user_id,
                    "num": pl.number,
                    "name": pl.name,
                    "avatar": self._avatars.get(pl.user_id, ""),
                    "alive": pl.alive,
                    "banned": pl.banned,
                    "is_bot": pl.is_bot,
                    "role": pl.role.value if reveal and pl.role else None,
                    "role_ru": ROLE_RU[pl.role] if reveal and pl.role else None,
                    "role_emoji": ROLE_EMOJI[pl.role] if reveal and pl.role else None,
                }
            )

        timeline = [
            e
            for e in self.timeline
            if e["vis"] == "all" or e["vis"] == f"user:{uid}"
        ]

        confirm = None
        if g.state == "confirm":
            t = g.players.get(g.confirm_target_uid) if g.confirm_target_uid else None
            confirm = {
                "target": g.confirm_target_uid,
                "target_name": t.name if t else None,
                "likes": len(g.confirm_likes),
                "dislikes": len(g.confirm_dislikes),
                "voted": bool(p and (uid in g.confirm_likes or uid in g.confirm_dislikes)),
            }

        if g.state == "ended":
            if g.winner == "mafia":
                ended_title = "🎉 Мафия победила!"
            elif g.winner == "town":
                ended_title = "🎉 Город победил! Мирные жители выиграли!"
            elif g.winner == "maniac":
                ended_title = "🎉 Маньяк победил! Он остался последним выжившим."
            else:
                ended_title = "⏹️ Игра остановлена администратором."
        else:
            ended_title = None

        return {
            "room_id": self.room_id,
            "chat_id": self.chat_id,
            "admin": self.admin_uid,
            "is_admin": uid == self.admin_uid,
            "state": g.state,
            "night_number": g.night_number,
            "me": uid,
            "players": players,
            "timeline": timeline,
            "confirm": confirm,
            "ended_title": ended_title,
            "you": self._you_block(uid),
        }

    # ---------------------------------------------------------------- actions
    async def do_action(self, uid: int, action: str, payload: dict) -> tuple[bool, str]:
        g = self.game
        p = g.players.get(uid)

        if action == "join":
            ok, msg = await self._join(uid)
            self.notify()
            return ok, msg

        if g.state == "ended":
            return False, "Игра завершена"

        if action == "start":
            if uid != self.admin_uid:
                return False, "⛔ Только создатель комнаты"
            if g.state != "waiting":
                return False, "Игра уже идёт"
            if len(g.players) < config.MIN_PLAYERS:
                return False, f"Нужно минимум {config.MIN_PLAYERS} игроков"
            await g.start_game()
            return True, "🚀 Игра началась!"

        if action == "stop":
            if uid != self.admin_uid:
                return False, "⛔ Только создатель комнаты"
            await g.end_game("stop")
            return True, "⏹️ Игра остановлена"

        if action == "chat":
            text = (payload.get("text") or "").strip()
            if not text:
                return False, "Пустое сообщение"
            if len(text) > 500:
                return False, "Слишком длинное сообщение"
            if not p or not p.alive:
                return False, "Ты не можешь писать"
            if g.state == "night":
                if p.role in MAFIA_SIDE:
                    await g.mafia_chat_message(p, text)
                    self.add_event(f"💬 {p.name}: {text}", user_id=uid)
                    return True, "Отправлено в чат мафии"
                if p.role == Role.COMMISSAR:
                    await g.commissar_chat_message(p, text)
                    self.add_event(f"💬 {p.name}: {text}", user_id=uid)
                    return True, "Отправлено сержанту"
                return False, "Ночью ты спишь"
            await g.broadcast(f"💬 {g._link(p)}: {text}")
            try:
                await self.vk.send(self.chat_id, f"💬 {g._link(p)}: {text}")
            except Exception:  # noqa: BLE001
                logger.warning("forward chat to VK failed", exc_info=True)
            return True, "Сообщение отправлено"

        if action == "lastwords":
            if not p or uid not in g.last_words_open:
                return False, "Нет права на последние слова"
            text = (payload.get("text") or "").strip()
            if not text:
                return False, "Напиши текст"
            await g.submit_last_words(uid, text)
            return True, "Последние слова переданы городу"

        if action == "mode":
            mode = payload.get("mode")
            if g.check_mode(uid, mode):
                await g.submit_mode(uid, mode)
                return True, "Действие выбрано"
            return False, "Нельзя сейчас"

        if action == "target":
            raw = payload.get("uid")
            try:
                target_uid = int(raw)
            except (TypeError, ValueError):
                return False, "Неверная цель"
            if g.check_target(uid, target_uid):
                await g.submit_target(uid, target_uid)
                t = g.players.get(target_uid)
                self.add_event(f"✅ Ты выбрал: {t.name if t else target_uid}", user_id=uid)
                return True, "Выбор сделан"
            return False, "Нельзя выбрать этого игрока"

        if action == "skip":
            if g.check_skip(uid):
                await g.submit_skip(uid)
                self.add_event("✅ Ты пропустил ход", user_id=uid)
                return True, "Ход пропущен"
            return False, "Нельзя сейчас"

        if action == "abstain":
            if g.check_vote(uid):
                await g.submit_vote(uid, None)
                self.add_event("✅ Ты воздержался", user_id=uid)
                return True, "Ты воздержался"
            return False, "Нельзя голосовать"

        if action == "vote":
            if not g.check_vote(uid):
                return False, "Нельзя голосовать"
            raw = payload.get("uid")
            try:
                target_uid = int(raw)
            except (TypeError, ValueError):
                return False, "Неверная цель"
            t = g.players.get(target_uid)
            if not t or not t.alive or target_uid == uid:
                return False, "Нельзя голосовать за этого игрока"
            await g.submit_vote(uid, target_uid)
            self.add_event(f"✅ Твой голос: {t.name}", user_id=uid)
            return True, "Голос учтён"

        if action == "confirm":
            vote = payload.get("v")
            if vote not in {"like", "dislike"}:
                return False, "Неверный выбор"
            if g.state != "confirm":
                return False, "Сейчас не голосование за казнь"
            ok = await g.submit_confirm(uid, vote)
            return (True, "Засчитано") if ok else (False, "Ты уже голосовал")

        return False, "Неизвестное действие"

    async def _join(self, uid: int) -> tuple[bool, str]:
        g = self.game
        ban = manager.get_ban(uid)
        if ban:
            if ban["until"] is None:
                return False, f"Вы забанены в игре навсегда\nПричина: {ban['reason'] or '—'}"
            return False, f"Вы забанены в игре на {ban['duration']}\nПричина: {ban['reason'] or '—'}"
        if uid in g.players:
            return True, "Ты уже в игре"
        if g.state != "waiting":
            return False, "Игра уже началась"
        if len(g.players) >= config.MAX_PLAYERS:
            return False, "Мест больше нет"
        base = await self.vk.get_user_name(uid)
        name = manager.nickname(uid, base)
        await manager.register(uid)
        if not g.add_player(uid, name):
            return False, "Не удалось присоединиться"
        try:
            avatar = await self.vk.get_user_avatar(uid)
            if avatar:
                self._avatars[uid] = avatar
        except Exception:  # noqa: BLE001
            logger.warning("avatar fetch failed for %s", uid, exc_info=True)
        await g.broadcast(f"➕ {g._link(g.players[uid])} присоединился к игре.")
        await g.update_registration_message()
        if len(g.players) >= config.MAX_PLAYERS:
            await g.start_game()
        return True, "Ты в игре!"


class RoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        self._by_chat: dict[int, str] = {}

    def create(self, chat_id: int, admin_uid: int, vk) -> Room:
        room = Room(_new_room_id(), chat_id, admin_uid, vk)
        self.rooms[room.room_id] = room
        self._by_chat[chat_id] = room.room_id
        return room

    def get(self, room_id: str) -> Room | None:
        return self.rooms.get(room_id)

    def for_chat(self, chat_id: int) -> Room | None:
        rid = self._by_chat.get(chat_id)
        if rid:
            return self.rooms.get(rid)
        return None

    def remove(self, room_id: str) -> None:
        room = self.rooms.pop(room_id, None)
        if room and self._by_chat.get(room.chat_id) == room_id:
            del self._by_chat[room.chat_id]

    def sweep(self) -> None:
        now = time.time()
        for rid, room in list(self.rooms.items()):
            if now - room.created_at > 2 * 3600:
                self.remove(rid)


room_manager = RoomManager()
