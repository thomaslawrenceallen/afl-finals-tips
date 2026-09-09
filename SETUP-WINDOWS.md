# Setup on Windows

The same recipe as `SETUP.md`, written for Windows instead of bash. Follow this
one, not that one.

**Use PowerShell, not Command Prompt.** Windows key, type `powershell`, hit
enter. The Fly CLI installer needs it, and it handles paths with spaces without
a fight.

---

## Before you start

**Check Python.**

```powershell
py --version
```

You want 3.11 or newer. If that opens the Microsoft Store instead of printing a
version, you don't have real Python — get it from
[python.org](https://www.python.org/downloads/) and **tick "Add python.exe to
PATH"** on the first screen of the installer.

Use `py` rather than `python` throughout. The bare `python` command on Windows
is often a Store stub that does nothing useful.

**Decide your three passwords now** and save them somewhere you'll find them
again:

- your own player password
- the **join password** you give your mates
- the **admin password** you give nobody

There is no password reset. Losing the admin password means editing SQLite by
hand.

**A note on OneDrive.** Keeping the code there is fine and gets you free
backup. Keeping the *virtualenv* and the *database* there is not — the venv is
thousands of small files OneDrive will thrash trying to sync, and OneDrive can
take a file lock mid-write, which SQLite does not tolerate. The steps below
deliberately put both outside OneDrive.

---

## Part 1 — Running on your machine

**1. Unpack it.** Windows has no `unzip`; `tar` is built in and reads zips.

```powershell
cd "$env:USERPROFILE\OneDrive\Documents\projects"
tar -xf "$env:USERPROFILE\Downloads\afl-finals-tips.zip"
cd afl-finals-tips
```

Explorer's right-click → **Extract All** does the same job if you'd rather.

**2. Make a virtualenv, outside OneDrive.**

```powershell
py -m venv "$env:USERPROFILE\venvs\afltips"
& "$env:USERPROFILE\venvs\afltips\Scripts\Activate.ps1"
```

Your prompt should now start with `(afltips)`.

If PowerShell refuses with a script-execution error, allow local scripts for
your account — this is a per-user setting and doesn't touch system policy:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Then run the activate line again.

**3. Install the dependencies.**

```powershell
pip install -r requirements.txt
```

**4. Point the database outside OneDrive, and start the server.**

```powershell
$env:TIPS_DB = "$env:USERPROFILE\afl-tips-local.db"
uvicorn app.main:app --reload
```

`$env:TIPS_DB` only lives as long as that PowerShell window, so set it each
time. To make it stick permanently:

```powershell
[Environment]::SetEnvironmentVariable("TIPS_DB", "$env:USERPROFILE\afl-tips-local.db", "User")
```

Leave the server running and open a **second** PowerShell window for anything
else. `Ctrl-C` stops it.

**5. Open http://127.0.0.1:8000.**

You should get the dark green "Start a comp" card.

**6. Create a throwaway comp.** Not your real one — obvious junk, `testtest`
for all three passwords. You'll land on `/c/<code>`; that code is the only way
back in.

**7. Prove the three things that matter** before you trust it with real tips:

- **Locking works.** Admin → Add a round → Add a game → set First bounce to
  five minutes ago → save. Go to Tips. The game reads *Locked* and the buttons
  are dead.
- **Scoring works.** Set the bounce to next week, tip a side with a margin,
  then Admin → enter a result → back to Tips. The breakdown shows each line and
  the total.
- **Tips stay sealed.** Open an InPrivate window, go to the same `/c/<code>`,
  join as a second name, tip differently. Neither account sees the other's pick
  under Ladder → Who tipped what until the game locks.

**8. Clean up the test data.**

```powershell
del "$env:USERPROFILE\afl-tips-local.db"
```

---

## Part 2 — Putting it online

Fly.io, because SQLite needs a real disk. Expect a couple of dollars a month
for a machine this small — check [fly.io/pricing](https://fly.io/pricing) for
current rates and set a spend limit in the dashboard if you want a hard
ceiling.

**1. Install flyctl.**

```powershell
iwr https://fly.io/install.ps1 -useb | iex
```

**Close and reopen PowerShell** so the new PATH takes effect, then:

```powershell
fly version
fly auth signup     # or: fly auth login
```

If `fly` still isn't found after reopening, the installer prints the install
path — add it to PATH manually via Settings → System → About → Advanced system
settings → Environment Variables.

**2. Generate the config, don't deploy yet.**

```powershell
cd "$env:USERPROFILE\OneDrive\Documents\projects\afl-finals-tips"
fly launch --no-deploy
```

Pick a name (becomes `your-name.fly.dev`), choose **Sydney (syd)** as the
region, and say **no** to any offer of Postgres or Redis.

**3. Create the volume**, same region as the app.

```powershell
fly volumes create tips_data --size 1 --region syd
```

One gigabyte is enormous for this. The database is a few hundred kilobytes.

**4. Edit `fly.toml`.** Notepad is fine, or your editor of choice. Two things
must be true — compare against `fly.toml.example` in this folder:

- `internal_port = 8000` under `[http_service]`
- a `[mounts]` block:

```toml
[mounts]
  source = "tips_data"
  destination = "/data"
```

**This block is the whole ballgame.** Without it the database lands on the
machine's root filesystem, which Fly discards on every deploy. You'd lose the
comp mid-finals and only find out when someone asked why the ladder was empty.

**5. Pin it to one machine.** Two machines means two SQLite files quietly
disagreeing.

```powershell
fly scale count 1
```

**6. Deploy.**

```powershell
fly deploy
```

**7. Confirm the volume mounted.** Do not skip this.

```powershell
fly ssh console -C "ls -la /data"
```

After you create your comp you want to see `tips.db` in there. If `/data` is
empty or missing, the mount didn't take — fix `fly.toml` and redeploy *before*
anyone lodges a tip.

**8. Open it.**

```powershell
fly open
```

HTTPS is on by default, which matters — the app authenticates with bearer
tokens, and those are readable in plain text over open wifi.

---

## Part 3 — Setting up the real comp

**1. On the live URL, create your comp** with the three real passwords.

**2. Copy the secret link.** Admin tab, top of the page, **Copy link**. Looks
like `https://your-app.fly.dev/c/aB3xK9_mQ2p`. That's the only way in — no
directory, no search, no listing.

**3. Add the round.** Admin → Add a round.

**4. Add each game:**

| Field | What goes in it |
|---|---|
| Home / Away | Start typing, all 18 clubs autocomplete |
| Label | "Qualifying Final", "Elimination Final" — cosmetic |
| First bounce | **Adelaide time.** Each mate sees it converted to theirs |
| Favourite | Whoever is shorter on Sportsbet head-to-head. This is what makes the other side worth 3 |
| Opening game | **Yes** on exactly one game per round |

First bounce is not cosmetic — it's what the server checks when deciding
whether to accept a tip.

**5. Check the Rules tab.** It renders from the live scoring values, so if it
reads wrong, the scoring is wrong.

---

## Part 4 — Bringing the mates in

Send three things in one message: the secret link, the join password, and "pick
any name and any password, that's your login."

They open the link, type a name and password, hit **Join**. Coming back later,
same details, **Sign in**. Phone and laptop can both stay signed in.

Two things worth saying in the group chat:

- **Names are first-come.** Whoever grabs "Bob" owns it.
- **Forgotten passwords need you.** No reset flow — you'd be editing SQLite by
  hand.

---

## Part 5 — Match-day routine

Once per game, after the siren:

1. Admin → find the game → pick **Won by** and enter the **margin**.
2. On the opening game only, also enter first goalkicker, top two goalkickers,
   top two disposal-getters. **Surnames are enough.**
3. **Save result.**

The ladder recalculates immediately and that game's tips unseal.

Got it wrong? **Clear result**, fix, save again. Points are recomputed from
scratch on every read, so there's nothing stale left behind.

Got the rules wrong rather than the result? Admin → the game → **Manual
adjustment**. Points against a player, plus a reason everyone can see. Tips
stay untouched.

**Lock all tipping** in Admin is your manual override for reschedules.

---

## Part 6 — Backups

Before each finals week:

```powershell
fly ssh console -C "sqlite3 /data/tips.db '.backup /data/backup.db'"
fly sftp get /data/backup.db ".\tips-backup-$(Get-Date -Format yyyy-MM-dd).db"
```

If `sqlite3` isn't on the image, `fly sftp get /data/tips.db` works too — do it
when nobody's mid-write.

---

## When it goes wrong

**`'unzip' is not recognized`** — Windows has no `unzip`. Use `tar -xf`, or
Explorer's Extract All.

**`'python' is not recognized`, or the Store opens** — use `py` instead, or
reinstall Python with "Add to PATH" ticked.

**`cannot be loaded because running scripts is disabled`** — see the
`Set-ExecutionPolicy` line in Part 1 step 2.

**`'fly' is not recognized` after installing** — you didn't reopen PowerShell.

**`cd` seems to do nothing across drives** — that's Command Prompt behaviour,
where you need `cd /d`. PowerShell doesn't have this problem.

**Buttons do nothing, page half-built** — open devtools (F12) console. A JS
error means the page didn't finish loading; a 401 in the network tab means your
session expired, so sign out via your name top-right and back in.

**Ladder shows everyone on zero** — expected until you enter results.

**A tip didn't save** — the server returns
`{"saved": 0, "rejected": [{"game_id": 3, "why": "locked"}]}` if it arrived
after first bounce. That's the system working. Wrong bounce time? Fix it in
Admin and they can re-tip.

**Everything vanished after a deploy** — the `[mounts]` block is missing or the
volume didn't attach. `fly ssh console -C "ls -la /data"` to confirm, then
restore from backup.

**Deploy fails on the build** — `fly logs` shows the output. Usually a typo in
`fly.toml`; TOML is fussy about `[section]` headers.
