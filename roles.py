from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    DON = "don"
    MAFIA = "mafia"
    CITIZEN = "citizen"
    COMMISSAR = "commissar"
    SERGEANT = "sergeant"
    MISTRESS = "mistress"
    LAWYER = "lawyer"
    KAMIKAZE = "kamikaze"
    DOCTOR = "doctor"


ROLE_RU = {
    Role.DON: "Дон",
    Role.MAFIA: "Мафия",
    Role.CITIZEN: "Мирный житель",
    Role.COMMISSAR: "Коммисар",
    Role.SERGEANT: "Сержант",
    Role.MISTRESS: "Любовница",
    Role.LAWYER: "Адвокат",
    Role.KAMIKAZE: "Камикадзе",
    Role.DOCTOR: "Доктор",
}

ROLE_EMOJI = {
    Role.DON: "🤵🏻",
    Role.MAFIA: "🤵🏼",
    Role.CITIZEN: "👨🏼‍🦳",
    Role.COMMISSAR: "🕵️",
    Role.SERGEANT: "🎖️",
    Role.MISTRESS: "💋",
    Role.LAWYER: "⚖️",
    Role.KAMIKAZE: "💥",
    Role.DOCTOR: "🩺",
}

MAFIA_SIDE = {Role.DON, Role.MAFIA}

ROLE_CONFIG: dict[int, dict[Role, int]] = {
    4: {Role.DON: 1, Role.COMMISSAR: 1, Role.CITIZEN: 2},
    5: {Role.DON: 1, Role.DOCTOR: 1, Role.CITIZEN: 3},
    6: {Role.DON: 1, Role.MAFIA: 1, Role.COMMISSAR: 1, Role.DOCTOR: 1, Role.CITIZEN: 2},
    7: {Role.DON: 1, Role.MAFIA: 1, Role.COMMISSAR: 1, Role.DOCTOR: 1, Role.CITIZEN: 3},
    8: {Role.DON: 1, Role.MAFIA: 2, Role.COMMISSAR: 1, Role.DOCTOR: 1, Role.CITIZEN: 3},
    9: {Role.DON: 1, Role.MAFIA: 2, Role.COMMISSAR: 1, Role.DOCTOR: 1, Role.CITIZEN: 4},
    10: {Role.DON: 1, Role.MAFIA: 3, Role.COMMISSAR: 1, Role.DOCTOR: 1, Role.CITIZEN: 4},
    11: {Role.DON: 1, Role.MAFIA: 3, Role.COMMISSAR: 1, Role.DOCTOR: 1, Role.CITIZEN: 5},
    12: {Role.DON: 1, Role.MAFIA: 3, Role.COMMISSAR: 1, Role.DOCTOR: 1, Role.MISTRESS: 1, Role.CITIZEN: 5},
    13: {Role.DON: 1, Role.MAFIA: 4, Role.COMMISSAR: 1, Role.DOCTOR: 1, Role.MISTRESS: 1, Role.CITIZEN: 5},
    14: {Role.DON: 1, Role.MAFIA: 4, Role.COMMISSAR: 1, Role.DOCTOR: 1, Role.MISTRESS: 1, Role.LAWYER: 1, Role.CITIZEN: 5},
    15: {Role.DON: 1, Role.MAFIA: 4, Role.COMMISSAR: 1, Role.DOCTOR: 1, Role.MISTRESS: 1, Role.LAWYER: 1, Role.SERGEANT: 1, Role.KAMIKAZE: 1, Role.CITIZEN: 4},
}


def roles_for_count(count: int) -> dict[Role, int] | None:
    return ROLE_CONFIG.get(count)


def role_plural(role: Role, count: int) -> str:
    name = ROLE_RU[role]
    if count == 1:
        return name
    forms = {
        Role.CITIZEN: ("Мирных жителей", "Мирных жителя", "Мирных жителей"),
        Role.MAFIA: ("Мафий", "Мафии", "Мафий"),
    }
    n = count % 10
    n100 = count % 100
    if 11 <= n100 <= 19:
        idx = 2
    elif n == 1:
        idx = 0
    elif 2 <= n <= 4:
        idx = 1
    else:
        idx = 2
    if role in forms:
        return f"{count} {forms[role][idx]}"
    return f"{count} {name.lower()}"
