# Friends EverQuest Legends DPS Meter

A damage meter for EverQuest Legends: tails the game's `eqlog_*.txt` log
file and shows who's doing how much damage, healing, and damage taken, in
real time or replayed from history.

Requires only the Python standard library (`tkinter` included) — no pip
install, no other dependencies.

## Download

Get the code from GitHub:
**https://github.com/beanznrices/Friends-Everquest-Legends-DPS-Meter**

- **With git installed** (either OS):
  ```
  git clone https://github.com/beanznrices/Friends-Everquest-Legends-DPS-Meter.git
  ```
- **Without git**: on the repo page above, click the green **Code** button →
  **Download ZIP**, then extract it.

Either way, you end up with a folder named
`Friends-Everquest-Legends-DPS-Meter` containing `main.py`, `dpsmeter/`,
`tests/`, and this README. That whole folder is what gets placed as
described below — don't dig inside it and move individual files out.

## Where to put it

The meter finds the game's log files by looking at its own position on
disk: by default, it expects to be sitting **directly inside the game's
`Logs` folder** — the same folder that already contains files named
`eqlog_<charactername>_<server>.txt`.

**Windows** — the game is typically installed under the shared Public
profile, so the log folder is usually:

```
C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest Legends\Logs\
```

Place the whole `Friends-Everquest-Legends-DPS-Meter` folder directly
inside that `Logs` folder, so you end up with:

```
...\EverQuest Legends\Logs\Friends-Everquest-Legends-DPS-Meter\main.py
```

If your game is installed somewhere else, look for wherever your
`eqlog_*.txt` files actually are — that's the `Logs` folder you want.

**Linux (Lutris/Wine/Proton)** — the same Windows-side path exists inside
your Wine prefix's simulated `C:` drive. With a typical Lutris setup
that's something like:

```
~/Games/everquest-legends/drive_c/users/Public/Daybreak Game Company/Installed Games/EverQuest Legends/Logs/
```

The exact prefix path (the `~/Games/everquest-legends/` part) depends on
what your Lutris install script named it — if that doesn't exist, search
for `eqlog_` to find your actual `Logs` folder:

```
find ~ -iname "eqlog_*.txt" 2>/dev/null
```

Once you've found it, place the `Friends-Everquest-Legends-DPS-Meter`
folder directly inside it, same as on Windows.

**Prefer to keep it out of the game folder?** Put the project folder
anywhere you like (Desktop, `~/projects`, wherever) and set the
`EQ_LOG_DIR` environment variable to your actual `Logs` folder path
instead — the meter will use that and skip the position-on-disk guess
entirely. Everything below still applies; just export/set that variable
before launching.

## Setup

**Windows:**

1. Install Python from **https://www.python.org/downloads/** if you don't
   already have it. During setup, check **"Add python.exe to PATH"** on
   the first installer screen. The standard Windows installer already
   includes `tkinter` — no extra step needed for that.
2. That's it — no other install step. Nothing to build, no packages to
   fetch.

**Linux:**

1. Python 3 is already installed on almost every distro. `tkinter`
   sometimes isn't bundled by default and needs its own package:
   - Debian/Ubuntu: `sudo apt install python3-tk`
   - Fedora: `sudo dnf install python3-tkinter`
   - Arch/CachyOS/Manjaro: `sudo pacman -S tk`
2. Check it's there: `python3 -c "import tkinter"` — no error means
   you're set.

## Launching

**Windows** — open the `Friends-Everquest-Legends-DPS-Meter` folder and
either:
- Double-click `main.py` (if `.py` files are set to open with Python), or
- Open Command Prompt/PowerShell in that folder and run:
  ```
  python main.py
  ```
  (some installs use `py main.py` instead — try that if `python` isn't
  recognized).

For a one-click launcher, make a text file named `run.bat` in that same
folder containing:
```
@echo off
python main.py
pause
```
and double-click that instead — the `pause` keeps the window open if
something goes wrong, so you can read the error.

**Linux** — open a terminal in the `Friends-Everquest-Legends-DPS-Meter`
folder and run:
```
python3 main.py
```

On either OS, pass a different log directory as an argument instead of
relying on auto-detection or `EQ_LOG_DIR`:
```
python3 main.py "/path/to/your/Logs"
```

## Using it as an overlay while playing

The meter only ever opens the log file for reading — no memory access, no
injection into the game process, no packet capture. That's the standard
approach every EverQuest log parser has used for two decades, and this
specific game already has an established community ecosystem of tools
built the same way (EQL Meter, EQLogParser, Loadout Legends) — none of
that is a guarantee about any particular server's rules, but it's the
right category to be in.

To run it overlaid on top of the game:

1. Check **Always on top** — the meter's own window will now stay above
   other windows.
2. In EQ Legends' display settings, use **Windowed** or **Borderless
   Windowed**, not exclusive Fullscreen — exclusive fullscreen bypasses
   the desktop compositor entirely, so no other window (this one
   included) can render above it.
3. Drag the **Opacity** slider down if you want to see the game through
   it.
4. If "Always on top" doesn't stick (seen occasionally through XWayland
   on some Wayland compositors), your window manager's own "keep above"
   rule is more reliable than any app-level hint: on KDE Plasma, System
   Settings → Window Management → Window Rules → New, match the window
   by title, and force **Keep above other windows** to Yes.

There's no click-through — clicks on the meter go to the meter, not the
game behind it — so the practical move (same as every classic-EQ parser
window) is sizing and parking it in a corner that doesn't sit over
anything you click on.

## Combatant model

Every combatant — you, your pet, each party member — is tracked by name in
one dict, so a full raid group scales for free instead of capping out at
a hardcoded set of slots. The table (`ttk.Treeview`) is sortable by
clicking any column header, and clicking a row drives a per-ability (or
per-target) breakdown panel below it.

Parsing covers the full range of melee/combat-art verbs (`hit`, `slash`,
`cleave`, `bash`, `pierce`, `crush`, `attack`, `kick`, `punch`, `backstab`,
`smite`, `shoot`, `strike`, `claw`, `gore`, `sting`, `bite`, `maul`,
`rend`, `gouge`, `frenzy`), damage-shield/thorns lines (phrased backwards
from a normal swing: `"<target> is burned by YOUR flames for N points..."`),
and crit tags (`(Critical)` / `(Lucky)` / `(Flurry)`) alongside spell-name
attribution.

`Track others` scopes to *players*, not monsters — player names in this
log format are always a single token; mob names virtually always aren't,
which is what the toggle keys off rather than a curated name list.

Full-history replay and live tailing compute identical DPS for identical
encounters, because both use the log's own timestamp, not wall-clock time
— see **Timing**, below.

## Party status and charmed pets

Party status (`Status: In Group` / `Solo`) is driven primarily by the
XP-gain line — `You gain party experience!` vs `You gain experience!` —
because that's the one signal that's correct even if the meter started
watching the log after the group had already formed (the explicit
`invites you to join a group.` / `has joined the group.` lines are also
used to populate *who's* in the group, but aren't required for the
Solo/In Group status itself to be right).

Pets, charmed or permanent, are tracked per-owner — **yours or a party
member's**, never a stranger's. A charm confirmation (`<mob> has been
charmed.`) is attributed to whichever of "you" or a current party member
most recently began a charm-type cast (`You begin casting <charm spell>.`
or, third person, `<name> begins casting <charm spell>.`) within the last
10 seconds; failed casts (interrupted/resisted/target too high level)
clear that window immediately rather than leaving it open. The moment
`Your <charm> spell has worn off of <mob>.` appears, that name stops being
tracked. The same owner resolution applies to the permanent "/pet leader"
trick (`<pet> says, 'My leader is <name>.'`) — it's attributed to whoever
`<name>` actually is (you, a party member, or ignored if it's neither).
Every pet row is pinned directly under "You" in the table and labeled
`<mob name> (<owner>)`.

Evacuate/Succor/Exodus (matched by keyword, so every rank and zone-specific
variant — `Lesser Evacuate IV`, `Evacuate: North Karana`, `Greater Succor`
— is covered without an exact-name list) end any currently-charmed pet:
casting one of these ports the whole group, and a charmed pet isn't a real
group member, so it gets left behind. The trigger is the *confirmed*
zone transition (`You have entered <zone>.`) following the cast, not the
cast itself — a real captured evac took 23 seconds from cast-begin to that
confirmation (cast time, then a `LOADING, PLEASE WAIT...` screen), so the
window is generous (60s) on purpose. If the cast is interrupted or
cancelled — this log format uses the same "spell is interrupted" message
for both — no confirming zone line ever follows, so the pet is untouched.
Permanent pets aren't affected either way; they zone with you.

**Known limitation:** a charmed or summoned pet keeps its ordinary in-game
name, and the EQ log has no per-instance ID. If two pets — or a pet and a
same-species hostile mob — share the exact same name while both are active
at once, damage lines for them are textually indistinguishable, and the
most recent "has been charmed."/"My leader is" line simply wins for that
name going forward. This is a ceiling on what any text-log parser can
know, not a bug to fix.

## Healing, damage taken, and per-target breakdown

The table has a **Damage / Healing / Taken** switch (under "Metric:") —
same sortable table, same click-a-row-for-a-breakdown panel, just fed a
different one of `Segment.damage` / `.healing` / `.taken`. All three use
the exact same `CombatantStats` shape, so nothing about the table or
breakdown panel is duplicated per metric.

- **Healing** comes from `"<healer> healed <target> for N (M) hit points by
  <spell>."` — N is the effective heal; the parenthesized M (when present)
  is overheal-inclusive and isn't what matters for HPS, so it's discarded.
- **Damage taken**: `"a mob hits YOU for N points"` starts and feeds an
  encounter just like outgoing damage does — you don't have to land the
  first hit for the meter to know you're in a fight. (Reflexive targets
  like "itself" stay excluded — that's not damage taken by anything
  tracked.)
- The breakdown panel under the table also has a **By Ability / By
  Target** toggle — for damage dealt, "by target" shows which mob you hit;
  for damage taken, which mob hit you.

## General buff tracking

Separate from charm — the AA that extends buff duration applies to
*beneficial* spells (buffs on you/allies), which charm structurally isn't
(it targets a hostile). Every spell you cast (`You begin casting
<spell>.`) that the game's own spell data confirms has a real duration is
tracked until a matching wear-off line resolves it.

- **Duration is learned from your own log, not looked up.** The first time
  a spell goes cleanly from cast to wear-off, that elapsed time (normalized
  by whatever AA bonus was active at the time) becomes its known base
  duration. Every later cast of that same spell gets a live countdown from
  it. A wiki/tool-scraping approach was considered and rejected for the
  actual *numbers*: eqlwiki's spell pages show only a flat base-tier
  duration, and the community's own tier-scaling tools explicitly caveat
  that they don't treat a wiki number as ground truth. Measuring it
  directly from a confirmed cast→wear-off pair sidesteps that uncertainty
  entirely, at the cost of a spell being unknown ("learning…") the first
  time you ever cast it.
- **Whether a cast is even trackable at all is answered by the game's own
  data, not a guess.** `dpsmeter/spelldata.py` reads `spells_us.txt` /
  `spells_us_str.txt` — the actual client spell files, sitting in the game
  install root — and checks the buff-duration formula/value fields (both
  `0` only for a true instant effect). A damage nuke produces a
  cast-begin line same as any buff, but never a wear-off line, so without
  this check it would sit in the Active list forever as "learning…".
- **Custom "wears off" text is recognized too, not just the generic
  template.** Plenty of spells don't say `"Your <spell> spell has worn
  off."` at all — they define their own flavor text in
  `spells_us_str.txt`'s SPELLGONE column (`"The echo of healing fades
  away."`, `"Your valor fades."`), which no generic pattern can match.
  These are looked up directly from the same file, joined to the cast by
  spell ID. Ambiguous on their own — a whole spell line (e.g. Echo of
  Health → Celestial Echo → Sacred Echo) frequently shares the exact same
  fade text — so the match is resolved against whichever of those spells
  is actually pending right now, not a fixed guess.
- **Spell Casting Reinforcement** (the AA that extends beneficial-spell
  duration) is auto-detected from `/alt list` output in the log: the
  `Description:` line for it already resolves to your *current* rank's
  actual percentage (`"...cast by 50%."`), not a generic per-rank list, so
  the percentage is read directly rather than mapped from a hardcoded rank
  table. A dropdown (`Auto (from /alt list)` / `None` / `Rank 1`–`4`) can
  override it, e.g. before you've ever run `/alt list` in this log.
- **What fell off** — two views, both fed by the same learned-duration
  data: an **Active** list sorted soonest-to-expire, and a **What fell
  off** history sorted by shortest actual lifespan, showing actual vs.
  expected duration side by side so a buff that dropped early (dispelled,
  or something else knocked it off) stands out. A buff you manually
  right-click to remove shows up here exactly like a natural expiry — same
  underlying message, same code path. If the game refuses the removal
  (`"You cannot remove this effect."`), nothing changes, correctly: the
  buff is still actually active.
- Falls back gracefully if `spells_us.txt` isn't found (different install
  layout, files moved, etc.) — duration checks default to "unknown, don't
  filter" and fade-message lookups just miss, rather than breaking.

## Charm-break audio alert

A single fixed beep (`dpsmeter/assets/charm_break.wav`, generated with
pure-stdlib `wave` — no audio library dependency), played by shelling out
to `paplay`/`pw-play`/`aplay`/`ffplay`/`mpv`, whichever is found first —
fails silently if none are available. Scoped to **your own** charm only
(not a party member's), and does nothing unless the "🔔 Charm break
alert" checkbox is on.

## Timing

Every combat line carries its own EQ timestamp
(`[Tue Sep 08 15:45:50 2026]`), and encounter start/end are always that
log-clock value, not `time.time()`. That's what makes a full-history
replay report the exact same DPS as watching the same fight live —
replaying processes the whole log in a fraction of a second, so measuring
elapsed time in real wall-clock seconds during a replay would make every
encounter look instantaneous. See
`tests/test_state.py::test_replay_uses_log_clock_for_duration_not_wallclock`.

The same live-vs-replay split applies to the Buffs panel's countdowns:
live mode uses real wall-clock time, history/replay mode uses the log's
own last-seen timestamp — otherwise every buff in a replayed session reads
as expired the instant "now" (today, in real life) is compared against a
`cast_ts` from a log that might be days old.

## Layout

```
dpsmeter/
  parser.py     regex → Event parsing (one lazy read of spelldata for fade-text
                lookups; otherwise no I/O — unit tested directly)
  spelldata.py  reads the game's own spells_us.txt / spells_us_str.txt for
                buff-vs-instant classification and custom fade-message text
  model.py      Segment / CombatantStats / AbilityStats (damage, healing, taken)
  state.py      turns a line stream into encounters; classification; replay;
                pet ownership; buff-duration learning; AA bonus
  watcher.py    tails the newest (or pinned) eqlog_*.txt, tolerant of rotation
  gui.py        tkinter front end
  audio.py      charm-break alert playback (shells out, no audio dependency)
  config.py     theme + tunables + tiny json config persistence
  assets/       charm_break.wav
tests/          unit tests, most built from real captured log lines
main.py         entry point
```

## Tests

```
python3 -m unittest discover -s tests
```
