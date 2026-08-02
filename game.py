from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter

import config
from keyboards import commissar_mode_kb, join_kb, lynch_confirm_kb, players_kb
from models import Action, NightData, Player
from roles import MAFIA_SIDE, ROLE_EMOJI, ROLE_RU, Role, roles_for_count
from vk_api import VKAPI

logger = logging.getLogger(__name__)

CHAT_ACTIONS = {
    Role.DOCTOR: ("🩺 Доктор вышел на ночное дежурство.", "Доктор сегодня остается дома."),
    Role.COMMISSAR: ("🕵️ Коммисар ушел искать злодеев.", "Коммисар сегодня спит."),
    Role.LAWYER: ("⚖️ Адвокат ушел искать мафию для защиты.", "Адвокат не хочет работать."),
    Role.MISTRESS: ("💋 Любовница уже ждет кого-то в гости.", "Любовница завязала со своим прошлым."),
    Role.DON: ("🗡️ Мафия выбрала жертву.", "Дон не хочет ничего решать."),
}

SHOOT_MESSAGE = "🔫 Коммисар зарядил пистолет."


def _cancel(task: asyncio.Task | None) -> None:
    if task and task is not asyncio.current_task():
        task.cancel()


NIGHT_ORDER = [
    Role.DOCTOR,
    Role.COMMISSAR,
    Role.LAWYER,
    Role.MISTRESS,
    Role.MAFIA,
    Role.DON,
]


class Game:
    def __init__(self, chat_id: int, bot: VKAPI):
        self.chat_id = chat_id
        self.bot = bot
        self.players: dict[int, Player] = {}
        self.state = "waiting"  # waiting | night | voting | ended
        self.registration_message_id: int | None = None
        self.night_number = 0
        self.night = NightData()
        self.votes: dict[int, int | None] = {}
        self.day_open = False
        self.confirm_target_uid: int | None = None
        self.confirm_message_id: int | None = None
        self.confirm_likes: set[int] = set()
        self.confirm_dislikes: set[int] = set()
        self._confirm_task: asyncio.Task | None = None
        self.mistress_visit: int | None = None
        self.last_mistress_visit: int | None = None
        self._night_timer: asyncio.Task | None = None
        self._vote_timer: asyncio.Task | None = None
        self._reg_timer: asyncio.Task | None = None
        self._bots_task: asyncio.Task | None = None
        self._bot_ids: set[int] = set()
        self._night_groups: list[tuple[Role, list[int]]] = []
        self._night_group_i = 0
        self.last_words_open: set[int] = set()

    # ------------------------------------------------------------------ utils
    @property
    def alive_players(self) -> list[Player]:
        return sorted(
            (p for p in self.players.values() if p.alive), key=lambda x: x.number
        )

    def _p(self, uid: int | None) -> Player | None:
        return self.players.get(uid) if uid is not None else None

    async def say(self, chat_id: int, text: str, keyboard=None) -> int | None:
        if chat_id in self._bot_ids:
            return None
        return await self.bot.send(chat_id, text, keyboard=keyboard)

    async def broadcast(self, text: str, **kw) -> int | None:
        return await self.say(self.chat_id, text, **kw)

    @staticmethod
    def _link(player: Player) -> str:
        return f"[id{player.user_id}|{player.name}]"

    def _alive_role_counts(self) -> str:
        cnt = Counter(p.role for p in self.alive_players)
        parts = []
        for role in (
            Role.DON, Role.MAFIA, Role.LAWYER, Role.COMMISSAR, Role.SERGEANT,
            Role.DOCTOR, Role.MISTRESS, Role.KAMIKAZE, Role.CITIZEN,
        ):
            if cnt.get(role):
                parts.append(
                    f"{ROLE_EMOJI[role]} {ROLE_RU[role]}"
                    if cnt[role] == 1
                    else f"{ROLE_EMOJI[role]} {cnt[role]} {ROLE_RU[role].lower()}"
                )
        return ", ".join(parts) or "никого"

    def alive_commissar_uid(self) -> int | None:
        for p in self.players.values():
            if p.alive and p.role == Role.COMMISSAR:
                return p.user_id
        return None

    def mafia_allies(self) -> list[Player]:
        return [p for p in self.alive_players if p.role in MAFIA_SIDE]

    # ------------------------------------------------------------ registration
    async def update_registration_message(self) -> None:
        if not self.registration_message_id:
            return
        lines = [
            "🎭 Регистрация в игру Мафия!",
            "",
            f"Участников: {len(self.players)}/{MAX_PLAYERS_STR} (минимум {MIN_PLAYERS_STR})",
            "⏱️ Регистрация идёт или игра начнётся сразу при заполнении.",
            "",
        ]
        for p in sorted(self.players.values(), key=lambda x: x.number):
            lines.append(f"{p.number}. {self._link(p)}")
        text = "\n".join(lines)
        try:
            await self.bot.edit(self.chat_id, self.registration_message_id, text, keyboard=join_kb())
        except Exception as e:  # noqa: BLE001
            logger.warning("edit reg message failed: %s", e)

    def add_player(self, user_id: int, name: str) -> bool:
        if user_id in self.players:
            return False
        self.players[user_id] = Player(user_id=user_id, name=name, number=len(self.players) + 1)
        return True

    def fill_with_bots(self, target: int | None = None) -> int:
        target = target or MAX_PLAYERS
        added = 0
        i = 1
        while len(self.players) < target:
            uid = -1000 - i
            i += 1
            self._bot_ids.add(uid)
            self.players[uid] = Player(
                user_id=uid,
                name=f"Бот {len(self.players) + 1}",
                number=len(self.players) + 1,
                is_bot=True,
            )
            added += 1
        return added

    @property
    def bot_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_bot]

    async def start_registration_timer(self) -> None:
        await asyncio.sleep(REGISTRATION_SECONDS)
        if self.state != "waiting":
            return
        if len(self.players) >= MIN_PLAYERS:
            await self.start_game()
        else:
            await self.broadcast(
                f"⏱️ Время регистрации вышло. Нужно минимум {MIN_PLAYERS} игроков. Игра отменена."
            )
            self.state = "ended"

    # ------------------------------------------------------------------ start
    async def start_game(self) -> None:
        if self.state != "waiting":
            return
        if self._reg_timer:
            _cancel(self._reg_timer)
        count = len(self.players)
        if count < MIN_PLAYERS:
            await self.broadcast(f"⚠️ Нужно минимум {MIN_PLAYERS} игроков.")
            return
        conf = roles_for_count(count)
        pool = []
        for role, n in sorted(conf.items()):
            pool.extend([role] * n)
        random.shuffle(pool)
        for p, role in zip(sorted(self.players.values(), key=lambda x: x.number), pool):
            p.role = role
        self.state = "night"
        await self.broadcast(
            f"🎭 Игра начинается! Участников: {count}\nВсем роли отправлены в личные сообщения."
        )
        for p in sorted(self.players.values(), key=lambda x: x.number):
            await self.send_role_intro(p)
        await self.start_night()

    # ------------------------------------------------------------- role intros
    async def send_role_intro(self, player: Player) -> None:
        role = player.role
        assert role is not None
        emoji = ROLE_EMOJI[role]
        if role == Role.DON:
            allies = ", ".join(
                f"{ROLE_EMOJI[a.role]} {self._link(a)} — {ROLE_RU[a.role]}"
                for a in self.mafia_allies() if a.user_id != player.user_id
            )
            text = (
                f"{emoji} Ты Дон — Глава мафии!\n"
                "Ночью ты вместе со всей мафиозной семьёй выбираешь жертву и исполняешь приговор.\n"
                "Вся семья ждёт твоего слова. Решай, кто не доживёт до утра!\n\n"
                f"Союзники:\n{allies or 'нет'}\n\n"
                "🗣️ Общаться с союзниками можно прямо в этом чате во время выбора жертвы."
            )
        elif role == Role.MAFIA:
            allies = ", ".join(
                f"{ROLE_EMOJI[a.role]} {self._link(a)} — {ROLE_RU[a.role]}"
                for a in self.mafia_allies() if a.user_id != player.user_id
            )
            text = (
                f"{emoji} Ты Мафия — член мафиозной семьи!\n"
                "Ночью вместе с семьёй выбираешь жертву. Если дона убьют — место босса может стать твоим.\n"
                "Покажи, на что способен. Город будет молить о пощаде!\n\n"
                f"Союзники:\n{allies or 'нет'}\n\n"
                "🗣️ Общаться с союзниками можно прямо в этом чате во время выбора жертвы."
            )
        elif role == Role.COMMISSAR:
            sergeants = [p for p in self.alive_players if p.role == Role.SERGEANT]
            text = (
                f"{emoji} Ты Коммисар — страж порядка!\n"
                "Ночью ты можешь узнать роль любого игрока или сделать свой выстрел — только одно действие за ночь.\n"
                "Найди мафию и вычисти город от грязи. Закон на твоей стороне!\n\n"
            )
            if sergeants:
                text += (
                    "Запомни своих союзников:\n"
                    + "\n".join(
                        f"🫂 {self._link(s)} — {ROLE_EMOJI[Role.SERGEANT]} Сержант" for s in sergeants
                    )
                    + "\n\nЯ буду сообщать сержанту о твоих проверках.\n"
                )
            text += "Если коммисар погибнет — сержант займёт его место."
        elif role == Role.SERGEANT:
            text = (
                f"{emoji} Ты Сержант — помощник комиссара!\n"
                "Ты узнаёшь обо всех проверках коммисара. А если он погибнет — повышаешься по службе "
                "и занимаешь его место.\n"
                "Держись рядом с законом. Твоё время придёт!"
            )
        elif role == Role.DOCTOR:
            text = (
                f"{emoji} Ты Доктор — работник реанимации!\n"
                "Ночью можешь приехать к любому игроку и спасти ему жизнь. Один раз за игру можешь спасти себя.\n"
                "Спасай жизни — каждый житель города на счету!"
            )
        elif role == Role.MISTRESS:
            text = (
                f"{emoji} Ты Любовница!\n"
                "Ночью нейтрализуй одного человека. Переспишь с доном — мафия никого не убьёт. "
                "С коммисаром — он не сможет проверить и выстрелить.\n"
                "Твой гость не сможет голосовать днём. Твоя красота — твоё оружие!"
            )
        elif role == Role.LAWYER:
            text = (
                f"{emoji} Ты Адвокат!\n"
                "Ночью выбери подзащитного — если коммисар проверит его, он увидит "
                f"«{ROLE_EMOJI[Role.CITIZEN]} Мирный житель» — даже если это не так.\n"
                "Ты играешь за город и не знаешь, кто мафия. Выбирай подзащитного с умом!"
            )
        elif role == Role.KAMIKAZE:
            text = (
                f"{emoji} Ты Камикадзе!\n"
                "Когда тебя убивают ночью, ты взрываешься и забираешь убийцу с собой.\n"
                "Умри достойно — враг упадёт вместе с тобой!"
            )
        else:
            text = (
                f"{emoji} Ты Мирный житель!\n"
                "Ночью ты спишь, а днём ищешь мафию и линчуешь её голосованием.\n"
                "Защити свой город. Каждый твой голос — твой выстрел!"
            )
        await self.say(player.user_id, text)

    # ------------------------------------------------------------------ night
    async def start_night(self) -> None:
        self.state = "night"
        self.night = NightData()
        self.last_mistress_visit = self.mistress_visit
        self.mistress_visit = None
        self.night_number += 1

        alive = self.alive_players
        lines = [
            f"🌙 Ночь {self.night_number}. Город засыпает.",
            f"Живых: {len(alive)}",
        ]
        for p in alive:
            lines.append(f"{p.number}. {self._link(p)}")
        lines.append("")
        lines.append(f"Среди них: {self._alive_role_counts()}")
        await self.broadcast("\n".join(lines))

        self._night_groups = self._build_night_groups()
        self._night_group_i = 0
        if not self._night_groups:
            await self.resolve_night()
            return
        await self._prompt_night_group(0)

    def _build_night_groups(self) -> list[tuple[Role, list[int]]]:
        com_uid = self.alive_commissar_uid()
        groups: list[tuple[Role, list[int]]] = []
        for role in NIGHT_ORDER:
            members = []
            for p in self.alive_players:
                eff = Role.COMMISSAR if p.role == Role.SERGEANT else p.role
                if eff != role:
                    continue
                if p.role in (Role.CITIZEN, Role.KAMIKAZE):
                    continue
                if role == Role.COMMISSAR and p.role == Role.SERGEANT and com_uid is not None:
                    continue
                members.append(p.user_id)
            if members:
                groups.append((role, members))
        return groups

    async def _prompt_night_group(self, i: int) -> None:
        role, uids = self._night_groups[i]
        for uid in uids:
            self.night.needed.add(uid)
            await self.send_night_prompt(self.players[uid], role)
        self._night_timer = asyncio.create_task(self._night_group_timeout(i))
        bots = [p for p in self.bot_players if p.alive and p.user_id in set(uids)]
        if bots:
            self._bots_task = asyncio.create_task(self._run_bots_phase(i))

    async def send_night_prompt(self, player: Player, role: Role, page: int = 0) -> None:
        alive = self.alive_players
        if role == Role.COMMISSAR:
            self.night.awaiting_target[player.user_id] = Role.COMMISSAR
            text = "🕵️ Ты коммисар!\nЧто делаем этой ночью?"
            await self.say(player.user_id, text, keyboard=commissar_mode_kb())
            return
        if role == Role.DOCTOR:
            exclude = {player.user_id} if player.self_healed else set()
            kb = players_kb(
                alive, exclude=exclude, skip_label=CHAT_ACTIONS[Role.DOCTOR][1], page=page
            )
            text = "🩺 Ты доктор!\nКого будем сегодня лечить?"
        elif role == Role.MISTRESS:
            exclude = {player.user_id}
            if self.last_mistress_visit:
                exclude.add(self.last_mistress_visit)
            kb = players_kb(
                alive, exclude=exclude, skip_label=CHAT_ACTIONS[Role.MISTRESS][1], page=page
            )
            text = "💋 Ты любовница!\nС кем провести эту ночь?"
        elif role == Role.LAWYER:
            kb = players_kb(
                alive, skip_label=CHAT_ACTIONS[Role.LAWYER][1], page=page
            )
            text = "⚖️ Ты адвокат!\nКого защитим от проверки коммисара?"
        elif role in (Role.DON, Role.MAFIA):
            exclude = {player.user_id}
            for q in alive:
                if q.user_id != player.user_id and q.role in (Role.DON, Role.MAFIA):
                    exclude.add(q.user_id)
            kb = players_kb(
                alive,
                exclude=exclude,
                skip_label=CHAT_ACTIONS[Role.DON][1] if role == Role.DON else "Мафия пропускает ход",
                page=page,
            )
            if role == Role.DON:
                text = "🗡️ Ты дон!\nКого убираем сегодня ночью?"
            else:
                text = "🗡️ Ты мафия!\nЗа кого голосуешь?"
        else:
            return
        self.night.awaiting_target[player.user_id] = role
        await self.say(player.user_id, text, keyboard=kb)

    async def _night_group_timeout(self, i: int) -> None:
        await asyncio.sleep(NIGHT_SECONDS)
        if self.state != "night":
            return
        if self._night_group_i != i:
            return
        group = self._night_groups[i]
        skipped = [uid for uid in group[1] if uid not in self.night.actions]
        for uid in skipped:
            await self.force_skip(uid)
        await self._advance_night_group()

    async def force_skip(self, uid: int) -> None:
        role = self.night.awaiting_target.pop(uid, None)
        if role is None and uid in self.night.needed:
            role = self._role_of_uid(uid)
        if role is not None and uid not in self.night.actions:
            self.night.actions[uid] = Action(role=role, target=None)
            await self._broadcast_action(uid, role, None)
            p = self.players.get(uid)
            if p and not p.is_bot:
                await self.say(uid, "⏰ Время вышло — ты пропустил ход.")

    def _role_of_uid(self, uid: int) -> Role | None:
        p = self.players.get(uid)
        if p is None or p.role is None:
            return None
        if p.role == Role.SERGEANT:
            return Role.COMMISSAR
        return p.role

    # -------------------------------------------------- fast checks (no side effects)
    def check_target(self, uid: int, target_uid: int) -> bool:
        if self.state != "night":
            return False
        role = self.night.awaiting_target.get(uid)
        if role is None:
            return False
        target = self._p(target_uid)
        if target is None or not target.alive or (target_uid == uid and role != Role.DOCTOR):
            return False
        if role in (Role.DON, Role.MAFIA) and target.role in (Role.DON, Role.MAFIA):
            return False
        if role == Role.MISTRESS and target_uid == self.last_mistress_visit:
            return False
        if role == Role.DOCTOR and target_uid == uid and self.players[uid].self_healed:
            return False
        return True

    def check_skip(self, uid: int) -> bool:
        return self.state == "night" and uid in self.night.awaiting_target

    def check_mode(self, uid: int, mode: str) -> bool:
        return (
            self.state == "night"
            and self.night.awaiting_target.get(uid) == Role.COMMISSAR
            and mode in {"check", "shoot"}
        )

    def check_vote(self, uid: int) -> bool:
        if self.state != "voting" or not self.day_open:
            return False
        p = self._p(uid)
        return bool(p and p.alive and not p.blocked_vote and uid not in self.votes)

    async def submit_mode(self, uid: int, mode: str) -> bool:
        if not self.check_mode(uid, mode):
            return False
        self.night.commissar_mode[uid] = mode
        text = "🕵️ Кого проверяем?" if mode == "check" else "🔫 В кого стреляем?"
        kb = players_kb(self.alive_players, exclude={uid})
        await self.say(uid, text, keyboard=kb)
        return True

    async def resend_prompt(self, uid: int, action: str, page: int = 0) -> bool:
        if self.state == "night" and uid in self.night.awaiting_target:
            role = self.night.awaiting_target[uid]
            if role == Role.COMMISSAR and uid in self.night.commissar_mode:
                mode = self.night.commissar_mode[uid]
                text = "🕵️ Кого проверяем?" if mode == "check" else "🔫 В кого стреляем?"
                kb = players_kb(self.alive_players, exclude={uid}, page=page)
                await self.say(uid, text, keyboard=kb)
                return True
            await self.send_night_prompt(self.players[uid], role, page=page)
            return True
        if self.state == "voting":
            p = self._p(uid)
            if p and p.alive and not p.blocked_vote and uid not in self.votes:
                await self.send_vote_prompt(p, page=page)
                return True
        return False

    async def submit_target(self, uid: int, target_uid: int) -> bool:
        if not self.check_target(uid, target_uid):
            return False
        role = self.night.awaiting_target[uid]
        if role == Role.DOCTOR and target_uid == uid:
            self.players[uid].self_healed = True
        self.night.awaiting_target.pop(uid, None)
        mode = self.night.commissar_mode.pop(uid, None) if role == Role.COMMISSAR else None
        self.night.actions[uid] = Action(role=role, target=target_uid, mode=mode)
        await self._broadcast_action(uid, role, target_uid, mode)
        await self._maybe_advance_night_group()
        return True

    async def submit_skip(self, uid: int) -> bool:
        if not self.check_skip(uid):
            return False
        role = self.night.awaiting_target.pop(uid)
        self.night.actions[uid] = Action(role=role, target=None)
        await self._broadcast_action(uid, role, None)
        await self._maybe_advance_night_group()
        return True

    async def _broadcast_action(self, uid: int, role: Role, target_uid: int | None, mode: str | None = None) -> None:
        p = self._p(uid)
        who = self._link(p) if p else f"#{uid}"
        if role == Role.DON:
            await self.broadcast(CHAT_ACTIONS[Role.DON][0] if target_uid else CHAT_ACTIONS[Role.DON][1])
            if target_uid:
                t = self._p(target_uid)
                await self._mafia_chat(f"🗳️ Дон выбрал: {self._link(t) if t else '—'}")
        elif role == Role.MAFIA:
            if target_uid:
                t = self._p(target_uid)
                await self._mafia_chat(
                    f"🗳️ {who} (мафия) проголосовал: {self._link(t) if t else '—'}"
                )
            else:
                await self._mafia_chat(f"⏭️ {who} (мафия) пропускает ход.")
        elif role == Role.COMMISSAR:
            if target_uid:
                if mode == "shoot":
                    await self.broadcast(SHOOT_MESSAGE)
                else:
                    await self.broadcast(CHAT_ACTIONS[Role.COMMISSAR][0])
            else:
                await self.broadcast(CHAT_ACTIONS[Role.COMMISSAR][1])
        elif role in CHAT_ACTIONS:
            await self.broadcast(CHAT_ACTIONS[role][0] if target_uid else CHAT_ACTIONS[role][1])
        if role == Role.MISTRESS and target_uid:
            self.mistress_visit = target_uid

    async def _mafia_chat(self, text: str) -> None:
        for ally in self.mafia_allies():
            await self.say(ally.user_id, f"🗯️ {text}")

    async def mafia_chat_message(self, player: Player, text: str) -> None:
        for ally in self.mafia_allies():
            if ally.user_id != player.user_id:
                await self.say(ally.user_id, f"💬 {self._link(player)}: {text}")

    async def commissar_chat_message(self, player: Player, text: str) -> None:
        for p in self.alive_players:
            if p.role == Role.SERGEANT and p.user_id != player.user_id:
                await self.say(p.user_id, f"💬 {self._link(player)} (коммисар): {text}")

    async def submit_last_words(self, user_id: int, text: str) -> bool:
        if user_id not in self.last_words_open:
            return False
        text = text.strip()
        if not text:
            await self.say(user_id, "✍️ Напиши последние слова текстом.")
            return True
        p = self.players.get(user_id)
        self.last_words_open.discard(user_id)
        name = self._link(p) if p else f"[id{user_id}|{user_id}]"
        await self.broadcast(f"📢 Кто-то слышал, как {name} кричал перед смертью:\n{text}")
        await self.say(user_id, "✅ Твои последние слова переданы городу.")
        return True

    async def _maybe_advance_night_group(self) -> None:
        if self.state != "night":
            return
        if self._night_group_i >= len(self._night_groups):
            return
        group = self._night_groups[self._night_group_i]
        if all(uid in self.night.actions for uid in group[1]):
            await self._advance_night_group()

    async def _advance_night_group(self) -> None:
        if self.state != "night":
            return
        _cancel(self._night_timer)
        self._night_timer = None
        self._night_group_i += 1
        try:
            if self._night_group_i < len(self._night_groups):
                await self._prompt_night_group(self._night_group_i)
            else:
                await self.resolve_night()
        except Exception:  # noqa: BLE001
            logger.exception("advance night group failed; resolving night")
            try:
                await self.resolve_night()
            except Exception:  # noqa: BLE001
                logger.exception("resolve_night failed after group advance")
                await self.end_game("stop")

    # ------------------------------------------------------------------ bots
    async def _run_bots_phase(self, i: int | None = None) -> None:
        if self.state == "night" and i is not None:
            group = self._night_groups[i]
            members = set(group[1])
            bots = [p for p in self.bot_players if p.alive and p.user_id in members]
            await asyncio.gather(*(self._bot_night_solo(p) for p in bots))
        elif self.state == "voting":
            await asyncio.gather(
                *(self._bot_vote_solo(p) for p in self.bot_players if p.alive and not p.blocked_vote)
            )
        elif self.state == "confirm":
            await asyncio.gather(
                *(self._bot_confirm_solo(p) for p in self.bot_players if p.alive and not p.blocked_vote)
            )

    async def _bot_night_solo(self, player: Player) -> None:
        await asyncio.sleep(random.uniform(1.0, 2.5))
        if self.state != "night":
            return
        await self._bot_night_move(player.user_id)

    async def _bot_vote_solo(self, player: Player) -> None:
        await asyncio.sleep(random.uniform(1.0, 2.0))
        if self.state != "voting":
            return
        candidates = [q.user_id for q in self.alive_players if q.user_id != player.user_id]
        if candidates and random.random() < 0.85:
            await self.submit_vote(player.user_id, random.choice(candidates))
        else:
            await self.submit_vote(player.user_id, None)

    async def _bot_night_move(self, uid: int) -> None:
        role = self.night.awaiting_target.get(uid)
        if role is None:
            return
        if role == Role.COMMISSAR:
            mode = random.choice(["check", "shoot"])
            if not await self.submit_mode(uid, mode):
                return
            targets = [q.user_id for q in self.alive_players if q.user_id != uid]
            if targets:
                await self.submit_target(uid, random.choice(targets))
            return
        if role == Role.DOCTOR:
            targets = [
                q.user_id
                for q in self.alive_players
                if not (q.user_id == uid and q.self_healed)
            ]
            if targets and random.random() < 0.7:
                await self.submit_target(uid, random.choice(targets))
            else:
                await self.submit_skip(uid)
            return
        if role in (Role.DON, Role.MAFIA):
            targets = [
                q.user_id
                for q in self.alive_players
                if q.user_id != uid and q.role not in (Role.DON, Role.MAFIA)
            ]
            if targets:
                await self.submit_target(uid, random.choice(targets))
            else:
                await self.submit_skip(uid)
            return
        targets = [q.user_id for q in self.alive_players if q.user_id != uid]
        if role == Role.MISTRESS and self.last_mistress_visit:
            targets = [t for t in targets if t != self.last_mistress_visit]
        if targets and random.random() < 0.7:
            await self.submit_target(uid, random.choice(targets))
        else:
            await self.submit_skip(uid)

    # ------------------------------------------------------------- resolution
    async def resolve_night(self) -> None:
        if self.state != "night":
            return
        try:
            await asyncio.wait_for(self._resolve_night_inner(), timeout=PHASE_TIMEOUT)
        except asyncio.TimeoutError:
            logger.exception("resolve_night watchdog timed out; moving to day")
            try:
                await self._after_deaths(next_phase="day")
            except Exception:  # noqa: BLE001
                logger.exception("forced transition to day failed; ending game")
                await self.end_game("stop")
        except Exception:  # noqa: BLE001
            logger.exception("resolve_night failed; moving to day")
            try:
                await self._after_deaths(next_phase="day")
            except Exception:  # noqa: BLE001
                logger.exception("forced transition to day failed; ending game")
                await self.end_game("stop")

    async def _resolve_night_inner(self) -> None:
        self.state = "resolving"
        alive = self.alive_players
        act = self.night.actions
        don = next((p for p in alive if p.role == Role.DON), None)
        mafias = [p for p in alive if p.role == Role.MAFIA]
        commissar = next((p for p in alive if p.role == Role.COMMISSAR), None)
        doctor = next((p for p in alive if p.role == Role.DOCTOR), None)
        mistress = next((p for p in alive if p.role == Role.MISTRESS), None)

        don_act = act.get(don.user_id) if don else None
        com_act = act.get(commissar.user_id) if commissar else None
        doc_act = act.get(doctor.user_id) if doctor else None
        mis_act = act.get(mistress.user_id) if mistress else None

        mafia_votes = [a.target for m in mafias if (a := act.get(m.user_id)) and a.target]
        unanimous = None
        if mafias and len(mafia_votes) == len(mafias) and len(set(mafia_votes)) == 1:
            unanimous = mafia_votes[0]

        don_target = don_act.target if don_act else None
        mistress_blocks_don = bool(mis_act and mis_act.target == (don.user_id if don else None))
        mistress_blocks_com = bool(mis_act and mis_act.target == (commissar.user_id if commissar else None))

        kill_target = None
        if don and not mistress_blocks_don:
            if unanimous and unanimous != don_target:
                kill_target = unanimous
            else:
                kill_target = don_target
        elif don is None:
            kill_target = unanimous

        com_target = None
        if com_act and com_act.mode == "shoot" and com_act.target and not mistress_blocks_com:
            com_target = com_act.target
        com_check = None
        if com_act and com_act.mode == "check" and com_act.target and not mistress_blocks_com:
            com_check = com_act.target

        heal = doc_act.target if doc_act else None

        hits: dict[int, list[str]] = {}
        if kill_target:
            hits.setdefault(kill_target, []).append("mafia")
        if com_target:
            hits.setdefault(com_target, []).append("com")

        logger.info("resolve_night: night=%d hits=%s heal=%s", self.night_number, hits, heal)
        morning = []
        for uid, kinds in hits.items():
            p = self.players[uid]
            if heal == uid:
                morning.append(f"🛡️ {self._link(p)} был(а) в опасности, но доктор вылечил его!")
                await self.say(uid, "🛡️ Тебя убили, но доктор вылечил тебя!")
                continue
            p.alive = False
            self.last_words_open.add(uid)
            await self.say(uid, "💀 Тебя убили этой ночью!\nНапиши последние слова — город их услышит.")
            if p.role == Role.KAMIKAZE:
                msg = (
                    f"💥 {self._link(p)} сегодня убили, "
                    f"он был {ROLE_EMOJI[Role.KAMIKAZE]} {ROLE_RU[Role.KAMIKAZE]}"
                )
                guests = []
                for kind in kinds:
                    killer = don if kind == "mafia" else commissar
                    if killer is None:
                        continue
                    if killer.alive:
                        if heal == killer.user_id:
                            await self.say(
                                killer.user_id,
                                "🛡️ Камикадзе взорвался рядом с тобой, но доктор вылечил тебя!",
                            )
                            guests.append(
                                f"{ROLE_EMOJI[killer.role]} {ROLE_RU[killer.role]} (спасён доктором)"
                            )
                        else:
                            killer.alive = False
                            if killer.user_id not in hits:
                                self.last_words_open.add(killer.user_id)
                                await self.say(
                                    killer.user_id,
                                    "💥 Камикадзе взорвался и забрал тебя с собой!\nНапиши последние слова — город их услышит.",
                                )
                            guests.append(f"{ROLE_EMOJI[killer.role]} {ROLE_RU[killer.role]}")
                    else:
                        guests.append(f"{ROLE_EMOJI[killer.role]} {ROLE_RU[killer.role]}")
                if guests:
                    msg += "\nГоворят, у него в гостях был: " + ", ".join(guests)
                morning.append(msg)
            else:
                killer_roles = []
                if "mafia" in kinds:
                    killer_roles.append(f"{ROLE_EMOJI[Role.DON]} {ROLE_RU[Role.DON]}")
                if "com" in kinds:
                    killer_roles.append(f"{ROLE_EMOJI[Role.COMMISSAR]} {ROLE_RU[Role.COMMISSAR]}")
                killer_txt = (
                    f"\nГоворят, у него в гостях был: {', '.join(killer_roles)}"
                    if killer_roles else ""
                )
                morning.append(
                    f"💀 {self._link(p)} — сегодня убили, "
                    f"он был {ROLE_EMOJI[p.role]} {ROLE_RU[p.role]}{killer_txt}"
                )

        if self.mistress_visit:
            guest = self._p(self.mistress_visit)
            if guest and guest.alive:
                guest.blocked_vote = True
                await self.say(guest.user_id, "💋 К тебе этой ночью приходила любовница. Сегодня ты не можешь голосовать.")

        await asyncio.sleep(MORNING_DELAY)
        await self.broadcast("🌅 Наступило утро!\n\n" + ("\n\n".join(morning) if morning else "Ночь прошла спокойно — никто не погиб."))
        logger.info("resolve_night: morning broadcast sent, state=%s", self.state)

        if com_check:
            checked = self.players[com_check]
            shown_role = Role.CITIZEN
            lawyer = next((p for p in alive if p.role == Role.LAWYER), None)
            if lawyer and (la := act.get(lawyer.user_id)) and la.target == com_check:
                shown_role = Role.CITIZEN
            elif checked.role in MAFIA_SIDE:
                shown_role = Role.MAFIA
            res = f"🕵️ Коммисар проверил {self._link(checked)}: это {ROLE_EMOJI[shown_role]} {ROLE_RU[shown_role]}"
            if commissar:
                await self.say(commissar.user_id, res)
                for p in alive:
                    if p.role == Role.SERGEANT:
                        await self.say(p.user_id, f"🕵️ Сообщение от коммисара:\n{res}")
                        break

        logger.info("resolve_night: calling _after_deaths(day)")
        await self._after_deaths(next_phase="day")

    async def _after_deaths(self, next_phase: str = "day") -> None:
        try:
            await self._promote_sergeant()
            winner = self.endgame_status()
            logger.info("_after_deaths: next_phase=%s endgame=%s", next_phase, winner)
            if winner:
                await self.end_game(winner)
                return
            if next_phase == "night":
                await asyncio.wait_for(self.start_night(), timeout=PHASE_TIMEOUT)
            else:
                await asyncio.wait_for(self.start_day(), timeout=PHASE_TIMEOUT)
        except asyncio.TimeoutError:
            logger.exception("phase transition watchdog timed out; ending game")
            try:
                await self.end_game("stop")
            except Exception:  # noqa: BLE001
                logger.exception("end_game failed too")
        except Exception:  # noqa: BLE001
            logger.exception("phase transition failed; ending game")
            try:
                await self.end_game("stop")
            except Exception:  # noqa: BLE001
                logger.exception("end_game failed too")

    # --------------------------------------------------------------- day vote
    async def send_vote_prompt(self, player: Player, page: int = 0) -> None:
        kb = players_kb(
            self.alive_players,
            action="vote",
            exclude={player.user_id},
            include_skip=True,
            skip_label="🤐 Воздержаться",
            skip_data="abstain",
            page=page,
        )
        await self.say(player.user_id, "🗳️ Собрание!\nЗа кого будем голосовать? (одна минута)", keyboard=kb)

    async def start_day(self) -> None:
        self.state = "voting"
        self.votes = {}
        self.day_open = False
        await self.broadcast(
            f"☀️ Наступил день! Минута на обсуждение, потом начнётся голосование.\n"
            f"Живых: {len(self.alive_players)}"
        )
        await asyncio.sleep(DISCUSSION_SECONDS)
        if self.state != "voting":
            return
        self.day_open = True
        eligible = [p for p in self.alive_players if not p.blocked_vote]
        logger.info("start_day: sending vote prompts to %d eligible players", len(eligible))
        for p in eligible:
            await self.send_vote_prompt(p)
        await self.broadcast(
            f"🗳️ Началось дневное голосование! Одна минута.\nЖивых: {len(self.alive_players)}"
        )
        logger.info("start_day: vote broadcast sent, timers started")
        self._vote_timer = asyncio.create_task(self._vote_timeout())
        if self.bot_players:
            self._bots_task = asyncio.create_task(self._run_bots_phase())

    async def submit_vote(self, uid: int, target: int | None) -> bool:
        if not self.check_vote(uid):
            return False
        p = self._p(uid)
        self.votes[uid] = target
        await self.broadcast(f"🗳️ {self._link(p)} — отдал свой голос.")
        await self.say(uid, "✅ Твой голос учтён.")
        eligible = [q for q in self.alive_players if not q.blocked_vote]
        if all(q.user_id in self.votes for q in eligible):
            await self.resolve_votes()
        return True

    async def _vote_timeout(self) -> None:
        await asyncio.sleep(VOTE_SECONDS)
        if self.state == "voting":
            await self.resolve_votes()

    async def resolve_votes(self) -> None:
        if self.state != "voting":
            return
        _cancel(self._vote_timer)
        self._vote_timer = None
        try:
            await asyncio.wait_for(self._resolve_votes_inner(), timeout=PHASE_TIMEOUT)
        except asyncio.TimeoutError:
            logger.exception("resolve_votes watchdog timed out; moving to night")
            try:
                await self._after_deaths(next_phase="night")
            except Exception:  # noqa: BLE001
                logger.exception("forced transition to night failed; ending game")
                await self.end_game("stop")
        except Exception:  # noqa: BLE001
            logger.exception("resolve_votes failed; moving to night")
            try:
                await self._after_deaths(next_phase="night")
            except Exception:  # noqa: BLE001
                logger.exception("forced transition to night failed; ending game")
                await self.end_game("stop")

    async def _resolve_votes_inner(self) -> None:
        self.state = "resolving"
        counts = Counter(v for v in self.votes.values() if v is not None)
        if counts:
            top = max(counts.values())
            winners = [uid for uid, c in counts.items() if c == top]
        else:
            top, winners = 0, []
        if len(winners) == 1:
            target = self.players[winners[0]]
            await self._open_lynch_confirm(target)
        else:
            await self.broadcast("⚖️ Голоса разделились — сегодня никого не повесили.")
            await self._after_deaths(next_phase="night")

    # ------------------------------------------------------------ lynching
    async def _open_lynch_confirm(self, target: Player) -> None:
        self.state = "confirm"
        self.confirm_target_uid = target.user_id
        self.confirm_likes = set()
        self.confirm_dislikes = set()
        self.confirm_message_id = None
        await self._render_lynch_confirm(send=True)
        self._confirm_task = asyncio.create_task(self._confirm_timeout())
        if self.bot_players:
            self._bots_task = asyncio.create_task(self._run_bots_phase())

    async def _render_lynch_confirm(self, send: bool = False) -> None:
        target = self.players[self.confirm_target_uid]
        likes = len(self.confirm_likes)
        dislikes = len(self.confirm_dislikes)
        text = (
            f"⚖️ Город вынес решение!\n"
            f"Вы уверены, что хотите повесить {self._link(target)}?\n"
            f"Голосуй: 👍 повесить или 👎 помиловать."
        )
        kb = lynch_confirm_kb(likes, dislikes)
        if send or not self.confirm_message_id:
            sent = await self.broadcast(text, keyboard=kb)
            self.confirm_message_id = sent
        else:
            await self.bot.edit(self.chat_id, self.confirm_message_id, text, keyboard=kb)

    def _confirm_ready(self) -> bool:
        eligible = [p for p in self.alive_players if not p.blocked_vote]
        if not eligible:
            return True
        return all(
            p.user_id in self.confirm_likes or p.user_id in self.confirm_dislikes
            for p in eligible
        )

    async def submit_confirm(self, uid: int, vote: str) -> bool:
        if self.state != "confirm":
            return False
        p = self._p(uid)
        if not p or not p.alive or p.blocked_vote:
            return False
        if uid in self.confirm_likes or uid in self.confirm_dislikes:
            return False
        if vote == "like":
            self.confirm_likes.add(uid)
        elif vote == "dislike":
            self.confirm_dislikes.add(uid)
        else:
            return False
        await self._render_lynch_confirm()
        if self._confirm_ready():
            await self._finalize_lynch_confirm()
        return True

    async def _confirm_timeout(self) -> None:
        await asyncio.sleep(CONFIRM_SECONDS)
        if self.state == "confirm":
            await self._finalize_lynch_confirm()

    async def _finalize_lynch_confirm(self) -> None:
        if self.state != "confirm":
            return
        _cancel(self._confirm_task)
        self._confirm_task = None
        self.state = "resolving"
        likes = len(self.confirm_likes)
        dislikes = len(self.confirm_dislikes)
        target = self.players[self.confirm_target_uid]
        result = f"Голосование завершено:\n👍 {likes} | 👎 {dislikes}"
        try:
            await self.bot.edit(self.chat_id, self.confirm_message_id, result, keyboard="")
        except Exception:  # noqa: BLE001
            logger.warning("edit confirm message failed", exc_info=True)
        await asyncio.sleep(LYNCH_DELAY)
        if likes > dislikes and likes > 0:
            target.alive = False
            self.last_words_open.add(target.user_id)
            await self.say(
                target.user_id,
                "⚖️ Город вынес тебе приговор!\nНапиши последние слова — город их услышит.",
            )
            await self.broadcast(
                f"Вешаем {self._link(target)} :)\n"
                f"Он был {ROLE_EMOJI[target.role]} {ROLE_RU[target.role]}"
            )
        else:
            await self.broadcast("Помилован — сегодня никого не повесили.")
        await asyncio.sleep(LYNCH_DELAY)
        await self._after_deaths(next_phase="night")

    async def _bot_confirm_solo(self, player: Player) -> None:
        await asyncio.sleep(random.uniform(1.0, 2.5))
        if self.state != "confirm":
            return
        await self.submit_confirm(player.user_id, random.choice(["like", "dislike"]))

    # ------------------------------------------------------------ promotions
    async def _promote_sergeant(self) -> None:
        if self.alive_commissar_uid() is None:
            for p in self.players.values():
                if p.alive and p.role == Role.SERGEANT:
                    p.role = Role.COMMISSAR
                    await self.say(
                        p.user_id,
                        f"{ROLE_EMOJI[Role.COMMISSAR]} Коммисар погиб. "
                        "Ты занимаешь его должность и становишься Коммисаром!",
                    )
                    break

    # -------------------------------------------------------------- endgame
    def endgame_status(self) -> str | None:
        alive = self.alive_players
        mafia = sum(1 for p in alive if p.role in MAFIA_SIDE)
        town = len(alive) - mafia
        if mafia == 0:
            return "town"
        if town <= 1:
            return "mafia"
        return None

    async def end_game(self, winner: str) -> None:
        self.state = "ended"
        for t in (self._night_timer, self._vote_timer, self._reg_timer, self._bots_task, self._confirm_task):
            _cancel(t)
        if winner == "mafia":
            win_line = f"🎉 {ROLE_EMOJI[Role.MAFIA]} Мафия победила!"
        elif winner == "town":
            win_line = f"🎉 {ROLE_EMOJI[Role.CITIZEN]} Город победил! Мирные жители выиграли!"
        else:
            win_line = "⏹️ Игра остановлена администратором."
        lines = [
            "🏁 Игра окончена!",
            win_line,
            "",
            "Роли:",
        ]
        for p in sorted(self.players.values(), key=lambda x: x.number):
            status = "жив" if p.alive else "мёртв"
            role_txt = f"{ROLE_EMOJI[p.role]} {ROLE_RU[p.role]}" if p.role else "—"
            lines.append(f"{p.number}. {self._link(p)} — {role_txt} ({status})")
        lines.append("")
        lines.append("🔄 Напиши /start, чтобы начать новую игру.")
        await self.broadcast("\n".join(lines))


MIN_PLAYERS = config.MIN_PLAYERS
MAX_PLAYERS = config.MAX_PLAYERS
NIGHT_SECONDS = config.NIGHT_SECONDS
DISCUSSION_SECONDS = config.DISCUSSION_SECONDS
VOTE_SECONDS = config.VOTE_SECONDS
CONFIRM_SECONDS = config.CONFIRM_SECONDS
MORNING_DELAY = config.MORNING_DELAY
LYNCH_DELAY = config.LYNCH_DELAY
REGISTRATION_SECONDS = config.REGISTRATION_SECONDS
MIN_PLAYERS_STR = str(MIN_PLAYERS)
MAX_PLAYERS_STR = str(MAX_PLAYERS)
PHASE_TIMEOUT = 240
