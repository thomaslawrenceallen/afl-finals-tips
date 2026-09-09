"""AFL finals tipping — FastAPI backend.

One database, many comps. A comp is reached by an unguessable code in the URL;
joining it needs the join password, and editing fixtures or results needs the
admin password. Points are only ever calculated here.

Run:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Literal

from pydantic import BaseModel, Field

from .scoring import DEFAULT_SCORING, score_game

DB_PATH = os.environ.get("TIPS_DB", "tips.db")
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Finals Tips", docs_url="/api/docs", openapi_url="/api/openapi.json")


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS comps (
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  admin_hash TEXT NOT NULL,
  join_hash TEXT,
  scoring TEXT NOT NULL,
  lock_all INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS players (
  id INTEGER PRIMARY KEY,
  comp_id INTEGER NOT NULL REFERENCES comps(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  pass_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  UNIQUE (comp_id, name)
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rounds (
  id INTEGER PRIMARY KEY,
  comp_id INTEGER NOT NULL REFERENCES comps(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  ord INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS games (
  id INTEGER PRIMARY KEY,
  round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
  home TEXT NOT NULL DEFAULT '',
  away TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '',
  start TEXT,                       -- UTC ISO8601, sent by the client
  favourite TEXT NOT NULL DEFAULT '',
  featured INTEGER NOT NULL DEFAULT 0,
  result TEXT
);
CREATE TABLE IF NOT EXISTS tips (
  player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  winner TEXT,
  margin INTEGER,
  extras TEXT,
  PRIMARY KEY (player_id, game_id)
);
CREATE TABLE IF NOT EXISTS adjustments (
  player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  points INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  PRIMARY KEY (player_id, game_id)
);
"""


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with db() as con:
        con.executescript(SCHEMA)


init_db()


# --------------------------------------------------------------------------
# passwords, tokens, throttling
# --------------------------------------------------------------------------
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"{salt.hex()}:{dk.hex()}"


def verify_pw(pw: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        salt_hex, dk_hex = stored.split(":")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(dk.hex(), dk_hex)


def new_session(con, player_id: int, token: str) -> None:
    con.execute(
        "INSERT INTO sessions (token, player_id, created) VALUES (?,?,?)",
        (token, player_id, time.time()),
    )


_attempts: dict[str, list[float]] = {}


def throttle(key: str, limit: int = 8, window: float = 300.0):
    """Crude in-process brake on password guessing. Swap for Redis if you ever scale."""
    now = time.time()
    hits = [t for t in _attempts.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Too many attempts — wait five minutes")
    hits.append(now)
    _attempts[key] = hits


# --------------------------------------------------------------------------
# auth dependencies
# --------------------------------------------------------------------------
def get_comp(con, code: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM comps WHERE code = ?", (code,)).fetchone()
    if not row:
        raise HTTPException(404, "No comp at that link")
    return row


def bearer(authorization: str | None = Header(None)) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_player(code: str, token: str | None, con) -> sqlite3.Row:
    if not token:
        raise HTTPException(401, "Sign in to continue")
    comp = get_comp(con, code)
    row = con.execute(
        "SELECT p.* FROM players p JOIN sessions s ON s.player_id = p.id"
        " WHERE s.token = ? AND p.comp_id = ?",
        (token, comp["id"]),
    ).fetchone()
    if not row:
        raise HTTPException(401, "Sign in again")
    return row


def require_admin(code: str, token: str | None, con) -> sqlite3.Row:
    p = current_player(code, token, con)
    if not p["is_admin"]:
        raise HTTPException(403, "Admin only")
    return p


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_start(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def is_locked(game_row, lock_all: bool) -> bool:
    if lock_all:
        return True
    if game_row["result"]:
        return True
    start = parse_start(game_row["start"])
    return bool(start and now_utc() >= start)


def game_dict(row, lock_all: bool) -> dict:
    return {
        "id": row["id"],
        "home": row["home"],
        "away": row["away"],
        "label": row["label"],
        "start": row["start"],
        "favourite": row["favourite"],
        "featured": bool(row["featured"]),
        "result": json.loads(row["result"]) if row["result"] else None,
        "locked": is_locked(row, lock_all),
    }


def tip_dict(row) -> dict:
    return {
        "winner": row["winner"],
        "margin": row["margin"],
        "extras": json.loads(row["extras"]) if row["extras"] else {},
    }


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------
class CompCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    admin_password: str = Field(min_length=6, max_length=128)
    join_password: str = Field(default="", max_length=128)
    your_name: str = Field(min_length=1, max_length=24)
    your_password: str = Field(min_length=6, max_length=128)


class Register(BaseModel):
    name: str = Field(min_length=1, max_length=24)
    password: str = Field(min_length=6, max_length=128)
    join_password: str = ""


class Login(BaseModel):
    name: str
    password: str


class AdminUnlock(BaseModel):
    password: str


class TipIn(BaseModel):
    game_id: int
    winner: str | None = None
    # 1-200: you can't tip a draw, because a draw can't score under the rules.
    # Note this is tighter than ResultIn.margin, where 0 IS a legitimate draw.
    margin: int = Field(default=12, ge=1, le=200)
    extras: dict = {}


class AdjustmentIn(BaseModel):
    points: int = Field(ge=-100, le=100)
    reason: str = Field(default="", max_length=120)


class RoundIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class GameIn(BaseModel):
    home: str = ""
    away: str = ""
    label: str = ""
    start: str | None = None
    favourite: str = ""
    featured: bool = False


class ResultIn(BaseModel):
    winner: Literal["home", "away"] = "home"
    margin: int = Field(default=0, ge=0, le=200)   # 0 means a draw
    fgs: str = ""
    goals1: str = ""
    goals2: str = ""
    disp1: str = ""
    disp2: str = ""


# --------------------------------------------------------------------------
# comp lifecycle
# --------------------------------------------------------------------------
@app.post("/api/comps")
def create_comp(body: CompCreate):
    code = secrets.token_urlsafe(9)
    token = secrets.token_urlsafe(24)
    with db() as con:
        cur = con.execute(
            "INSERT INTO comps (code, name, admin_hash, join_hash, scoring, lock_all, created)"
            " VALUES (?,?,?,?,?,0,?)",
            (
                code,
                body.name.strip(),
                hash_pw(body.admin_password),
                hash_pw(body.join_password) if body.join_password else None,
                json.dumps(DEFAULT_SCORING),
                time.time(),
            ),
        )
        pc = con.execute(
            "INSERT INTO players (comp_id, name, pass_hash, is_admin) VALUES (?,?,?,1)",
            (cur.lastrowid, body.your_name.strip(), hash_pw(body.your_password)),
        )
        new_session(con, pc.lastrowid, token)
    return {"code": code, "token": token, "url": f"/c/{code}"}


@app.get("/api/comps/{code}")
def comp_meta(code: str):
    with db() as con:
        comp = get_comp(con, code)
        n = con.execute("SELECT COUNT(*) c FROM players WHERE comp_id = ?", (comp["id"],)).fetchone()["c"]
    return {"name": comp["name"], "needs_join_password": comp["join_hash"] is not None, "players": n}


@app.post("/api/comps/{code}/register")
def register(code: str, body: Register):
    throttle(f"reg:{code}")
    with db() as con:
        comp = get_comp(con, code)
        if comp["join_hash"] and not verify_pw(body.join_password, comp["join_hash"]):
            raise HTTPException(403, "Wrong join password")
        name = body.name.strip()
        if con.execute(
            "SELECT 1 FROM players WHERE comp_id = ? AND name = ?", (comp["id"], name)
        ).fetchone():
            raise HTTPException(409, "That name is taken — sign in instead")
        token = secrets.token_urlsafe(24)
        pc = con.execute(
            "INSERT INTO players (comp_id, name, pass_hash) VALUES (?,?,?)",
            (comp["id"], name, hash_pw(body.password)),
        )
        new_session(con, pc.lastrowid, token)
    return {"token": token, "name": name, "is_admin": False}


@app.post("/api/comps/{code}/login")
def login(code: str, body: Login):
    throttle(f"login:{code}:{body.name.strip().lower()}")
    with db() as con:
        comp = get_comp(con, code)
        p = con.execute(
            "SELECT * FROM players WHERE comp_id = ? AND name = ?", (comp["id"], body.name.strip())
        ).fetchone()
        if not p or not verify_pw(body.password, p["pass_hash"]):
            raise HTTPException(401, "Wrong name or password")
        token = secrets.token_urlsafe(24)
        new_session(con, p["id"], token)   # extra device, existing sessions survive
    return {"token": token, "name": p["name"], "is_admin": bool(p["is_admin"])}


@app.post("/api/comps/{code}/admin")
def unlock_admin(code: str, body: AdminUnlock, token: str | None = Depends(bearer)):
    throttle(f"admin:{code}")
    with db() as con:
        comp = get_comp(con, code)
        p = current_player(code, token, con)
        if not verify_pw(body.password, comp["admin_hash"]):
            raise HTTPException(403, "Wrong admin password")
        con.execute("UPDATE players SET is_admin = 1 WHERE id = ?", (p["id"],))
    return {"is_admin": True}


# --------------------------------------------------------------------------
# state — the one read the client makes
# --------------------------------------------------------------------------
@app.get("/api/comps/{code}/state")
def state(code: str, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        me = current_player(code, token, con)
        sc = json.loads(comp["scoring"])
        lock_all = bool(comp["lock_all"])

        players = con.execute(
            "SELECT id, name, is_admin FROM players WHERE comp_id = ? ORDER BY name", (comp["id"],)
        ).fetchall()
        all_tips = {
            (r["player_id"], r["game_id"]): tip_dict(r)
            for r in con.execute(
                "SELECT t.* FROM tips t JOIN players p ON p.id = t.player_id WHERE p.comp_id = ?",
                (comp["id"],),
            )
        }

        adjustments = {
            (r["player_id"], r["game_id"]): {"points": r["points"], "reason": r["reason"]}
            for r in con.execute(
                "SELECT a.* FROM adjustments a JOIN players p ON p.id = a.player_id"
                " WHERE p.comp_id = ?",
                (comp["id"],),
            )
        }

        rounds_out, totals = [], {p["id"]: 0 for p in players}
        for r in con.execute(
            "SELECT * FROM rounds WHERE comp_id = ? ORDER BY ord, id", (comp["id"],)
        ):
            games_out = []
            for g in con.execute("SELECT * FROM games WHERE round_id = ? ORDER BY start, id", (r["id"],)):
                gd = game_dict(g, lock_all)
                gd["my_tip"] = all_tips.get((me["id"], g["id"]))
                # everyone else's tips stay sealed until the first bounce
                gd["tips"] = (
                    {
                        p["id"]: all_tips.get((p["id"], g["id"]))
                        for p in players
                        if all_tips.get((p["id"], g["id"]))
                    }
                    if gd["locked"]
                    else {}
                )
                gd["scores"] = {}
                gd["adjustments"] = {}
                for p in players:
                    s = score_game(gd, all_tips.get((p["id"], g["id"])), sc)
                    adj = adjustments.get((p["id"], g["id"]))
                    if adj:
                        # An adjustment stands on its own — it applies even to a
                        # game with no result yet, so admin can correct early.
                        s = s or {"total": 0, "lines": []}
                        s["total"] += adj["points"]
                        s["lines"].append({
                            "label": "Adjustment" + (f" — {adj['reason']}" if adj["reason"] else ""),
                            "pts": adj["points"],
                            "hit": adj["points"] > 0,
                            "adjustment": True,
                        })
                        gd["adjustments"][p["id"]] = adj
                    if s:
                        gd["scores"][p["id"]] = s
                        totals[p["id"]] += s["total"]
                games_out.append(gd)
            rounds_out.append({"id": r["id"], "name": r["name"], "games": games_out})

        ladder = sorted(
            ({"id": p["id"], "name": p["name"], "total": totals[p["id"]]} for p in players),
            key=lambda x: (-x["total"], x["name"].lower()),
        )

    return {
        "comp": {"name": comp["name"], "code": code, "lock_all": lock_all},
        "me": {"id": me["id"], "name": me["name"], "is_admin": bool(me["is_admin"])},
        "scoring": sc,
        "players": [{"id": p["id"], "name": p["name"]} for p in players],
        "rounds": rounds_out,
        "ladder": ladder,
        "server_time": now_utc().isoformat(),
    }


# --------------------------------------------------------------------------
# tips
# --------------------------------------------------------------------------
@app.put("/api/comps/{code}/tips")
def save_tips(code: str, body: list[TipIn], token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        me = current_player(code, token, con)
        lock_all = bool(comp["lock_all"])
        saved, rejected = 0, []
        for t in body:
            g = con.execute(
                "SELECT g.* FROM games g JOIN rounds r ON r.id = g.round_id"
                " WHERE g.id = ? AND r.comp_id = ?",
                (t.game_id, comp["id"]),
            ).fetchone()
            if not g:
                rejected.append({"game_id": t.game_id, "why": "unknown game"})
                continue
            if is_locked(g, lock_all):
                rejected.append({"game_id": t.game_id, "why": "locked"})
                continue
            if t.winner not in (None, "home", "away"):
                rejected.append({"game_id": t.game_id, "why": "bad winner"})
                continue
            extras = {k: str(v)[:40] for k, v in list(t.extras.items())[:6]}
            con.execute(
                "INSERT INTO tips (player_id, game_id, winner, margin, extras) VALUES (?,?,?,?,?)"
                " ON CONFLICT(player_id, game_id) DO UPDATE SET"
                " winner = excluded.winner, margin = excluded.margin, extras = excluded.extras",
                (me["id"], t.game_id, t.winner, t.margin, json.dumps(extras)),
            )
            saved += 1
    return {"saved": saved, "rejected": rejected}


# --------------------------------------------------------------------------
# admin: fixtures, results, scoring
# --------------------------------------------------------------------------
@app.post("/api/comps/{code}/rounds")
def add_round(code: str, body: RoundIn, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        n = con.execute("SELECT COUNT(*) c FROM rounds WHERE comp_id = ?", (comp["id"],)).fetchone()["c"]
        cur = con.execute(
            "INSERT INTO rounds (comp_id, name, ord) VALUES (?,?,?)", (comp["id"], body.name.strip(), n)
        )
    return {"id": cur.lastrowid}


@app.patch("/api/comps/{code}/rounds/{rid}")
def rename_round(code: str, rid: int, body: RoundIn, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        con.execute(
            "UPDATE rounds SET name = ? WHERE id = ? AND comp_id = ?", (body.name.strip(), rid, comp["id"])
        )
    return {"ok": True}


@app.delete("/api/comps/{code}/rounds/{rid}")
def del_round(code: str, rid: int, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        con.execute("DELETE FROM rounds WHERE id = ? AND comp_id = ?", (rid, comp["id"]))
    return {"ok": True}


def _own_round(con, comp_id: int, rid: int):
    r = con.execute("SELECT * FROM rounds WHERE id = ? AND comp_id = ?", (rid, comp_id)).fetchone()
    if not r:
        raise HTTPException(404, "No such round")
    return r


def _own_game(con, comp_id: int, gid: int):
    g = con.execute(
        "SELECT g.* FROM games g JOIN rounds r ON r.id = g.round_id WHERE g.id = ? AND r.comp_id = ?",
        (gid, comp_id),
    ).fetchone()
    if not g:
        raise HTTPException(404, "No such game")
    return g


@app.post("/api/comps/{code}/rounds/{rid}/games")
def add_game(code: str, rid: int, body: GameIn, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        _own_round(con, comp["id"], rid)
        if body.featured:
            con.execute(
                "UPDATE games SET featured = 0 WHERE round_id = ?", (rid,)
            )
        cur = con.execute(
            "INSERT INTO games (round_id, home, away, label, start, favourite, featured)"
            " VALUES (?,?,?,?,?,?,?)",
            (rid, body.home, body.away, body.label, body.start, body.favourite, int(body.featured)),
        )
    return {"id": cur.lastrowid}


@app.patch("/api/comps/{code}/games/{gid}")
def edit_game(code: str, gid: int, body: GameIn, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        g = _own_game(con, comp["id"], gid)
        if body.featured:
            con.execute("UPDATE games SET featured = 0 WHERE round_id = ?", (g["round_id"],))
        con.execute(
            "UPDATE games SET home=?, away=?, label=?, start=?, favourite=?, featured=? WHERE id=?",
            (body.home, body.away, body.label, body.start, body.favourite, int(body.featured), gid),
        )
    return {"ok": True}


@app.delete("/api/comps/{code}/games/{gid}")
def del_game(code: str, gid: int, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        _own_game(con, comp["id"], gid)
        con.execute("DELETE FROM games WHERE id = ?", (gid,))
    return {"ok": True}


@app.put("/api/comps/{code}/games/{gid}/result")
def set_result(code: str, gid: int, body: ResultIn, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        _own_game(con, comp["id"], gid)
        con.execute("UPDATE games SET result = ? WHERE id = ?", (json.dumps(body.model_dump()), gid))
    return {"ok": True}


@app.delete("/api/comps/{code}/games/{gid}/result")
def clear_result(code: str, gid: int, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        _own_game(con, comp["id"], gid)
        con.execute("UPDATE games SET result = NULL WHERE id = ?", (gid,))
    return {"ok": True}


@app.put("/api/comps/{code}/games/{gid}/adjust/{pid}")
def set_adjustment(
    code: str, gid: int, pid: int, body: AdjustmentIn, token: str | None = Depends(bearer)
):
    """Add or replace a manual points adjustment for one player on one game.

    Deliberately separate from tips: correcting a score never touches what
    somebody lodged, and the adjustment is shown to everyone rather than
    quietly folded into the total.
    """
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        _own_game(con, comp["id"], gid)
        if not con.execute(
            "SELECT 1 FROM players WHERE id = ? AND comp_id = ?", (pid, comp["id"])
        ).fetchone():
            raise HTTPException(404, "No such player in this comp")
        if body.points == 0:
            con.execute("DELETE FROM adjustments WHERE player_id = ? AND game_id = ?", (pid, gid))
            return {"cleared": True}
        con.execute(
            "INSERT INTO adjustments (player_id, game_id, points, reason, created)"
            " VALUES (?,?,?,?,?)"
            " ON CONFLICT(player_id, game_id) DO UPDATE SET"
            " points = excluded.points, reason = excluded.reason, created = excluded.created",
            (pid, gid, body.points, body.reason.strip(), time.time()),
        )
    return {"points": body.points, "reason": body.reason.strip()}


@app.delete("/api/comps/{code}/games/{gid}/adjust/{pid}")
def clear_adjustment(code: str, gid: int, pid: int, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        _own_game(con, comp["id"], gid)
        con.execute("DELETE FROM adjustments WHERE player_id = ? AND game_id = ?", (pid, gid))
    return {"cleared": True}


@app.put("/api/comps/{code}/scoring")
def set_scoring(code: str, body: dict, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        sc = {**DEFAULT_SCORING}
        for k, v in body.items():
            if k not in DEFAULT_SCORING:
                continue
            sc[k] = bool(v) if isinstance(DEFAULT_SCORING[k], bool) else max(0, min(50, int(v)))
        con.execute("UPDATE comps SET scoring = ? WHERE id = ?", (json.dumps(sc), comp["id"]))
    return sc


@app.put("/api/comps/{code}/lock")
def set_lock(code: str, body: dict, token: str | None = Depends(bearer)):
    with db() as con:
        comp = get_comp(con, code)
        require_admin(code, token, con)
        con.execute("UPDATE comps SET lock_all = ? WHERE id = ?", (int(bool(body.get("lock_all"))), comp["id"]))
    return {"lock_all": bool(body.get("lock_all"))}


# --------------------------------------------------------------------------
# static
# --------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/c/{code}")
def spa(code: str):
    return FileResponse(STATIC / "index.html")
