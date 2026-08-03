from __future__ import annotations

from dataclasses import dataclass, field

from roles import Role


@dataclass
class Player:
    user_id: int
    name: str
    number: int = 0
    role: Role | None = None
    alive: bool = True
    self_healed: bool = False
    blocked_vote: bool = False
    is_bot: bool = False
    lynched: bool = False
    kamikaze_used: bool = False
    missed_nights: int = 0


@dataclass
class Action:
    role: Role
    target: int | None = None
    mode: str | None = None


@dataclass
class NightData:
    needed: set[int] = field(default_factory=set)
    actions: dict[int, Action] = field(default_factory=dict)
    awaiting_target: dict[int, Role] = field(default_factory=dict)
    commissar_mode: dict[int, str] = field(default_factory=dict)
