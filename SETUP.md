# Setup, start to finish

**On Windows? Use `SETUP-WINDOWS.md` instead.** This file assumes bash.

Zero to mates-are-tipping. Parts 1 and 3 are the minimum if you just want it
running on your own machine. Part 2 is what puts it on the internet.

Rough timings: Part 1 is ten minutes, Part 2 is twenty the first time, Part 3
is five minutes per round.

---

## Before you start

- **Python 3.11 or newer.** Check with `python3 --version`.
- **Decide your three passwords now** and put them somewhere you'll find them
  again:
  - your own player password
  - the **join password** you give your mates
  - the **admin password** you give nobody
  There is no password reset. Losing the admin password means editing SQLite by
  hand.
- **For Part 2 only:** a Fly.io account with a card on file, and Docker is *not*
  required — Fly builds remotely.

---

## Part 1 — Get it running on your laptop

**1. Unpack and enter the project.**

```bash
cd ~/projects            # or wherever you keep things
unzip afl-finals-tips.zip
cd afl-finals-tips
```

**2. Make a virtualenv.** Skip this and you'll be installing into system
Python, which Debian and Ubuntu will refuse outright.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**3. Install the two dependencies.**

```bash
pip install -r requirements.txt
```

**4. Start the server.**

```bash
uvicorn app.main:app --reload
```

Leave that terminal running. `--reload` restarts on file changes, which you
want while you're poking at it and don't want in production.

**5. Open http://127.0.0.1:8000.**

You should get the dark green "Start a comp" card. If you get a connection
error, check the terminal — uvicorn prints the port it actually bound to.

**6. Create a throwaway comp to test with.** Not your real one. Use obvious
junk: name it "Test", passwords `testtest` for all three.

You'll land on `/c/<code>`. Copy that code somewhere — it's the only way back
in.

**7. Prove the three things that matter before you trust it with real tips.**

- **Locking works.** Admin → Add a round → Add a game → set First bounce to
  five minutes ago → save. Go to Tips. The game should read *Locked* and the
  buttons should be dead.
- **Scoring works.** Set the bounce back to next week, tip a side with a
  margin, then Admin → enter a result → back to Tips. The breakdown under the
  game should show each line and the total.
- **Tips stay sealed.** Open a private window, go to the same `/c/<code>`, join
  as a second name, lodge a different tip. Neither account should see the
  other's pick under Ladder → Who tipped what until the game locks.

If all three behave, stop the server with `Ctrl-C` and delete the test
database: `rm tips.db`.

> Stopping here is a legitimate choice — but your mates can only reach
> `127.0.0.1` if they're on your wifi and you leave the laptop open. For a real
> comp, keep going.

---

## Part 2 — Put it on the internet

Fly.io, because SQLite needs a real disk and Fly gives you one cheaply. Expect
a couple of dollars a month for a machine this small — check
[fly.io/pricing](https://fly.io/pricing) for current rates, and set a spend
limit in the dashboard if you want a hard ceiling.

**1. Install flyctl and sign in.**

```bash
curl -L https://fly.io/install.sh | sh
fly auth signup      # or: fly auth login
```

**2. Generate the app config, but don't deploy yet.**

```bash
cd afl-finals-tips
fly launch --no-deploy
```

It'll ask a few questions. Pick a name (that becomes
`your-name.fly.dev`), choose **Sydney (syd)** as the region since you're in
Adelaide, and say **no** to any offer of a Postgres or Redis database — you
don't need one.

**3. Create the volume.** In the same region as the app, or the machine won't
be able to attach it.

```bash
fly volumes create tips_data --size 1 --region syd
```

One gigabyte is enormous for this. The database is a few hundred kilobytes.

**4. Edit `fly.toml`.** Fly just generated it. Two things must be true —
compare against `fly.toml.example` in this repo:

- `internal_port = 8000` under `[http_service]`
- a `[mounts]` block:

```toml
[mounts]
  source = "tips_data"
  destination = "/data"
```

**This block is the whole ballgame.** Without it the SQLite file lands on the
machine's root filesystem, which Fly throws away on every deploy. You'd lose
the comp mid-finals and only find out when someone asked why the ladder was
empty.

**5. Pin it to one machine.** Two machines means two separate SQLite files
silently disagreeing with each other.

```bash
fly scale count 1
```

**6. Deploy.**

```bash
fly deploy
```

**7. Confirm the volume actually mounted.** Do not skip this.

```bash
fly ssh console -C "ls -la /data"
```

You want to see `tips.db` in there after you've created your comp. If `/data`
is empty or missing, the mount didn't take — fix `fly.toml` and redeploy before
anyone lodges a tip.

**8. Open it.**

```bash
fly open
```

HTTPS is on by default via `force_https`, which matters: the app authenticates
with bearer tokens, and those are readable in plain text over open wifi.

---

## Part 3 — Set up the real comp

**1. On the live URL, create your comp for real.** Use the three passwords you
picked at the top.

**2. Copy the secret link.** Admin tab, top of the page, **Copy link**. It
looks like `https://your-app.fly.dev/c/aB3xK9_mQ2p`. That's the only way in —
there's no directory, no search, no listing.

**3. Add the round.** Admin → Add a round → name it "Week 1" or "Finals Week
1".

**4. Add each game.** For every final:

| Field | What goes in it |
|---|---|
| Home / Away | Start typing, all 18 clubs autocomplete |
| Label | "Qualifying Final", "Elimination Final" — cosmetic |
| First bounce | **In your own time.** Enter Adelaide time; each mate sees it converted to theirs |
| Favourite | Whoever is shorter on Sportsbet head-to-head. This is what makes the other side worth 3 |
| Opening game | **Yes** on exactly one game per round |

The first-bounce time is not cosmetic — it's what the server checks when it
decides whether to accept a tip. Get it wrong and tipping either closes early
or stays open after the ball's up.

**5. Sanity-check the Rules tab.** It renders from the live scoring values, so
if it reads wrong, the scoring is wrong. Fix it in Admin → Scoring before
anyone tips.

---

## Part 4 — Bring the mates in

Send them three things in the one message:

1. the secret link
2. the join password
3. "pick any name and any password, that's your login"

What they do: open the link → type a name and a password → **Join**. Coming
back later, same details → **Sign in**. They can use their phone and their
laptop at once; sessions are independent.

Two things worth saying out loud in the group chat:

- **Names are permanent-ish.** Whoever grabs "Bob" first owns it.
- **Forgotten passwords need you.** There's no reset flow. You'd be deleting
  their row from SQLite by hand, which is a job.

---

## Part 5 — The match-day routine

Once per game, after the siren:

1. Admin tab → find the game → pick **Won by** and enter the **margin**. A
   margin of 0 is a draw, whichever side is showing.
2. On the opening game only, also enter first goalkicker, top two goalkickers,
   and top two disposal-getters. **Surnames are enough** — Gawn, M Gawn and Max
   Gawn all match.
3. **Save result.**

The ladder recalculates immediately, and that game's tips unseal for everyone.

Got it wrong? **Clear result**, fix it, save again. Points are recomputed from
scratch on every read, so there's nothing stale to clean up.

Got the rules wrong rather than the result? Admin → the game → **Manual
adjustment**. Points against a player, plus a reason everyone can see. Tips
stay untouched.

**Lock all tipping** in Admin is your manual override — useful if a game gets
rescheduled and you want everything frozen while you sort the times out.

---

## Part 6 — Back it up

One file, one command. Worth doing before each finals week.

```bash
fly ssh console -C "sqlite3 /data/tips.db '.backup /data/backup.db'"
fly sftp get /data/backup.db ./tips-backup-$(date +%F).db
```

If `sqlite3` isn't on the image, `fly sftp get /data/tips.db` works too — just
do it when nobody's mid-write.

---

## When it goes wrong

**Buttons do nothing, page looks half-built.** Open devtools console. A JS
error means the page didn't finish loading; a 401 in the network tab means your
session expired — sign out via your name in the top right and back in.

**"Sign in to continue" on every action.** Your bearer token is stale. Clear it
by signing out, or clear the site's local storage.

**Ladder shows everyone on zero.** Expected until you enter results. Points
only exist once a game has a score.

**Someone's tip didn't save.** Check the response — the server returns
`{"saved": 0, "rejected": [{"game_id": 3, "why": "locked"}]}` if it arrived
after first bounce. That's the system working. If the bounce time was wrong,
fix it in Admin and they can re-tip.

**Everything vanished after a deploy.** The `[mounts]` block is missing or the
volume didn't attach. `fly ssh console -C "ls -la /data"` to confirm, then
restore from your backup.

**Deploy fails on the build.** `fly logs` shows the build output. Most common
cause is a typo in `fly.toml` — TOML is fussy about the `[section]` headers.

---

## What you'd want to add before next season

Honest gaps, in the order I'd fix them:

1. **Password reset.** The one that'll actually bite you. An admin button that
   clears a player's hash and lets them set a new one.
2. **A "you haven't tipped yet" nudge** before lockout. Right now a mate who
   forgets just silently scores zero.
3. **Ties on the bonuses.** Two players level on goals is currently the admin's
   judgement call, entered by hand.
