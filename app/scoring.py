"""Scoring rules for the finals tipping comp.

Single source of truth: the client never computes points, it only displays
what the server returns.
"""
from __future__ import annotations

import re

DEFAULT_SCORING = {
    "pick": 2,            # tipped the winner
    "underdog": 3,        # tipped the winner and they were the outsider (replaces `pick`)
    "m_exact": 3,         # margin spot on
    "m_close": 2,         # margin 1-5 out
    "m_far": 1,           # margin 6-10 out
    "fgs": 2,             # first goal of the match
    "goals1": 2,          # leading goalkicker
    "goals2": 1,          # named the second-highest goalkicker instead
    "disp1": 2,           # most disposals
    "disp2": 1,           # named the second-highest instead
    "margin_only_if_correct": False,
    "margin_on_draw": True,
}

_WORD = re.compile(r"[^a-z ]+")


def name_key(s: str | None) -> str:
    """Match players on surname, so 'Gawn', 'M Gawn' and 'Max Gawn' all agree."""
    if not s:
        return ""
    parts = _WORD.sub(" ", str(s).lower()).split()
    return parts[-1] if parts else ""


def margin_band(diff: int, sc: dict) -> int:
    if diff == 0:
        return sc["m_exact"]
    if diff <= 5:
        return sc["m_close"]
    if diff <= 10:
        return sc["m_far"]
    return 0


def score_game(game: dict, tip: dict | None, sc: dict) -> dict | None:
    """Return {'total': int, 'lines': [{'label','pts','hit'}]} or None if no result yet."""
    result = game.get("result")
    if not result:
        return None

    lines: list[dict] = []
    total = 0
    # A result is a winning side and a margin. Margin 0 means a draw, whatever
    # side is recorded as the winner.
    won_by = int(result.get("margin") or 0)
    actual = won_by if result.get("winner") == "home" else -won_by   # positive = home win
    draw = won_by == 0

    tip = tip or {}
    winner = tip.get("winner")
    if winner not in ("home", "away"):
        return {"total": 0, "lines": [{"label": "No tip lodged", "pts": 0, "hit": False}]}

    mag = abs(int(tip.get("margin") or 0))
    pred = mag if winner == "home" else -mag
    correct_team = (not draw) and (result.get("winner") == winner)

    if draw:
        lines.append({"label": "Drawn game — no tip points", "pts": 0, "hit": False})
    elif correct_team:
        is_dog = bool(game.get("favourite")) and game["favourite"] != winner
        pts = sc["underdog"] if is_dog else sc["pick"]
        total += pts
        lines.append({"label": "Underdog getting up" if is_dog else "Winner", "pts": pts, "hit": True})
    else:
        lines.append({"label": "Winner", "pts": 0, "hit": False})

    diff = abs(pred - actual)
    blocked = (sc["margin_only_if_correct"] and not correct_team) or (draw and not sc["margin_on_draw"])
    mp = 0 if blocked else margin_band(diff, sc)
    total += mp
    lines.append({"label": f"Margin — out by {diff}", "pts": mp, "hit": mp > 0})

    if game.get("featured"):
        extras = tip.get("extras") or {}

        guess = name_key(extras.get("fgs"))
        if guess and guess == name_key(result.get("fgs")):
            total += sc["fgs"]
            lines.append({"label": "First goal", "pts": sc["fgs"], "hit": True})
        else:
            lines.append({"label": "First goal", "pts": 0, "hit": False})

        def best_of(field, first, second, p1, p2, label):
            nonlocal total
            g = name_key(extras.get(field))
            if g and g == name_key(result.get(first)):
                total += p1
                lines.append({"label": label, "pts": p1, "hit": True})
            elif g and g == name_key(result.get(second)):
                total += p2
                lines.append({"label": f"{label} (took second)", "pts": p2, "hit": True})
            else:
                lines.append({"label": label, "pts": 0, "hit": False})

        best_of("goals", "goals1", "goals2", sc["goals1"], sc["goals2"], "Most goals")
        best_of("disp", "disp1", "disp2", sc["disp1"], sc["disp2"], "Most disposals")

    return {"total": total, "lines": lines}
