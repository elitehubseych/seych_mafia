from __future__ import annotations

import json
from typing import Iterable

import config
from models import Player

TARGET = "target"
VOTE = "vote"

MAX_INLINE_BUTTONS = 10
MAX_INLINE_ROWS = 6
PAGE_SIZE = 8


def _btn(label: str, payload: dict, color: str = "primary") -> dict:
    return {
        "action": {
            "type": "callback",
            "label": label,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
        "color": color,
    }


def _rows(items: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    row: list[dict] = []
    for item in items:
        row.append(item)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def players_kb(
    players: Iterable[Player],
    *,
    action: str = TARGET,
    exclude: set[int] | None = None,
    include_skip: bool = True,
    skip_label: str = "⏭️ Пропустить",
    skip_data: str = "skip",
    page: int = 0,
) -> dict:
    exclude = exclude or set()
    pool = [p for p in sorted(players, key=lambda x: x.number) if p.user_id not in exclude]
    total_pages = max(1, (len(pool) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = pool[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    buttons = [_btn(f"{p.number}. {p.name}", {"t": action, "u": p.user_id}) for p in chunk]
    rows = _rows(buttons)
    nav = []
    if page > 0:
        nav.append(_btn("⏪", {"t": "page", "a": action, "p": page - 1}, color="secondary"))
    if page < total_pages - 1:
        nav.append(_btn("⏩", {"t": "page", "a": action, "p": page + 1}, color="secondary"))
    if nav:
        rows.append(nav)
    if include_skip:
        rows.append([_btn(skip_label, {"t": skip_data}, color="secondary")])
    return {"inline": True, "buttons": rows}


def commissar_mode_kb() -> dict:
    return {
        "inline": True,
        "buttons": [
            [
                _btn("🕵️ Проверить роль", {"t": "cmode", "m": "check"}),
                _btn("🔫 Стрелять", {"t": "cmode", "m": "shoot"}),
            ],
            [_btn("😴 Коммисар сегодня спит", {"t": "skip"}, color="secondary")],
        ],
    }


def join_kb() -> dict:
    return {
        "inline": True,
        "buttons": [[_btn("➕ Присоединиться", {"t": "join"}, color="positive")]],
    }


def bot_dm_kb() -> dict:
    return {
        "inline": True,
        "buttons": [
            [
                {
                    "action": {
                        "type": "open_link",
                        "label": "💬 Написать боту в личные сообщения",
                        "link": config.VK_ME_LINK,
                    }
                }
            ]
        ],
    }


def inline_link_kb(label: str, url: str) -> dict:
    return {
        "inline": True,
        "buttons": [
            [
                {
                    "action": {
                        "type": "open_link",
                        "label": label,
                        "link": url,
                    }
                }
            ]
        ],
    }


def lynch_confirm_kb(likes: int, dislikes: int) -> dict:
    return {
        "inline": True,
        "buttons": [
            [
                _btn(f"👍 ({likes})", {"t": "confirm", "v": "like"}, color="positive"),
                _btn(f"👎 ({dislikes})", {"t": "confirm", "v": "dislike"}, color="negative"),
            ],
        ],
    }
