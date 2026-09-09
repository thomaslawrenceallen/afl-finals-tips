# Finals Tips

AFL finals tipping for a group of mates. FastAPI + SQLite backend, single-page
frontend, no build step.

**New here? Read `SETUP.md`** (or **`SETUP-WINDOWS.md`** on Windows) — it walks the whole thing from unzip to mates
tipping, including the Fly.io deploy. This file is the reference.

## Run it locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000, fill in the create-comp form, and you'll land on
`/c/<code>`. That URL is the secret link — send it to your mates.

Interactive API docs are at `/api/docs`.

## Access model

Three separate gates, so a mate can't quietly promote himself:

| Gate | What it protects | Who needs it |
|---|---|---|
| Secret link (`/c/<code>`) | Finding the comp at all | Everyone |
| Join password | Registering a new name | Everyone, once |
| Admin password | Fixtures, results, scoring, lockout | You |

The code is 12 random URL-safe characters from `secrets.token_urlsafe`, so it
isn't guessable. Passwords are stored as PBKDF2-SHA256 with a per-user salt and
200k iterations. Sessions are random bearer tokens in their own table, so
signing in on your phone doesn't log you out on your laptop.

Password guessing is throttled to 8 attempts per five minutes, in process
memory. If you ever run more than one worker, move that to Redis or it's per
worker rather than global.

## What the server enforces

The client is a display layer. It never decides anything that matters.

- **Points** are only ever calculated in `app/scoring.py`. Nothing about a
  score comes from the browser.
- **Lockout** is checked server-side against the stored kick-off time. A tip
  PUT after first bounce comes back `{"saved": 0, "rejected": [...]}` no matter
  what the frontend thinks.
- **Sealed tips.** `/state` omits other players' tips entirely until a game
  locks. They aren't hidden with CSS — they never leave the database.
- **Admin routes** re-check the admin flag on every request.

Times are sent as UTC and rendered in each viewer's local timezone, so a mate
in Perth sees the right first-bounce time.

## Deploying

The Dockerfile writes the database to `/data`, so mount a volume there or you
will lose everything on redeploy.

**Fly.io** (free-tier friendly, and it's the one I'd pick):

```bash
fly launch --no-deploy
fly volumes create tips_data --size 1
# in fly.toml:
#   [mounts]
#   source = "tips_data"
#   destination = "/data"
fly deploy
```

**Anything else** — Railway, Render, a $5 VPS behind Caddy — works the same
way. Two requirements: HTTPS (bearer tokens in plain HTTP are readable on cafe
wifi), and a persistent disk for the SQLite file.

`app/static/index.html` sends `noindex, nofollow`, but treat the link as
unlisted rather than secret: anyone you send it to can forward it.

## Manual adjustments

Admin → any game → **Manual adjustment**. Enter points against a player (negative
to deduct, 0 to clear) and a reason.

It never touches lodged tips — the tip stays exactly as it was, and the
adjustment is applied on top when the total is worked out. It's also public:
the reason appears under Ladder → Who tipped what, so nobody's score moves
without the group being able to see why and what for.

Adjustments work on games with no result yet, and one adjustment is stored per
player per game, so applying a second one replaces the first rather than
stacking.

## Backups

It's one file. `sqlite3 tips.db ".backup backup.db"` before each finals week is
plenty.

## Layout

```
app/
  main.py      routes, auth, SQLite schema
  scoring.py   the rules, and the only place points are worked out
  static/
    index.html the whole frontend
```

## Scoring rules

Defaults live in `DEFAULT_SCORING` in `app/scoring.py` and are editable per
comp from the Admin tab.

Every game: 2 for the winner, or 3 if they were the underdog (replaces the 2,
doesn't stack). Nothing for a draw. Margin pays 3 spot on, 2 within five, 1
within ten.

Tipped margins are bounded 1-200 — you can't tip a draw, since a draw scores
nothing under the rules either way. Results are bounded 0-200, because a drawn
game is a real outcome and 0 is how you record it.

A result is recorded as a winning side plus a margin — the raw team scores are
never asked for, because no rule uses them. A margin of 0 is a draw.

Margin is scored on the signed difference, so tipping Geelong by 10 when Sydney
win by 10 leaves you 20 out. There's a toggle to withhold margin points
entirely when the winner is wrong.

Opening game of each round only: 2 for the first goalkicker, 2 for the leading
goalkicker (1 if your pick came second), 2 for most disposals (1 for second).
Player names match on surname, so `Gawn`, `M Gawn` and `Max Gawn` agree — watch
out if two blokes in the same game share a surname.
