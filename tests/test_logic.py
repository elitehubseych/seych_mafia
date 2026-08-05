"""Проверка игровой логики бота Мафия.

Запуск:
    .venv\\Scripts\\python tests\\test_logic.py

Все проверки не требуют настоящего токена и сети — используется поддельный бот.
"""

import asyncio
import os
import random
import sys
from pathlib import Path

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

import game as game_mod
from game import Game, NIGHT_ORDER
from roles import MAFIA_SIDE, ROLE_CONFIG, Role


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.edited_keyboards = []

    async def send(self, chat_id, text, keyboard=None, **kw):
        self.sent.append((chat_id, text))
        return len(self.sent)

    async def edit(self, peer_id, message_id, text, keyboard=None, conversation_message_id=None):
        self.edited.append(text)
        self.edited_keyboards.append(keyboard)
        return True


def chat_msgs(g):
    return [t for cid, t in g.bot.sent if cid == g.chat_id]


def dm_msgs(g, uid):
    return [t for cid, t in g.bot.sent if cid == uid]


def make_game(roles):
    g = Game(chat_id=-1001, bot=FakeBot())
    for i, (uid, role) in enumerate(roles.items(), 1):
        g.add_player(uid, f"P{uid % 1000}")
        g.players[uid].role = role
        g.players[uid].number = i
    return g


async def night_flow(g, actions):
    await g.start_night()
    for role in NIGHT_ORDER:
        for uid in list(actions.keys()):
            p = g.players[uid]
            eff = Role.COMMISSAR if p.role == Role.SERGEANT else p.role
            if eff != role or not p.alive:
                continue
            if uid not in g.night.awaiting_target:
                continue
            spec = actions[uid]
            if spec[0] == "mode":
                await g.submit_mode(uid, spec[1])
                await g.submit_target(uid, spec[2])
            elif spec[0] == "target":
                await g.submit_target(uid, spec[1])
            else:
                await g.submit_skip(uid)


def test_role_configs():
    for n, conf in ROLE_CONFIG.items():
        assert sum(conf.values()) == n, (n, conf)
    print("test_role_configs OK")


async def test_kill_and_heal():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.KAMIKAZE,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1007),
        1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "check", 1001),
        1004: ("target", 1007),
        1005: ("skip",), 1006: ("skip",),
    })
    assert g.players[1007].alive, "доктор должен спасти P7 от убийства дона"
    assert "🛡️ Тебя убили, но доктор спас тебя!" in "\n".join(dm_msgs(g, 1007))
    assert "Коммисар проверил" in "\n".join(dm_msgs(g, 1003)), dm_msgs(g, 1003)
    assert "Мафия" in "\n".join(dm_msgs(g, 1003))
    print("test_kill_and_heal OK")


async def test_mistress_blocks_don():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1007),
        1001: ("skip",), 1002: ("skip",), 1003: ("skip",), 1004: ("skip",),
        1005: ("target", 1000), 1006: ("skip",),
    })
    assert g.players[1007].alive, "любовница у дона — мафия никого не убивает"
    print("test_mistress_blocks_don OK")


async def test_kamikaze():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.KAMIKAZE,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1008),  # дон убивает камикадзе
        1001: ("skip",), 1002: ("skip",), 1003: ("skip",), 1004: ("skip",),
        1005: ("skip",), 1006: ("skip",),
    })
    assert not g.players[1008].alive, "камикадзе погибает"
    assert not g.players[1000].alive, "дон должен взорваться вместе с камикадзе"
    m = "\n".join(chat_msgs(g))
    assert "Камикадзе" in m and "Дон" in m, m
    print("test_kamikaze OK")


async def test_kamikaze_multiple_attackers():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.MANIAC, 1007: Role.CITIZEN, 1008: Role.KAMIKAZE,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1008),
        1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "shoot", 1008),
        1004: ("target", 1003),
        1005: ("skip",),
        1006: ("target", 1008),
    })
    assert not g.players[1008].alive, "камикадзе погибает"
    assert not g.players[1000].alive, "дон должен взорваться вместе с камикадзе"
    assert not g.players[1006].alive, "маньяк должен погибнуть вместе с камикадзе"
    assert g.players[1003].alive, "коммисар должен выжить, доктор лечит его"
    assert "спасён доктором" in "\n".join(chat_msgs(g)), "в сообщении должно быть указано спасение комиссара"
    print("test_kamikaze_multiple_attackers OK")


async def test_kamikaze_mistress_blocks():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.KAMIKAZE, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1006),
        1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "shoot", 1006),
        1004: ("skip",), 1005: ("target", 1006),
    })
    assert not g.players[1006].alive, "камикадзе погибает"
    assert g.players[1000].alive, "дон не должен погибнуть, когда любовница рядом"
    assert g.players[1003].alive, "коммисар не должен погибнуть, когда любовница рядом"
    assert "он не смог забрать гостей" in "\n".join(chat_msgs(g)), "утреннее сообщение должно сообщать, что любовница мешает"
    print("test_kamikaze_mistress_blocks OK")


async def test_maniac_kills_target():
    g = make_game({
        1000: Role.MANIAC, 1001: Role.CITIZEN, 1002: Role.CITIZEN,
        1003: Role.MAFIA, 1004: Role.COMMISSAR, 1005: Role.DOCTOR,
        1006: Role.MISTRESS, 1007: Role.LAWYER, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await g.start_night()
    while g.state == "night":
        for uid in list(g.night.awaiting_target):
            role = g.night.awaiting_target[uid]
            if role == Role.MANIAC:
                await g.submit_target(uid, 1001)
            else:
                await g.submit_skip(uid)
    assert not g.players[1001].alive, "маньяк должен убить выбранную цель"
    assert g.players[1000].alive, "маньяк не должен погибнуть сразу"
    m = "\n".join(chat_msgs(g))
    assert "сегодня убили" in m, m
    print("test_maniac_kills_target OK")


async def test_maniac_system_message():
    g = make_game({
        1000: Role.MANIAC, 1001: Role.CITIZEN, 1002: Role.CITIZEN,
        1003: Role.DON, 1004: Role.COMMISSAR, 1005: Role.DOCTOR,
        1006: Role.MISTRESS, 1007: Role.LAWYER, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await g.start_night()
    while g.state == "night":
        for uid in list(g.night.awaiting_target):
            role = g.night.awaiting_target[uid]
            if role == Role.MANIAC:
                await g.submit_target(uid, 1001)
            elif role == Role.DON:
                await g.submit_target(uid, 1002)
            else:
                await g.submit_skip(uid)
    m = "\n".join(chat_msgs(g))
    assert "🔪 Маньяк засел в кустах." in m, m
    assert m.index("Маньяк засел в кустах") < m.index("Мафия выбрала жертву"), m
    print("test_maniac_system_message OK")


async def test_lawyer_protects_from_check():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",), 1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "check", 1000),
        1004: ("skip",), 1005: ("skip",),
        1006: ("target", 1000),
    })
    dm = "\n".join(dm_msgs(g, 1003))
    assert "Коммисар проверил" in dm and "Мирный житель" in dm, dm
    print("test_lawyer_protects_from_check OK")


async def test_mafia_unanimous_overrides_don():
    g = make_game({
        1000: Role.DON,
        1001: Role.MAFIA, 1002: Role.MAFIA, 1003: Role.MAFIA, 1004: Role.MAFIA,
        1005: Role.COMMISSAR, 1006: Role.DOCTOR, 1007: Role.MISTRESS, 1008: Role.LAWYER,
        1009: Role.CITIZEN, 1010: Role.CITIZEN, 1011: Role.CITIZEN, 1012: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1012),  # дон хочет 1012
        1001: ("target", 1009),  # мафия единогласно — 1009
        1002: ("target", 1009),
        1003: ("target", 1009),
        1004: ("target", 1009),
        1005: ("skip",), 1006: ("skip",), 1007: ("skip",), 1008: ("skip",),
    })
    assert not g.players[1012].alive, "дон выбирает жертву — его решение решает"
    assert g.players[1009].alive, "голоса мафии не перебивают выбор дона"
    print("test_mafia_unanimous_overrides_don OK")


async def test_mafia_three_unanimous_don_decides():
    g = make_game({
        1000: Role.DON,
        1001: Role.MAFIA, 1002: Role.MAFIA, 1003: Role.MAFIA,
        1005: Role.COMMISSAR, 1006: Role.DOCTOR, 1007: Role.MISTRESS, 1008: Role.LAWYER,
        1009: Role.CITIZEN, 1010: Role.CITIZEN, 1011: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1009),  # дон хочет 1009
        1001: ("target", 1010),  # мафия единогласно — 1010
        1002: ("target", 1010),
        1003: ("target", 1010),
        1005: ("skip",), 1006: ("skip",), 1007: ("skip",), 1008: ("skip",),
    })
    assert not g.players[1009].alive, "при 3 мафии решает дон"
    assert g.players[1010].alive, "единогласная мафия из 3 человек не перебивает дона"
    print("test_mafia_three_unanimous_don_decides OK")


async def test_mafia_split_don_decides():
    g = make_game({
        1000: Role.DON,
        1001: Role.MAFIA, 1002: Role.MAFIA, 1003: Role.MAFIA,
        1005: Role.COMMISSAR, 1006: Role.DOCTOR, 1007: Role.MISTRESS, 1008: Role.LAWYER,
        1009: Role.CITIZEN, 1010: Role.CITIZEN, 1011: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1009),  # дон хочет 1009
        1001: ("target", 1010),  # мафия кинула в разных
        1002: ("target", 1011),
        1003: ("skip",),
        1005: ("skip",), 1006: ("skip",), 1007: ("skip",), 1008: ("skip",),
    })
    assert not g.players[1009].alive, "при разнобое мафии решает дон"
    assert g.players[1010].alive and g.players[1011].alive, "выбор отдельных мафий не должен убивать"
    print("test_mafia_split_don_decides OK")


async def test_don_skip_mafia_unanimous_kills():
    g = make_game({
        1000: Role.DON,
        1001: Role.MAFIA, 1002: Role.MAFIA, 1003: Role.MAFIA, 1004: Role.MAFIA,
        1005: Role.COMMISSAR, 1006: Role.DOCTOR, 1007: Role.MISTRESS, 1008: Role.LAWYER,
        1009: Role.CITIZEN, 1010: Role.CITIZEN, 1011: Role.CITIZEN, 1012: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",),  # дон отказался
        1001: ("target", 1009),  # мафия единогласно — 1009
        1002: ("target", 1009),
        1003: ("target", 1009),
        1004: ("target", 1009),
        1005: ("skip",), 1006: ("skip",), 1007: ("skip",), 1008: ("skip",),
    })
    assert not g.players[1009].alive, "дон пропустил, но единогласная мафия убивает 1009"
    print("test_don_skip_mafia_unanimous_kills OK")


async def test_don_skip_mafia_split_no_kill():
    g = make_game({
        1000: Role.DON,
        1001: Role.MAFIA, 1002: Role.MAFIA,
        1005: Role.COMMISSAR, 1006: Role.DOCTOR, 1007: Role.MISTRESS, 1008: Role.LAWYER,
        1009: Role.CITIZEN, 1010: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",),  # дон отказался
        1001: ("target", 1009),  # мафия кинула в разных
        1002: ("target", 1010),
        1005: ("skip",), 1006: ("skip",), 1007: ("skip",), 1008: ("skip",),
    })
    assert g.players[1009].alive, "дон пропустил и мафия разошлась — никто не убит"
    assert g.players[1010].alive, "дон пропустил и мафия разошлась — никто не убит"
    m = "\n".join(chat_msgs(g))
    assert "Ночь прошла спокойно" in m, m
    print("test_don_skip_mafia_split_no_kill OK")


async def test_mafia_decision_messages():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",),  # дон не хочет ничего решать
        1001: ("target", 1007), 1002: ("target", 1008),  # мафия разошлась
        1003: ("skip",), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    m = "\n".join(chat_msgs(g))
    assert "🤵🏻 Дон не хочет участвовать." in m, m
    assert "🤵🏼 Мафия выбрала жертву" not in m, "без решения мафии сообщение о жертве не пишется"
    assert g.players[1007].alive and g.players[1008].alive

    g2 = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await night_flow(g2, {
        1000: ("target", 1009),  # дон выбрал 1009
        1001: ("target", 1007), 1002: ("target", 1007),  # мафия единогласно
        1003: ("skip",), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    m2 = "\n".join(chat_msgs(g2))
    assert "🤵🏼 Мафия выбрала жертву" in m2, m2
    assert "🗡️ Мафия выбрала жертву" not in m2, "эмодзи кинжала заменён на эмодзи человека"
    print("test_mafia_decision_messages OK")


async def test_don_transfer_to_random_mafia():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS, 1006: Role.LAWYER,
        1002: Role.CITIZEN, 1007: Role.CITIZEN, 1008: Role.CITIZEN, 1009: Role.CITIZEN,
    })
    g.players[1000].alive = False  # дон убит ночью
    await night_flow(g, {
        1001: ("target", 1007),  # бывшая мафия теперь дон
        1003: ("skip",), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    assert g.players[1001].role == Role.DON, "роль дона передаётся живому мафиози"
    assert not g.players[1007].alive, "решение нового дона определяет убийство"
    dm = "\n".join(dm_msgs(g, 1001))
    assert "теперь новый дон." in dm, dm
    m = "\n".join(chat_msgs(g))
    assert "теперь новый дон." not in m, "о передаче дона знает только мафия"
    print("test_don_transfer_to_random_mafia OK")


async def test_don_afk_single_mafia_choice_counts():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS, 1006: Role.LAWYER,
        1007: Role.CITIZEN, 1008: Role.CITIZEN, 1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",),  # дон афк
        1001: ("target", 1007),  # одна мафия выбрала
        1002: ("skip",),  # вторая промолчала
        1003: ("skip",), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    assert not g.players[1007].alive, "дон не выбрал — убивает по голосам мафии"
    m = "\n".join(chat_msgs(g))
    assert "🤵🏻 Дон не хочет участвовать." not in m, "убийство состоялось — сообщение не нужно"
    print("test_don_afk_single_mafia_choice_counts OK")


async def test_mafia_skip_triggers_don_message():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS, 1006: Role.LAWYER,
        1007: Role.CITIZEN, 1008: Role.CITIZEN, 1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",), 1001: ("skip",), 1002: ("skip",),
        1003: ("skip",), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    m = "\n".join(chat_msgs(g))
    assert "🤵🏻 Дон не хочет участвовать." in m, m
    assert "🤵🏼 Мафия выбрала жертву" not in m, "никто не убит — сообщения о жертве нет"
    print("test_mafia_skip_triggers_don_message OK")


async def test_don_and_one_mafia_don_decides():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS, 1006: Role.LAWYER,
        1002: Role.CITIZEN, 1007: Role.CITIZEN, 1008: Role.CITIZEN, 1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1007),  # дон хочет 1007
        1001: ("target", 1008),  # единственная мафия — 1008
        1003: ("skip",), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    assert not g.players[1007].alive, "дон и одна мафия — решает дон"
    assert g.players[1008].alive, "одна мафия не перебивает выбор дона"
    print("test_don_and_one_mafia_don_decides OK")


async def test_commissar_shoot():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",), 1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "shoot", 1001),
        1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    assert not g.players[1001].alive, "выстрел коммисара убивает мафию"
    print("test_commissar_shoot OK")


async def test_page_edits_same_message():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN,
    })
    await g.start_night()
    sent_before = len(g.bot.sent)
    edited_before = len(g.bot.edited)
    ok = await g.resend_prompt(1000, "target", page=1, peer_id=1000, message_id=1, conversation_message_id=1)
    assert ok is True, "перелистывание страниц в ночи"
    assert len(g.bot.edited) == edited_before + 1, "страница должна редактировать текущее сообщение"
    assert len(g.bot.sent) == sent_before, "не должно появляться новое сообщение"
    print("test_page_edits_same_message OK")


async def test_commissar_mode_edits_same_message():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN,
    })
    await g.start_night()
    sent_before = len(g.bot.sent)
    edited_before = len(g.bot.edited)
    ok = await g.submit_mode(1003, "check", peer_id=1003, message_id=2, conversation_message_id=2)
    assert ok is True, "режим коммисара"
    assert 1003 in g.night.commissar_mode, "режим запоминается"
    assert len(g.bot.edited) == edited_before + 1, "режим должен редактировать текущее сообщение"
    assert len(g.bot.sent) == sent_before, "не должно появляться новое сообщение"
    print("test_commissar_mode_edits_same_message OK")


async def test_guest_message_shows_don_when_don_decides():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1007),
        1001: ("skip",), 1002: ("skip",),
        1003: ("skip",), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    assert not g.players[1007].alive, "дон убивает выбранную жертву"
    guests = [l for l in "\n".join(chat_msgs(g)).splitlines() if "в гостях был" in l]
    assert any("🤵🏻 Дон" in l for l in guests), guests
    assert not any("🤵🏼 Мафия" in l for l in guests), guests
    print("test_guest_message_shows_don_when_don_decides OK")


async def test_guest_message_never_shows_mafia_when_don_skips():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",),
        1001: ("target", 1007), 1002: ("target", 1007),
        1003: ("skip",), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    assert not g.players[1007].alive, "при афк доне мафия решает большинством"
    guests = [l for l in "\n".join(chat_msgs(g)).splitlines() if "в гостях был" in l]
    assert any("🤵🏻 Дон" in l for l in guests), guests
    assert not any("Мафия" in l for l in guests), "в гостях никогда не пишется Мафия"
    print("test_guest_message_never_shows_mafia_when_don_skips OK")


async def test_don_afk_mafia_plurality_kills_most_voted():
    g = make_game({
        1000: Role.DON,
        1001: Role.MAFIA, 1002: Role.MAFIA, 1003: Role.MAFIA, 1004: Role.MAFIA,
        1005: Role.COMMISSAR, 1006: Role.DOCTOR, 1007: Role.MISTRESS, 1008: Role.LAWYER,
        1009: Role.CITIZEN, 1010: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",),  # дон афк
        1001: ("target", 1009),  # 1009 — 3 голоса
        1002: ("target", 1009),
        1003: ("target", 1009),
        1004: ("target", 1010),  # 1010 — 1 голос
        1005: ("skip",), 1006: ("skip",), 1007: ("skip",), 1008: ("skip",),
    })
    assert not g.players[1009].alive, "дон афк — убивает того, за кого больше голосов мафии"
    assert g.players[1010].alive, "меньшинство не убивает"
    guests = [l for l in "\n".join(chat_msgs(g)).splitlines() if "в гостях был" in l]
    assert any("🤵🏻 Дон" in l for l in guests), guests
    print("test_don_afk_mafia_plurality_kills_most_voted OK")


async def test_lynch_confirm_updates_counts():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",), 1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "check", 1001), 1004: ("skip",), 1005: ("skip",),
        1006: ("skip",),
    })
    assert g.state == "voting"
    for p in g.alive_players:
        await g.submit_vote(p.user_id, 1001)
    await g.resolve_votes()
    assert g.state == "confirm"
    await g.submit_confirm(1000, "like")
    assert any("Вы уверены" in t for t in g.bot.edited), g.bot.edited
    assert any("👍 (1)" in str(kb) for kb in g.bot.edited_keyboards), g.bot.edited_keyboards
    assert not any("Твой голос учтён" in t for t in g.bot.edited), g.bot.edited
    print("test_lynch_confirm_updates_counts OK")


async def test_day_lynch():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1009), 1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "check", 1001),
        1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    assert g.state == "voting"
    for p in g.alive_players:
        await g.submit_vote(p.user_id, 1001)
    await g.resolve_votes()
    assert g.state == "confirm", f"ожидалась фаза подтверждения, state={g.state}"
    for p in g.alive_players:
        await g.submit_confirm(p.user_id, "like")
    assert not g.players[1001].alive, "мафия повешена днём"
    assert g.state == "night", "после дня должна начаться следующая ночь"
    print("test_day_lynch OK")


async def test_1v1_endgame():
    g = Game(chat_id=-2, bot=FakeBot())
    g.add_player(1, "A")
    g.add_player(2, "B")
    g.players[1].role = Role.DON
    g.players[2].role = Role.CITIZEN
    assert g.endgame_status() == "mafia", g.endgame_status()
    g.players[2].role = Role.CITIZEN
    g.add_player(3, "C")
    g.players[3].role = Role.CITIZEN
    assert g.endgame_status() is None
    g.add_player(4, "D")
    g.players[4].role = Role.MAFIA
    assert g.endgame_status() is None, "2 мирных + 2 мафии — игра продолжается"
    g.add_player(5, "E")
    g.players[5].role = Role.KAMIKAZE
    g.players[3].alive = False
    assert g.endgame_status() is None, "камикадзе + мирный + 2 мафии — игра продолжается"
    g.players[2].alive = False
    assert g.endgame_status() == "mafia", "один мирный — победа мафии"
    print("test_1v1_endgame OK")


async def test_full_game():
    random.seed(7)
    g = Game(chat_id=-77, bot=FakeBot())
    roles = [Role.DON, Role.MAFIA, Role.COMMISSAR, Role.DOCTOR, Role.CITIZEN, Role.CITIZEN]
    random.shuffle(roles)
    for i, r in enumerate(roles, 1):
        g.add_player(5000 + i, f"P{i}")
        g.players[5000 + i].role = r
        g.players[5000 + i].number = i
    await g.start_night()

    async def night():
        alive = g.alive_players
        for role in NIGHT_ORDER:
            for p in alive:
                eff = Role.COMMISSAR if p.role == Role.SERGEANT else p.role
                if eff != role or p.user_id not in g.night.awaiting_target:
                    continue
                if p.role in (Role.CITIZEN, Role.KAMIKAZE):
                    continue
                if p.role == Role.DON:
                    targets = [q.user_id for q in alive if q.role not in MAFIA_SIDE]
                    if targets and random.random() < 0.9:
                        await g.submit_target(p.user_id, random.choice(targets))
                    else:
                        await g.submit_skip(p.user_id)
                elif p.role == Role.MAFIA:
                    await g.submit_skip(p.user_id)
                elif p.role == Role.COMMISSAR:
                    await g.submit_mode(p.user_id, "check")
                    await g.submit_target(p.user_id, random.choice([q.user_id for q in alive]))
                else:
                    await g.submit_skip(p.user_id)

    async def day():
        ids = [p.user_id for p in g.alive_players]
        for uid in ids:
            await g.submit_vote(uid, random.choice(ids))

    async def confirm():
        for p in g.alive_players:
            if (
                not p.blocked_vote
                and p.user_id not in g.confirm_likes
                and p.user_id not in g.confirm_dislikes
            ):
                await g.submit_confirm(p.user_id, random.choice(["like", "dislike"]))

    steps = 0
    while g.state not in ("ended",) and steps < 30:
        steps += 1
        if g.state == "night":
            await night()
        elif g.state == "voting":
            await day()
        elif g.state == "confirm":
            await confirm()
    assert g.state == "ended", g.state
    print(f"test_full_game OK (ended in {steps} steps)")


async def test_night_timeout_advances():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    old_night = game_mod.NIGHT_SECONDS
    game_mod.NIGHT_SECONDS = 0.01
    try:
        await g.start_night()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 2.0
        while g.state == "night" and loop.time() < deadline:
            await asyncio.sleep(0.05)
    finally:
        game_mod.NIGHT_SECONDS = old_night
    assert g.state == "voting", f"ночь зависла: state={g.state}"
    print("test_night_timeout_advances OK")


async def test_last_words():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA,
        1002: Role.CITIZEN, 1003: Role.CITIZEN,
    })
    g.players[1002].alive = False
    g.last_words_open.add(1002)
    assert await g.submit_last_words(1002, "Я знал, что так будет!") is True
    m = "\n".join(chat_msgs(g))
    assert "кричал перед смертью" in m and "Я знал" in m, m
    assert await g.submit_last_words(1002, "ещё раз") is False, "слова принимаются один раз"
    print("test_last_words OK")


async def test_mafia_cannot_kill_allies():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await g.start_night()
    await g.submit_target(1004, 1007)
    await g.submit_mode(1003, "check")
    await g.submit_target(1003, 1007)
    await g.submit_skip(1006)
    await g.submit_skip(1005)
    assert await g.submit_target(1001, 1002) is False, "мафия не может убить мафию"
    assert await g.submit_target(1001, 1000) is False, "мафия не может убить дона"
    assert await g.submit_target(1002, 1001) is False, "мафия не может убить мафию"
    await g.submit_skip(1001)
    await g.submit_skip(1002)
    assert await g.submit_target(1000, 1001) is False, "дон не может убить мафию"
    assert await g.submit_target(1000, 1002) is False, "дон не может убить мафию"
    await g.submit_skip(1000)
    assert g.players[1000].alive, "дон должен выжить"
    assert g.players[1001].alive, "мафия должна выжить"
    assert g.players[1002].alive, "мафия должна выжить"
    print("test_mafia_cannot_kill_allies OK")


async def test_kamikaze_guest_message_shows_don_only_when_don_and_mafia_same_target():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.KAMIKAZE,
    })
    await g.start_night()
    await g.submit_target(1000, 1009)
    await g.submit_skip(1001)
    await g.submit_skip(1002)
    await g.submit_skip(1003)
    await g.submit_skip(1004)
    await g.submit_skip(1005)
    await g.submit_skip(1006)
    assert g.players[1009].alive is False, "камикадзе должен погибнуть"
    m = "\n".join(chat_msgs(g))
    assert "Говорят, у него в гостях был: 🤵🏻 Дон" in m, m
    guest_lines = [l for l in m.splitlines() if l.startswith("Говорят, у него в гостях был:")]
    assert any("🤵🏻" in l for l in guest_lines), m
    assert not any("🤵🏼" in l for l in guest_lines), m
    print("test_kamikaze_guest_message_shows_don_only_when_don_and_mafia_same_target OK")


async def test_morning_merged_message():
    base = {
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.CITIZEN,
        1006: Role.CITIZEN, 1007: Role.CITIZEN,
    }
    g = make_game(dict(base))
    await g.start_night()
    while g.state == "night":
        for uid in list(g.night.awaiting_target):
            await g.submit_skip(uid)
    quiet = [t for t in chat_msgs(g) if "Наступило утро" in t]
    assert quiet and "Ночь прошла спокойно" in quiet[0], quiet

    g2 = make_game(dict(base))
    await g2.start_night()
    while g2.state == "night":
        for uid in list(g2.night.awaiting_target):
            role = g2.night.awaiting_target[uid]
            if role == Role.DON:
                await g2.submit_target(uid, 1005)
            elif role in (Role.MAFIA,):
                await g2.submit_target(uid, 1006)
            else:
                await g2.submit_skip(uid)
    morning = [t for t in chat_msgs(g2) if "Наступило утро" in t]
    assert morning and "сегодня убили" in morning[0], morning
    print("test_morning_merged_message OK")


async def test_heal_then_morning():
    g = make_game({
        1001: Role.DOCTOR, 1002: Role.CITIZEN,
        1000: Role.DON, 1003: Role.MAFIA, 1004: Role.MAFIA,
        1005: Role.COMMISSAR, 1006: Role.LAWYER, 1007: Role.MISTRESS,
        1008: Role.CITIZEN, 1009: Role.CITIZEN,
    })
    await g.start_night()
    await g.submit_target(1001, 1002)
    await g.submit_mode(1005, "check")
    await g.submit_target(1005, 1002)
    await g.submit_skip(1006)
    await g.submit_skip(1007)
    await g.submit_target(1003, 1002)
    await g.submit_target(1004, 1002)
    await g.submit_target(1000, 1002)
    assert g.state == "voting", f"зависло: state={g.state}"
    assert "Наступило утро" in "\n".join(chat_msgs(g))
    assert "🛡️ Тебя убили, но доктор спас тебя!" in "\n".join(dm_msgs(g, 1002))
    assert "Ночь прошла спокойно" in "\n".join(chat_msgs(g))
    print("test_heal_then_morning OK")


async def test_kamikaze_don_and_commissar_shot_reach_voting():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.KAMIKAZE,
        1009: Role.SERGEANT,
    })
    await night_flow(g, {
        1000: ("target", 1008),  # дон убивает камикадзе
        1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "shoot", 1009),  # коммисар стреляет в сержанта
        1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    assert g.state == "voting", f"зависло: state={g.state}"
    assert not g.players[1008].alive, "камикадзе погибает"
    assert not g.players[1000].alive, "дон взрывается вместе с камикадзе"
    assert not g.players[1009].alive, "сержант убит выстрелом коммисара"
    m = "\n".join(chat_msgs(g))
    assert "Камикадзе" in m and "Сержант" in m, m
    guests = [line for line in m.split("\n") if "в гостях был" in line]
    assert len(guests) == 3, guests
    assert "Камикадзе" in guests[0] and "💥" in guests[0] and "id1000" not in guests[0], guests
    assert "Дон" in guests[1] and "🤵🏻" in guests[1] and "id1000" not in guests[1], guests
    assert "Коммисар" in guests[2] and "id1003" not in guests[2] and "🕵️" in guests[2], guests
    print("test_kamikaze_don_and_commissar_shot_reach_voting OK")


async def test_lynch_confirm_spare():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",), 1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "check", 1001), 1004: ("skip",), 1005: ("skip",),
        1006: ("skip",),
    })
    assert g.state == "voting"
    for p in g.alive_players:
        await g.submit_vote(p.user_id, 1001)
    await g.resolve_votes()
    assert g.state == "confirm", f"state={g.state}"
    m = "\n".join(chat_msgs(g))
    assert "Вы уверены" in m, m
    for p in g.alive_players:
        await g.submit_confirm(p.user_id, "dislike")
    assert g.players[1001].alive, "помилованный должен выжить"
    assert g.state == "night", f"state={g.state}"
    print("test_lynch_confirm_spare OK")


async def test_mistress_cannot_repeat_visit():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await g.start_night()
    await g.send_night_prompt(g.players[1005], Role.MISTRESS)
    assert g.check_target(1005, 1007) is True, "первая ночь — можно выбрать любого"
    await g.submit_target(1005, 1007)
    assert g.mistress_visit == 1007

    await g.start_night()
    await g.send_night_prompt(g.players[1005], Role.MISTRESS)
    assert g.last_mistress_visit == 1007
    assert g.check_target(1005, 1007) is False, "вторая ночь — того же нельзя"
    assert g.check_target(1005, 1008) is True
    await g.submit_target(1005, 1008)
    assert g.mistress_visit == 1008

    await g.start_night()
    await g.send_night_prompt(g.players[1005], Role.MISTRESS)
    assert g.last_mistress_visit == 1008
    assert g.check_target(1005, 1007) is True, "третья ночь — можно снова"
    assert g.check_target(1005, 1008) is False
    print("test_mistress_cannot_repeat_visit OK")


async def test_commissar_check_notifies_target():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",), 1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "check", 1001),
        1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    dm = "\n".join(dm_msgs(g, 1001))
    assert "🕵️ Кто-то очень сильно заинтересовался твоей ролью." in dm, dm
    print("test_commissar_check_notifies_target OK")


async def test_doctor_visit_notification():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",), 1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "check", 1001), 1004: ("target", 1007), 1005: ("skip",),
        1006: ("skip",),
    })
    dm = "\n".join(dm_msgs(g, 1007))
    assert "🏥 Доктор приходил к тебе в гости." in dm, dm
    assert "спас тебя" not in dm, "визит не должен дублироваться со спасением"
    print("test_doctor_visit_notification OK")


async def test_doctor_self_heal_notification():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",), 1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "check", 1001), 1004: ("target", 1004), 1005: ("skip",),
        1006: ("skip",),
    })
    assert g.players[1004].self_healed, "доктор применил самолечение"
    dm = "\n".join(dm_msgs(g, 1004))
    assert "🩹 Сегодня ты остался жив, бинты и скальпель не пригодились." in dm, dm
    print("test_doctor_self_heal_notification OK")


async def test_night_duration_announced():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    await g.start_night()
    m = "\n".join(chat_msgs(g))
    assert "⏳ На ночь даётся" in m and "секунд" in m, m
    print("test_night_duration_announced OK")


async def test_inactivity_kill_after_3_nights():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    old_night = game_mod.NIGHT_SECONDS
    game_mod.NIGHT_SECONDS = 0.01
    try:
        for _ in range(3):
            await g.start_night()
            for uid in list(g.night.awaiting_target):
                if uid != 1004:
                    await g.submit_skip(uid)
            loop = asyncio.get_event_loop()
            deadline = loop.time() + 2.0
            while g.state == "night" and loop.time() < deadline:
                await asyncio.sleep(0.05)
    finally:
        game_mod.NIGHT_SECONDS = old_night
    assert not g.players[1004].alive, "доктор, спавший 3 ночи подряд, должен погибнуть"
    dm = "\n".join(dm_msgs(g, 1004))
    assert "Время вышло" not in dm, "бот должен молчать при бездействии"
    m = "\n".join(chat_msgs(g))
    assert "Я уснул во время игры, больше так не буду." in m, m
    assert "в гостях был" not in m, "у сна не должно быть убийцы"
    print("test_inactivity_kill_after_3_nights OK")


async def test_inactivity_resets_when_playing():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.CITIZEN,
        1009: Role.CITIZEN,
    })
    old_night = game_mod.NIGHT_SECONDS
    game_mod.NIGHT_SECONDS = 0.01
    try:
        for i in range(3):
            await g.start_night()
            for uid in list(g.night.awaiting_target):
                if uid == 1004:
                    if i == 1:
                        await g.submit_target(uid, 1007)
                else:
                    await g.submit_skip(uid)
            loop = asyncio.get_event_loop()
            deadline = loop.time() + 2.0
            while g.state == "night" and loop.time() < deadline:
                await asyncio.sleep(0.05)
    finally:
        game_mod.NIGHT_SECONDS = old_night
    assert g.players[1004].alive, "доктор, который начал играть, не должен погибнуть"
    assert g.players[1004].missed_nights == 1, g.players[1004].missed_nights
    print("test_inactivity_resets_when_playing OK")


async def test_kamikaze_revenge_after_lynch():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.KAMIKAZE,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",), 1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "check", 1001), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    assert g.state == "voting"
    for p in g.alive_players:
        await g.submit_vote(p.user_id, 1008)
    await g.resolve_votes()
    assert g.state == "confirm"
    for p in g.alive_players:
        await g.submit_confirm(p.user_id, "like")
    assert not g.players[1008].alive, "камикадзе повешен днём"
    assert g.players[1008].lynched, "у повешенного должен стоять флаг lynched"
    assert g.state == "night", f"после казни должна начаться ночь, state={g.state}"

    roles_in_night = [r for r, _ in g._night_groups]
    assert Role.KAMIKAZE in roles_in_night, "мёртвый камикадзе должен получить ночной ход"

    steps = 0
    while g.state == "night" and steps < 20:
        steps += 1
        for uid in list(g.night.awaiting_target):
            if uid == 1008:
                await g.submit_target(uid, 1007)
            else:
                await g.submit_skip(uid)
    assert not g.players[1007].alive, "камикадзе забирает выбранного с собой в могилу"
    m = "\n".join(chat_msgs(g))
    assert "Камикадзе" in m, m
    assert "в гостях был" in m, m
    print("test_kamikaze_revenge_after_lynch OK")


async def test_kamikaze_revenge_saved_by_doctor():
    g = make_game({
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.KAMIKAZE,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("skip",), 1001: ("skip",), 1002: ("skip",),
        1003: ("mode", "check", 1001), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    for p in g.alive_players:
        await g.submit_vote(p.user_id, 1008)
    await g.resolve_votes()
    for p in g.alive_players:
        await g.submit_confirm(p.user_id, "like")
    assert g.state == "night", f"state={g.state}"

    steps = 0
    while g.state == "night" and steps < 20:
        steps += 1
        for uid in list(g.night.awaiting_target):
            if uid == 1008:
                await g.submit_target(uid, 1007)
            elif uid == 1004:
                await g.submit_target(uid, 1007)
            else:
                await g.submit_skip(uid)
    assert g.players[1007].alive, "доктор спасает цель камикадзе"
    dm = "\n".join(dm_msgs(g, 1007))
    assert "🛡️ Тебя убили, но доктор спас тебя!" in dm, dm
    print("test_kamikaze_revenge_saved_by_doctor OK")


async def main():
    test_role_configs()
    await test_kill_and_heal()
    await test_mistress_blocks_don()
    await test_kamikaze()
    await test_lawyer_protects_from_check()
    await test_maniac_kills_target()
    await test_maniac_system_message()
    await test_mafia_unanimous_overrides_don()
    await test_mafia_three_unanimous_don_decides()
    await test_mafia_split_don_decides()
    await test_don_skip_mafia_unanimous_kills()
    await test_don_skip_mafia_split_no_kill()
    await test_mafia_decision_messages()
    await test_don_transfer_to_random_mafia()
    await test_don_afk_single_mafia_choice_counts()
    await test_mafia_skip_triggers_don_message()
    await test_don_and_one_mafia_don_decides()
    await test_commissar_shoot()
    await test_day_lynch()
    await test_1v1_endgame()
    await test_full_game()
    await test_night_timeout_advances()
    await test_last_words()
    await test_mafia_cannot_kill_allies()
    await test_morning_merged_message()
    await test_heal_then_morning()
    await test_kamikaze_don_and_commissar_shot_reach_voting()
    await test_lynch_confirm_spare()
    await test_mistress_cannot_repeat_visit()
    await test_commissar_check_notifies_target()
    await test_doctor_visit_notification()
    await test_doctor_self_heal_notification()
    await test_night_duration_announced()
    await test_inactivity_kill_after_3_nights()
    await test_inactivity_resets_when_playing()
    await test_kamikaze_revenge_after_lynch()
    await test_kamikaze_revenge_saved_by_doctor()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
