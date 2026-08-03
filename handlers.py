from __future__ import annotations

import asyncio
import logging

import config
from game import Game
from game_manager import manager
from keyboards import bot_dm_kb, join_kb
from roles import MAFIA_SIDE, Role
from vk_api import VKAPI

logger = logging.getLogger(__name__)

CHAT_PREFIX = 2_000_000_000


def chat_peer_to_id(peer_id: int) -> int:
    return peer_id - CHAT_PREFIX


def _player_link(game: Game, uid: int) -> str:
    p = game.players.get(uid)
    return f"[id{uid}|{p.name}]" if p else "—"


async def _confirm_event_message(vk: VKAPI, obj: dict, text: str) -> None:
    peer_id = obj.get("peer_id")
    msg_id = obj.get("conversation_message_id")
    if not peer_id or not msg_id:
        logger.warning("message_event without peer_id/conversation_message_id: %s", obj)
        return
    try:
        await vk.edit(peer_id, msg_id, text, keyboard="")
    except Exception:  # noqa: BLE001
        logger.warning("edit event message failed", exc_info=True)


async def display_name(vk: VKAPI, user_id: int) -> str:
    base = await vk.get_user_name(user_id)
    return manager.nickname(user_id, base)


async def handle_message_new(vk: VKAPI, obj: dict) -> None:
    msg = obj.get("message") or {}
    peer_id = msg.get("peer_id")
    from_id = msg.get("from_id")
    text = (msg.get("text") or "").strip()
    if peer_id is None or from_id is None:
        return

    is_chat = peer_id >= CHAT_PREFIX
    if is_chat:
        game = manager.game_for_chat(peer_id)
        if text.startswith("/"):
            await handle_chat_command(vk, peer_id, from_id, text)
        elif game and game.state == "night":
            await handle_night_chat_message(vk, game, from_id, text)
        return

    if text.startswith("/"):
        await handle_private_command(vk, from_id, text)
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


async def handle_chat_command(vk: VKAPI, peer_id: int, user_id: int, text: str) -> None:
    parts = text.split()
    cmd = parts[0].lower()
    game = manager.game_for_chat(peer_id)
    if cmd in {"/start", "/новость", "/начать"} and len(parts) == 1:
        await cmd_start_group(vk, peer_id, user_id, game)
    elif cmd == "/startadmin":
        await cmd_startadmin(vk, peer_id, user_id, game)
    elif cmd == "/startbot":
        await cmd_startbot(vk, peer_id, user_id, game)
    elif cmd == "/stopadmin":
        await cmd_stopadmin(vk, peer_id, user_id, game)
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


async def handle_private_command(vk: VKAPI, user_id: int, text: str) -> None:
    parts = text.split()
    cmd = parts[0].lower()
    if cmd == "/setnick":
        nick = " ".join(parts[1:]).strip()
        if not nick:
            await vk.send(user_id, "ℹ️ Использование: /setnick ТвойНик")
            return
        manager.set_nickname(user_id, nick)
        game = manager.game_for_user(user_id)
        if game and user_id in game.players:
            game.players[user_id].name = nick
        await vk.send(user_id, f"✅ Никнейм изменён: {nick}")
    elif cmd == "/help":
        await vk.send(user_id, HELP_TEXT)
    else:
        await handle_private_text(vk, user_id)


async def handle_private_text(vk: VKAPI, user_id: int) -> None:
    manager.register(user_id)
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
            await game.submit_mode(user_id, mode)
            text = "✅ Режим: 🔍 Проверить роль" if mode == "check" else "✅ Режим: 🔫 Выстрелить"
            await _confirm_event_message(vk, obj, text)
    elif action == "page":
        page = int(payload.get("p") or 0)
        sub = payload.get("a") or "target"
        await vk.answer_event(event_id, user_id, peer_id, f"📄 Стр. {page + 1}")
        await game.resend_prompt(user_id, sub, page)
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
    manager.register(user_id)
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
    "• /startadmin — принудительно начать игру (админ)\n"
    "• /startbot — заполнить места ботами и начать (админ, для теста)\n"
    "• /stopadmin — завершить игру (админ)\n\n"
    "Бот должен быть администратором чата, чтобы видеть сообщения."
)
