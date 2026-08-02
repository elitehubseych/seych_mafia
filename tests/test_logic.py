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

    async def send(self, chat_id, text, keyboard=None, **kw):
        self.sent.append((chat_id, text))
        return len(self.sent)

    async def edit(self, peer_id, message_id, text, keyboard=None):
        self.edited.append(text)


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
    m = "\n".join(chat_msgs(g))
    assert "доктор вылечил" in m, m
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
        1000: Role.DON, 1001: Role.MAFIA, 1002: Role.MAFIA,
        1003: Role.COMMISSAR, 1004: Role.DOCTOR, 1005: Role.MISTRESS,
        1006: Role.LAWYER, 1007: Role.CITIZEN, 1008: Role.KAMIKAZE,
        1009: Role.CITIZEN,
    })
    await night_flow(g, {
        1000: ("target", 1009),  # дон хочет 1009
        1001: ("target", 1008),  # мафия единогласно — 1008
        1002: ("target", 1008),
        1003: ("skip",), 1004: ("skip",), 1005: ("skip",), 1006: ("skip",),
    })
    assert not g.players[1008].alive, "единогласный голос мафии должен убить 1008"
    assert g.players[1009].alive, "выбор дона перебит"
    print("test_mafia_unanimous_overrides_don OK")


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


async def test_night_group_timeout_advances():
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
    print("test_night_group_timeout_advances OK")


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
    m = "\n".join(chat_msgs(g))
    assert "Наступило утро" in m
    assert "доктор вылечил" in m
    assert "Ночь прошла спокойно" not in m
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
    assert len(guests) == 2, guests
    assert "Дон" in guests[0] and "id1000" not in guests[0] and "🤵🏻" in guests[0], guests
    assert "Коммисар" in guests[1] and "id1003" not in guests[1] and "🕵️" in guests[1], guests
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


async def main():
    test_role_configs()
    await test_kill_and_heal()
    await test_mistress_blocks_don()
    await test_kamikaze()
    await test_lawyer_protects_from_check()
    await test_mafia_unanimous_overrides_don()
    await test_commissar_shoot()
    await test_day_lynch()
    await test_1v1_endgame()
    await test_full_game()
    await test_night_group_timeout_advances()
    await test_last_words()
    await test_mafia_cannot_kill_allies()
    await test_morning_merged_message()
    await test_heal_then_morning()
    await test_kamikaze_don_and_commissar_shot_reach_voting()
    await test_lynch_confirm_spare()
    await test_mistress_cannot_repeat_visit()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
