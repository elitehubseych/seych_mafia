from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import config
from game import Game
from game_manager import manager
from keyboards import bot_dm_kb, join_kb
from roles import MAFIA_SIDE, Role
from vk_api import VKAPI

logger = logging.getLogger(__name__)

CHAT_PREFIX = 2_000_000_000

_pending_ban_reason: dict[int, dict] = {}

DURATION_UNITS = {
    "минут": "minutes",
    "минута": "minutes",
    "минуту": "minutes",
    "минуты": "minutes",
    "час": "hours",
    "часа": "hours",
    "часов": "hours",
    "день": "days",
    "дня": "days",
    "дней": "days",
    "сутки": "days",
    "суток": "days",
    "месяц": "months",
    "месяца": "months",
    "месяцев": "months",
    "год": "years",
    "года": "years",
    "лет": "years",
}
_MINUTES_PER = {
    "minutes": 1,
    "hours": 60,
    "days": 1440,
    "months": 43800,
    "years": 525600,
}


def chat_peer_to_id(peer_id: int) -> int:
    return peer_id - CHAT_PREFIX


def _player_link(game: Game, uid: int) -> str:
    p = game.players.get(uid)
    return f"[id{uid}|{p.name}]" if p else "—"


def _resolve_target_id(text: str, reply: dict) -> int | None:
    if reply and isinstance(reply.get("from_id"), int) and reply.get("from_id", 0) > 0:
        return int(reply["from_id"])
    m = re.search(r"\[(?:id|club|public|screen)(\d+)\|", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\bid(\d+)\b", text, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    for tok in text.split():
        if tok.isdigit():
            return int(tok)
    return None


def _remove_target_token(text: str, target: int) -> str:
    rest = text
    rest = re.sub(rf"\[(?:id|club|public|screen){target}\|[^\]]*\]", "", rest)
    rest = re.sub(rf"\bid{target}\b", "", rest, flags=re.IGNORECASE)
    rest = re.sub(rf"(?<![\w\d]){target}(?![\w\d])", "", rest)
    return rest.strip()


def _split_duration_reason(line: str) -> tuple[str, object | None, str]:
    line = (line or "").strip()
    tokens = line.split()
    if not tokens:
        return "навсегда", None, ""
    if tokens[0].isdigit():
        num = int(tokens[0])
        rest = tokens[1:]
        if rest and rest[0].lower() in DURATION_UNITS:
            unit = rest[0]
            minutes = num * _MINUTES_PER[DURATION_UNITS[unit.lower()]]
            until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            return f"{tokens[0]} {unit}", until, " ".join(rest[1:])
        until = datetime.now(timezone.utc) + timedelta(minutes=num)
        return tokens[0], until, " ".join(rest)
    if tokens[0].lower() in {"навсегда", "навечно"}:
        return "навсегда", None, " ".join(tokens[1:])
    return "навсегда", None, line


def _parse_ban_duration(text: str) -> tuple[str, object | None]:
    duration_text, until, _ = _split_duration_reason(text)
    return duration_text, until


async def _name_link(vk: VKAPI, uid: int) -> str:
    name = await display_name(vk, uid)
    return f"[id{uid}|{name}]"


async def _send_report(vk: VKAPI, user_id: int, text: str, reply: dict) -> None:
    if not config.DEV_ID:
        logger.info("DEV_ID не задан: репорт от %s не отправлен", user_id)
        return
    try:
        dev_id = int(config.DEV_ID)
    except (TypeError, ValueError):
        logger.warning("DEV_ID некорректен: %r", config.DEV_ID)
        return
    body = text.strip()
    reply = reply or {}
    logger.info(
        "report from %s text=%r reply_keys=%s reply_from=%r",
        user_id,
        body,
        sorted(reply.keys()),
        reply.get("from_id"),
    )
    violator_id = reply.get("from_id")
    if isinstance(violator_id, int) and violator_id > 0:
        lines = [
            f"Новый репорт от: {await _name_link(vk, user_id)}",
            f"Нарушитель: {await _name_link(vk, violator_id)}",
        ]
        reply_text = (reply.get("text") or "").strip()
        if reply_text:
            lines.append(f"Реплай: {reply_text}")
        if body:
            lines.append(f"Текст: {body}")
    else:
        if not body:
            return
        lines = [
            f"Новый репорт от: {await _name_link(vk, user_id)}",
            f"Текст: {body}",
        ]
    await vk.send(dev_id, "\n".join(lines))


async def cmd_bangame(
    vk: VKAPI,
    peer_id: int,
    user_id: int,
    text: str,
    reply: dict,
) -> None:
    if not config.is_dev(user_id):
        return
    rest = text.split(maxsplit=1)[1].strip() if " " in text else ""
    target = _resolve_target_id(rest, reply)
    if target is None:
        await vk.send(
            peer_id,
            "ℹ️ Не нашёл игрока. Укажи id, @упоминание или ответь на сообщение.",
        )
        return
    rest = _remove_target_token(rest, target)
    duration_text, until, reason = _split_duration_reason(rest)
    if not reason:
        _pending_ban_reason[user_id] = {
            "peer_id": peer_id,
            "target": target,
            "duration": duration_text,
            "until": until,
        }
        await vk.send(
            peer_id,
            "ℹ️ Напиши причину бана следующим сообщением (или любую команду, чтобы отменить).",
        )
        return
    await _apply_ban(vk, peer_id, target, duration_text, until, reason)


async def _apply_ban(
    vk: VKAPI,
    peer_id: int,
    target: int,
    duration_text: str,
    until: object | None,
    reason: str,
) -> None:
    await manager.ban(target, reason, duration_text, until)
    link = await _name_link(vk, target)
    await vk.send(
        peer_id,
        f"{link} был заблокирован во вселенной игры\n"
        f"Срок: {duration_text}\n"
        f"Причина: {reason or '—'}",
    )
    game = manager.game_for_user(target)
    if game:
        p = game.players.get(target)
        nick = f"[id{target}|{p.name}]" if p else link
        if p and game.state != "waiting" and p.alive:
            p.alive = False
            p.banned = True
            if until is None:
                chat_text = f"{nick} был заблокирован доступ к игре навсегда\nПричина: {reason or '—'}"
            else:
                chat_text = (
                    f"{nick} был заблокирован доступ к игре на {duration_text}\n"
                    f"Причина: {reason or '—'}"
                )
            await vk.send(game.chat_id, chat_text)
            await vk.send(
                target,
                "💀 Тебя убили!\nНапиши последние слова — город их услышит.",
            )
            return
        if p and game.state == "waiting":
            game.players.pop(target, None)
            await game.update_registration_message()
    await vk.send(
        target,
        "🚫 Тебе был заблокирован доступ в игру.\n"
        f"Срок: {duration_text}\n"
        f"Причина: {reason or '—'}",
    )


async def cmd_unbangame(
    vk: VKAPI,
    peer_id: int,
    user_id: int,
    text: str,
    reply: dict,
) -> None:
    if not config.is_dev(user_id):
        return
    rest = text.split(maxsplit=1)[1].strip() if " " in text else ""
    target = _resolve_target_id(rest, reply)
    if target is None:
        await vk.send(
            peer_id,
            "ℹ️ Не нашёл игрока. Укажи id, @упоминание или ответь на сообщение.",
        )
        return
    await manager.unban(target)
    link = await _name_link(vk, target)
    await vk.send(peer_id, f"{link} был разблокирован во вселенной игры")


async def _confirm_event_message(
    vk: VKAPI,
    obj: dict,
    text: str,
    keyboard=None,
) -> None:
    peer_id = obj.get("peer_id")
    conversation_message_id = obj.get("conversation_message_id")
    message_id = obj.get("message_id")
    if not peer_id or (conversation_message_id is None and message_id is None):
        logger.warning("message_event without peer_id/message_id: %s", obj)
        return
    kb = keyboard if keyboard is not None else {"inline": True, "buttons": []}
    if message_id is not None:
        if await vk.edit(peer_id, message_id=message_id, text=text, keyboard=kb):
            return
    if conversation_message_id is not None:
        if await vk.edit(
            peer_id, conversation_message_id=conversation_message_id, text=text, keyboard=kb
        ):
            return
    logger.warning(
        "confirm edit failed for peer_id=%s conversation_message_id=%s message_id=%s",
        peer_id,
        conversation_message_id,
        message_id,
    )


async def display_name(vk: VKAPI, user_id: int) -> str:
    base = await vk.get_user_name(user_id)
    return manager.nickname(user_id, base)


async def handle_message_new(vk: VKAPI, obj: dict) -> None:
    msg = obj.get("message") or {}
    peer_id = msg.get("peer_id")
    from_id = msg.get("from_id")
    text = (msg.get("text") or "").strip()
    reply = msg.get("reply_message") or {}
    if not reply:
        fwd = msg.get("fwd_messages") or []
        if fwd:
            reply = fwd[0]
    if peer_id is None or from_id is None:
        return

    pending = _pending_ban_reason.pop(from_id, None)
    if pending is not None and not text.startswith("/"):
        await _apply_ban(
            vk,
            pending["peer_id"],
            pending["target"],
            pending["duration"],
            pending["until"],
            text,
        )
        return

    is_chat = peer_id >= CHAT_PREFIX
    if is_chat:
        game = manager.game_for_chat(peer_id)
        if text.startswith("/"):
            await handle_chat_command(vk, peer_id, from_id, text, reply)
        elif game and game.state == "night":
            await handle_night_chat_message(vk, game, from_id, text)
        return

    if text.startswith("/"):
        await handle_private_command(vk, from_id, text, reply)
        return
    game = manager.game_for_user(from_id)
    if game and await game.submit_last_words(from_id, text):
        return
    if game and game.state == "night":
        await handle_night_chat_message(vk, game, from_id, text)
        return
    if game:
        p = game.players.get(from_id)
        if p and not p.alive:
            await vk.send(from_id, "💀 Ты мёртв. Напиши последние слова — город их услышит.")
            return
    await handle_private_text(vk, from_id)


async def handle_chat_command(
    vk: VKAPI, peer_id: int, user_id: int, text: str, reply: dict | None = None
) -> None:
    parts = text.split()
    cmd = parts[0].lower()
    game = manager.game_for_chat(peer_id)
    reply = reply or {}
    if cmd in {"/start", "/новость", "/начать"} and len(parts) == 1:
        await cmd_start_group(vk, peer_id, user_id, game)
    elif cmd == "/startadmin":
        await cmd_startadmin(vk, peer_id, user_id, game)
    elif cmd == "/startbot":
        await cmd_startbot(vk, peer_id, user_id, game)
    elif cmd == "/stopadmin":
        await cmd_stopadmin(vk, peer_id, user_id, game)
    elif cmd in {"/rep", "/report"}:
        await _send_report(vk, user_id, " ".join(parts[1:]), reply)
    elif cmd == "/bangame":
        await cmd_bangame(vk, peer_id, user_id, text, reply)
    elif cmd == "/unbangame":
        await cmd_unbangame(vk, peer_id, user_id, text, reply)
    elif cmd == "/help":
        await vk.send(peer_id, HELP_TEXT)


async def cmd_start_group(
    vk: VKAPI, peer_id: int, user_id: int, game: Game | None
) -> None:
    if game:
        if game.state == "waiting":
            names = "\n".join(
                f"{p.number}. [id{p.user_id}|{p.name}]"
                for p in sorted(game.players.values(), key=lambda x: x.number)
            )
            await vk.send(
                peer_id,
                f"🎭 Регистрация уже идёт! Участников: {len(game.players)}/{config.MAX_PLAYERS}\n\n{names}",
            )
            return
        if game.state in {"night", "voting"}:
            await vk.send(peer_id, "🎭 Игра уже идёт! Следи за чатом.")
            return
        manager.games.pop(peer_id, None)

    game = Game(peer_id, vk)
    manager.games[peer_id] = game
    reg_seconds = config.REGISTRATION_SECONDS
    minutes = reg_seconds // 60
    seconds = reg_seconds % 60
    duration_text = (
        f"{minutes} минут {seconds} секунд" if seconds else f"{minutes} минут"
    )
    lines = [
        "🎭 Регистрация в игру Мафия!",
        "",
        f"Участников: 0/{config.MAX_PLAYERS} (минимум {config.MIN_PLAYERS})",
        f"⏱️ Регистрация займёт {duration_text} или начнётся сразу при заполнении.",
        "",
        "Чтобы присоединиться, нажми кнопку ниже. "
        "Сначала напиши боту в личные сообщения — иначе он не сможет отправлять тебе роли.",
    ]
    sent = await vk.send(peer_id, "\n".join(lines), keyboard=join_kb())
    game.registration_message_id = sent
    game._reg_timer = asyncio.create_task(game.start_registration_timer())


async def cmd_startadmin(
    vk: VKAPI, peer_id: int, user_id: int, game: Game | None
) -> None:
    if not await vk.is_chat_admin(peer_id, user_id):
        await vk.send(peer_id, "⛔ Только для админов чата.")
        return
    if not game or game.state != "waiting":
        await vk.send(peer_id, "ℹ️ Сейчас нет игры в режиме регистрации.")
        return
    if len(game.players) < config.MIN_PLAYERS:
        await vk.send(peer_id, f"⚠️ Нужно минимум {config.MIN_PLAYERS} игроков.")
        return
    await game.start_game()


async def cmd_startbot(
    vk: VKAPI, peer_id: int, user_id: int, game: Game | None
) -> None:
    if not await vk.is_chat_admin(peer_id, user_id):
        await vk.send(peer_id, "⛔ Только для админов чата.")
        return
    if not game or game.state != "waiting":
        await vk.send(peer_id, "ℹ️ Сейчас нет игры в режиме регистрации.")
        return
    added = game.fill_with_bots(config.MAX_PLAYERS)
    await vk.send(
        peer_id,
        f"🤖 Добавлено ботов: {added}. Всего игроков: {len(game.players)}/{config.MAX_PLAYERS}",
    )
    await game.update_registration_message()
    if len(game.players) >= config.MIN_PLAYERS:
        await vk.send(peer_id, "🚀 Заполнено, начинаем!")
        await game.start_game()


async def cmd_stopadmin(
    vk: VKAPI, peer_id: int, user_id: int, game: Game | None
) -> None:
    if not await vk.is_chat_admin(peer_id, user_id):
        await vk.send(peer_id, "⛔ Только для админов чата.")
        return
    if not game or game.state == "ended":
        await vk.send(peer_id, "ℹ️ Игры нет или она уже завершена.")
        return
    await game.end_game("stop")


async def handle_private_command(
    vk: VKAPI, user_id: int, text: str, reply: dict | None = None
) -> None:
    parts = text.split()
    cmd = parts[0].lower()
    reply = reply or {}
    if cmd == "/setnick":
        nick = " ".join(parts[1:]).strip()
        if not nick:
            await vk.send(user_id, "ℹ️ Использование: /setnick ТвойНик")
            return
        await manager.set_nickname(user_id, nick)
        game = manager.game_for_user(user_id)
        if game and user_id in game.players:
            game.players[user_id].name = nick
        await vk.send(user_id, f"✅ Никнейм изменён: {nick}")
    elif cmd in {"/rep", "/report"}:
        await _send_report(vk, user_id, " ".join(parts[1:]), reply)
    elif cmd == "/bangame":
        await cmd_bangame(vk, user_id, user_id, text, reply)
    elif cmd == "/unbangame":
        await cmd_unbangame(vk, user_id, user_id, text, reply)
    elif cmd == "/help":
        await vk.send(user_id, HELP_TEXT)
    else:
        await handle_private_text(vk, user_id)


async def handle_private_text(vk: VKAPI, user_id: int) -> None:
    await manager.register(user_id)
    await vk.send(
        user_id,
        "✅ Вы успешно зарегистрировались в боте!\n"
        "Теперь можете в чате нажать на кнопку регистрации — "
        "ник придумывать необязательно.\n\n"
        "ℹ️ Команды: /setnick Ник, /help",
    )


async def handle_night_chat_message(
    vk: VKAPI, game: Game, user_id: int, text: str
) -> None:
    p = game.players.get(user_id)
    if not p or not p.alive:
        return
    if p.role in MAFIA_SIDE:
        await game.mafia_chat_message(p, text)
    elif p.role == Role.COMMISSAR:
        await game.commissar_chat_message(p, text)


async def handle_message_event(vk: VKAPI, obj: dict) -> None:
    user_id = obj.get("user_id")
    peer_id = obj.get("peer_id")
    event_id = obj.get("event_id")
    payload = obj.get("payload") or {}
    action = payload.get("t")
    if user_id is None or peer_id is None or action is None:
        return

    game = manager.game_for_user(user_id)

    if action == "join":
        chat_game = manager.game_for_chat(peer_id)
        await event_join(vk, chat_game, peer_id, user_id, event_id)
        return

    if not game:
        await vk.answer_event(event_id, user_id, peer_id, "ℹ️ Нет активной игры.")
        return

    if action == "cmode":
        mode = payload.get("m")
        ok = mode in {"check", "shoot"} and game.check_mode(user_id, mode)
        await vk.answer_event(event_id, user_id, peer_id, "✅ Действие выбрано" if ok else "⛔ Нельзя сейчас")
        if ok:
            conversation_message_id = obj.get("conversation_message_id")
            message_id = obj.get("message_id")
            await game.submit_mode(
                user_id,
                mode,
                peer_id=peer_id,
                message_id=message_id,
                conversation_message_id=conversation_message_id,
            )
    elif action == "page":
        page = int(payload.get("p") or 0)
        sub = payload.get("a") or "target"
        await vk.answer_event(event_id, user_id, peer_id, f"📄 Стр. {page + 1}")
        conversation_message_id = obj.get("conversation_message_id")
        message_id = obj.get("message_id")
        await game.resend_prompt(
            user_id,
            sub,
            page,
            peer_id=peer_id,
            message_id=message_id,
            conversation_message_id=conversation_message_id,
        )
    elif action == "target":
        target_uid = payload.get("u")
        if target_uid is None:
            return
        target_uid = int(target_uid)
        if (
            target_uid == user_id
            and game.night.awaiting_target.get(user_id) == Role.LAWYER
        ):
            await vk.answer_event(event_id, user_id, peer_id, "⚖️ Себя нельзя защитить")
            return
        ok = game.check_target(user_id, target_uid)
        await vk.answer_event(event_id, user_id, peer_id, "✅ Выбор сделан" if ok else "⛔ Нельзя сейчас")
        if ok:
            await game.submit_target(user_id, target_uid)
            await _confirm_event_message(vk, obj, f"✅ Ты выбрал: {_player_link(game, target_uid)}")
    elif action == "skip":
        ok = game.check_skip(user_id)
        await vk.answer_event(event_id, user_id, peer_id, "✅ Ход пропущен" if ok else "⛔ Нельзя сейчас")
        if ok:
            await game.submit_skip(user_id)
            await _confirm_event_message(vk, obj, "✅ Ты пропустил ход")
    elif action == "vote":
        target_uid = payload.get("u")
        if target_uid is None:
            return
        ok = game.check_vote(user_id)
        await vk.answer_event(event_id, user_id, peer_id, "✅ Голос учтён" if ok else "⛔ Нельзя сейчас")
        if ok:
            await game.submit_vote(user_id, int(target_uid))
            await _confirm_event_message(vk, obj, f"✅ Твой голос: {_player_link(game, int(target_uid))}")
    elif action == "abstain":
        ok = game.check_vote(user_id)
        await vk.answer_event(event_id, user_id, peer_id, "✅ Воздержался" if ok else "⛔ Нельзя сейчас")
        if ok:
            await game.submit_vote(user_id, None)
            await _confirm_event_message(vk, obj, "✅ Ты воздержался")
    elif action == "confirm":
        vote = payload.get("v")
        if vote not in {"like", "dislike"}:
            return
        p = game.players.get(user_id)
        if p and not p.alive:
            await vk.answer_event(event_id, user_id, peer_id, "💀 Мертвым голос не давали")
            return
        if p and p.blocked_vote:
            await vk.answer_event(event_id, user_id, peer_id, "💋 Сегодня ты не можешь голосовать")
            return
        ok = await game.submit_confirm(user_id, vote)
        await vk.answer_event(event_id, user_id, peer_id, "✅ Засчитано" if ok else "⛔ Ты уже голосовал")


async def event_join(
    vk: VKAPI, game: Game | None, peer_id: int, user_id: int, event_id: int
) -> None:
    ban = manager.get_ban(user_id)
    if ban:
        if ban["until"] is None:
            msg = f"Вы были забанены в игре навсегда\nПричина: {ban['reason'] or '—'}"
        else:
            msg = f"Вы были забанены в игре на {ban['duration']}\nПричина: {ban['reason'] or '—'}"
        await vk.answer_event(event_id, user_id, peer_id, msg)
        return
    if not game or game.state != "waiting":
        await vk.answer_event(event_id, user_id, peer_id, "ℹ️ Игра уже началась или не найдена.")
        return
    if not await vk.is_dm_allowed(user_id):
        name = await display_name(vk, user_id)
        await vk.send(
            peer_id,
            f"⚠️ [id{user_id}|{name}], бот не может отправлять вам личные сообщения!\n"
            "Чтобы участвовать, напишите боту в личные сообщения или нажмите кнопку ниже:",
            keyboard=bot_dm_kb(),
        )
        await vk.answer_event(event_id, user_id, peer_id, "⚠️ Бот не может писать тебе в ЛС")
        return
    if user_id in game.players:
        await vk.answer_event(event_id, user_id, peer_id, "ℹ️ Ты уже в игре!")
        return
    await vk.answer_event(event_id, user_id, peer_id, "✅ Ты в игре!")
    name = await display_name(vk, user_id)
    await manager.register(user_id)
    if game.add_player(user_id, name):
        chat_title = await vk.get_chat_title(peer_id) or "этом чате"
        await vk.send(
            user_id,
            f"✅ Ты присоединился к игре Мафия, в чате {chat_title}",
            keyboard=bot_dm_kb(),
        )
        await game.broadcast(f"➕ [id{user_id}|{name}] присоединился к игре.")
        await game.update_registration_message()
    if len(game.players) >= config.MAX_PLAYERS:
        await game.start_game()


HELP_TEXT = (
    "🎭 Бот Мафия\n\n"
    "• /start — начать регистрацию в чате\n"
    "• /setnick Ник — сменить никнейм (в ЛС)\n"
    "• /rep текст — отправить жалобу разработчику (можно ответом на сообщение)\n"
    "• /startadmin — принудительно начать игру (админ)\n"
    "• /startbot — заполнить места ботами и начать (админ, для теста)\n"
    "• /stopadmin — завершить игру (админ)\n\n"
    "Бот должен быть администратором чата, чтобы видеть сообщения."
)
