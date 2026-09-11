"""Turns a stream of parsed log events into encounters (Segments).

Timing note: every damage line carries its own EQ timestamp. Encounter
boundaries and durations are computed from that log-clock, not
`time.time()`. Driving everything off the log's own clock — instead of
wall-clock time — is what makes a full-history replay report the same DPS
as watching it live: replaying processes the whole log in a fraction of a
second, so measuring elapsed time in real wall-clock seconds during a
replay would make every encounter look instantaneous.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from . import parser, spelldata
from .model import Segment
from .watcher import LogWatcher

RESET_INACTIVITY = 15.0  # seconds of no new damage before an encounter closes
MAX_HISTORY = 200

# How long after "You begin casting <charm spell>." a "<mob> has been
# charmed." confirmation still counts as *your* charm. The confirmation line
# doesn't name the caster, so this is a best-effort window, not a guarantee —
# see the note above _CHARM_SPELL_RE in parser.py.
CHARM_CONFIRM_WINDOW = 10.0

# Same idea for group escape spells (Evacuate/Succor/Exodus). These run much
# longer than charm: a real captured cast took 23 seconds from "You begin
# casting Lesser Evacuate IV." to the confirming "You have entered <zone>."
# — cast time, then "creates a mystic portal.", then a "LOADING, PLEASE
# WAIT..." screen of unpredictable length. Generous on purpose: missing a
# real evac (false negative) silently leaves a stale charmed pet in the
# table, which is the exact bug this feature exists to fix.
ESCAPE_CONFIRM_WINDOW = 60.0

# "Spell Casting Reinforcement" — the AA that extends the duration of
# beneficial spells you cast. Its /alt-list description already resolves to
# your *current* rank's actual percentage ("...cast by 50%."), not a generic
# per-rank list, so we read the number directly rather than mapping a rank
# index ourselves — one fewer hardcoded table that could drift from the
# server's real numbers.
_SCR_ABILITY_NAME = "spell casting reinforcement"
_SCR_PERCENT_RE = re.compile(r"beneficial spells that you cast by (?P<percent>\d+)%", re.IGNORECASE)

# EQ player names are always a single token (letters, optional backtick/apostrophe
# for names like "L`Rei"), no spaces or leading article. Mob names are almost
# always multi-word ("a sturdy skeleton", "skeletal excavator") or start with
# "a "/"an "/"the ". Used so "track others" means "other players", not every
# monster that happens to land a hit on someone.
_PLAYER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'`]*$")
_LEADING_ARTICLE_RE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)


def _looks_like_player(name: str) -> bool:
    return bool(_PLAYER_NAME_RE.match(name.strip()))


@dataclass
class _PetOwnership:
    display_name: str
    owner: str  # "You" or a party member's literal name
    kind: str  # "permanent" (leader-trick) or "charm" — only "charm" entries
    #             get released when a group escape spell completes


@dataclass
class ExpiredBuff:
    """One completed cast-to-wear-off observation, for the 'what fell off'
    history. `expected_duration` is None until we've learned this spell's
    base duration at least once (from an earlier, unrelated cast)."""
    spell: str
    cast_ts: float
    end_ts: float
    actual_duration: float
    expected_duration: Optional[float]


class MeterState:
    def __init__(self, track_others: bool = False):
        self.track_others = track_others

        # lowered actor name -> ownership, covering both permanent pets (the
        # "/pet leader" trick) and currently-charmed mobs, for you AND for
        # party members. Charm entries are removed the moment the charm ends
        # (or breaks) instead of sticking forever like a permanent pet would.
        self.owned_pets: Dict[str, _PetOwnership] = {}
        self._pending_charm_casts: Dict[str, float] = {}  # caster -> deadline (log-clock)
        self._pending_escape_casts: Dict[str, float] = {}  # caster -> deadline (log-clock)
        # Log-clock timestamps of when *your own* charm ended (naturally or
        # via a group escape) — the GUI watches this list's length to fire
        # the charm-break audio alert without polling individual events.
        self.charm_loss_events: List[float] = []

        # The log-owning character's real name (from the log filename, set by
        # the GUI) — needed to tell "your pet said its leader is you" apart
        # from "a party member's identically-named pet said its leader is
        # them", since the leader-trick text is visible to anyone nearby.
        self.character_name: Optional[str] = None

        self.segments: List[Segment] = []
        self.active: Optional[Segment] = None
        self._next_index = 1

        self.last_activity_ts: Optional[float] = None  # log-clock
        self.last_wall_update: Optional[float] = None  # wall-clock, live-mode staleness only

        self.matched_lines = 0
        self.lines_seen = 0

        self.party_members: Set[str] = set()
        self.pending_inviter: Optional[str] = None
        self.in_group = False

        # General buff tracking (separate from charm — the AA below extends
        # *beneficial* spells cast on you/allies, not charm, which targets a
        # hostile). Scoped to spells "You" cast, since that's the only cast
        # form phrased in first person and the AA bonus is inherently yours.
        # spell(lower) -> (cast_ts, aa_bonus_fraction_active_at_cast_time)
        self.active_buffs: Dict[str, tuple] = {}
        self.buff_base_durations: Dict[str, float] = {}  # spell(lower) -> learned base seconds, AA-normalized
        self.buff_display_names: Dict[str, str] = {}  # spell(lower) -> original-case name
        self.expired_buffs: List[ExpiredBuff] = []

        self.aa_bonus_detected: Optional[float] = None  # fraction, parsed from /alt list output
        self.aa_bonus_override: Optional[float] = None  # fraction, from the dropdown; None means "Auto"
        self._pending_ability_name: Optional[str] = None

        self.lock = threading.RLock()

    # ── world-state helpers ─────────────────────────────────────────────

    def set_character_name(self, name: str):
        self.character_name = name.strip() or None

    def _is_party_member(self, name: str) -> bool:
        name = name.strip()
        return name in self.party_members or name.lower() in {p.lower() for p in self.party_members}

    def _resolve_owner(self, name: str) -> Optional[str]:
        """Whose pet is this, given a name from the leader-trick line? Prefer
        a matched party member; otherwise assume it's yours unless we
        positively know your own name and it doesn't match (in which case
        it's a stranger's — not tracked)."""
        name = name.strip()
        if self._is_party_member(name):
            return name
        if self.character_name is not None and name.lower() != self.character_name.lower():
            return None
        return "You"

    def _owner_label(self, owner: str) -> str:
        return self.character_name if owner == "You" and self.character_name else owner

    def register_pet(self, pet: str, master: str):
        owner = self._resolve_owner(master)
        if owner is None:
            return
        clean = pet.strip()
        display = _LEADING_ARTICLE_RE.sub("", clean).strip() or clean
        self.owned_pets[clean.lower()] = _PetOwnership(display_name=display, owner=owner, kind="permanent")

    def _begin_charm_cast(self, caster: str, ts: Optional[float]):
        if caster != "You" and not self._is_party_member(caster):
            return  # a stranger casting charm nearby isn't tracked
        deadline = (ts if ts is not None else time.time()) + CHARM_CONFIRM_WINDOW
        self._pending_charm_casts[caster] = deadline

    def _apply_charmed(self, mob: str, ts: Optional[float]):
        ts = ts if ts is not None else time.time()
        candidates = sorted(
            ((deadline, caster) for caster, deadline in self._pending_charm_casts.items() if ts <= deadline)
        )
        if not candidates:
            return
        _, caster = candidates[-1]  # most recently begun still-valid cast
        clean = mob.strip()
        self.owned_pets[clean.lower()] = _PetOwnership(display_name=clean, owner=caster, kind="charm")
        self._pending_charm_casts.pop(caster, None)

    def _apply_charm_end(self, mob: str, ts: Optional[float] = None):
        owned = self.owned_pets.pop(mob.strip().lower(), None)
        if owned is not None and owned.owner == "You":
            self.charm_loss_events.append(ts if ts is not None else time.time())

    def _apply_charm_cast_failed(self):
        # Failure messages ("interrupted"/"resisted"/"too high level") are
        # only ever phrased in the first person, so we can only clear our own
        # pending cast this way; a party member's failed cast just expires
        # naturally at the end of its window instead.
        self._pending_charm_casts.pop("You", None)

    def _begin_escape_cast(self, caster: str, ts: Optional[float]):
        if caster != "You" and not self._is_party_member(caster):
            return  # someone else's evac, unrelated to your group
        deadline = (ts if ts is not None else time.time()) + ESCAPE_CONFIRM_WINDOW
        self._pending_escape_casts[caster] = deadline

    def _apply_escape_cast_failed(self):
        # Same first-person-only limitation as charm: we can only see our own
        # interrupt/cancel message. A party member's failed escape cast just
        # expires naturally, with no zone line ever following it to confirm.
        self._pending_escape_casts.pop("You", None)

    def _apply_zone_change(self, ts: Optional[float]):
        """A zone transition just happened. If it's the completion of a
        pending group escape cast (yours or a party member's — evac/succor
        ports the whole group), every currently-charmed pet is left behind.
        Permanent pets aren't affected; they zone with you."""
        ts = ts if ts is not None else time.time()
        escape_confirmed = any(ts <= deadline for deadline in self._pending_escape_casts.values())
        self._pending_escape_casts.clear()
        if escape_confirmed:
            for key in [k for k, v in self.owned_pets.items() if v.kind == "charm"]:
                owned = self.owned_pets.pop(key)
                if owned.owner == "You":
                    self.charm_loss_events.append(ts)

    def current_aa_bonus(self) -> float:
        if self.aa_bonus_override is not None:
            return self.aa_bonus_override
        return self.aa_bonus_detected or 0.0

    def _apply_ability_description(self, text: str):
        name = self._pending_ability_name
        self._pending_ability_name = None
        if not name or _SCR_ABILITY_NAME not in name.lower():
            return
        m = _SCR_PERCENT_RE.search(text)
        if m:
            self.aa_bonus_detected = int(m.group("percent")) / 100.0

    def _begin_generic_cast(self, caster: str, spell: str, ts: Optional[float]):
        if caster != "You":
            return  # buff-duration learning is scoped to your own casts (see class docstring note)
        if spelldata.get().has_duration(spell) is False:
            return  # confirmed instant effect (nuke, heal, ...) from the game's own spell data — not a buff
        ts = ts if ts is not None else time.time()
        key = spell.strip().lower()
        self.active_buffs[key] = (ts, self.current_aa_bonus())
        self.buff_display_names[key] = spell.strip()

    def _apply_generic_cast_failed(self, spell: str):
        self.active_buffs.pop(spell.strip().lower(), None)

    def _apply_buff_worn_off(self, spell: Optional[str], candidates: Optional[List[str]], ts: Optional[float]):
        ts = ts if ts is not None else time.time()
        if spell is not None:
            key = spell.strip().lower()
        else:
            # Ambiguous fade message shared by several spells (often a whole
            # spell line, e.g. Echo of Health -> Celestial Echo -> Sacred
            # Echo all fading with the exact same flavor text) — resolve
            # using whichever candidate is actually pending right now; only
            # state.py has that context, which is why parser.py couldn't
            # pick a single name on its own.
            pending = [c.strip().lower() for c in (candidates or []) if c.strip().lower() in self.active_buffs]
            if not pending:
                return  # none of the candidates are something we're tracking
            # More than one candidate active at once is rare and genuinely
            # unresolvable from text alone — same class of limitation as the
            # charmed-twin-mob case. Best guess: the one cast most recently.
            key = max(pending, key=lambda k: self.active_buffs[k][0])

        entry = self.active_buffs.pop(key, None)
        if entry is None:
            return  # wore off, but we never saw it begin (already active before we started watching)
        cast_ts, bonus_at_cast = entry
        actual = max(ts - cast_ts, 0.0)
        # Compute `expected` from whatever we already knew BEFORE this
        # observation — so a spell's very first observation always reports
        # expected=None (we had nothing to predict from yet) rather than
        # trivially matching itself, and "what fell off fastest" stays a
        # comparison against a prior, independent prediction.
        expected = self.buff_base_durations[key] * (1 + bonus_at_cast) if key in self.buff_base_durations else None
        if key not in self.buff_base_durations and actual > 0:
            self.buff_base_durations[key] = actual / (1 + bonus_at_cast)
        display = self.buff_display_names.get(key, (spell or key).strip())
        self.expired_buffs.append(ExpiredBuff(display, cast_ts, ts, actual, expected))
        if len(self.expired_buffs) > 100:
            self.expired_buffs = self.expired_buffs[-100:]

    def buff_remaining(self, key: str, now: float) -> Optional[float]:
        """Seconds left on an active buff, or None if we haven't learned its
        duration yet (first-ever cast of that spell always returns None)."""
        entry = self.active_buffs.get(key)
        if entry is None or key not in self.buff_base_durations:
            return None
        cast_ts, bonus_at_cast = entry
        expected = self.buff_base_durations[key] * (1 + bonus_at_cast)
        return expected - (now - cast_ts)

    def _apply_party_status(self, in_party: bool):
        """Driven by the XP-gain line, which is authoritative on whether a
        kill's XP was actually split with a group — unlike the explicit
        invite/join/leave lines, it doesn't depend on having observed the
        moment the group formed (you may already have been grouped when the
        meter started watching the log)."""
        self.in_group = in_party
        if not in_party:
            self.party_members.clear()
            self.pending_inviter = None

    def _classify(self, actor: str) -> str:
        low = actor.strip().lower()
        if low == "you":
            return "you"
        if low in self.owned_pets or "pet" in low:
            return "pet"
        if self._is_party_member(actor):
            return "party"
        return "other"

    def _combatant_name(self, kind: str, raw_name: str) -> str:
        if kind == "you":
            return "You"
        if kind == "pet":
            return self._pet_record_name(raw_name)
        return raw_name

    def _pet_record_name(self, actor: str) -> str:
        owned = self.owned_pets.get(actor.strip().lower())
        if owned is not None:
            return f"{owned.display_name} ({self._owner_label(owned.owner)})"
        # Matched only by the generic "pet" substring (e.g. "a shadowknight
        # pet") with no leader-trick or charm confirmation seen yet — we
        # don't actually know whose it is, so don't claim an owner.
        clean = actor.strip()
        return _LEADING_ARTICLE_RE.sub("", clean).strip() or clean

    # ── ingestion ────────────────────────────────────────────────────────

    def ingest_line(self, line: str):
        self.lines_seen += 1
        event = parser.parse_line(line)
        if event is None:
            return
        with self.lock:
            self._dispatch(event)

    def _dispatch(self, event: parser.Event):
        if isinstance(event, parser.SlainEvent):
            if self.active is not None:
                self.active.mobs_fought.add(event.mob)
        elif isinstance(event, parser.PartyStatusEvent):
            self._apply_party_status(event.in_party)
        elif isinstance(event, parser.InviteEvent):
            self.pending_inviter = event.inviter
        elif isinstance(event, parser.GroupJoinEvent):
            self.in_group = True
            if self.pending_inviter:
                self.party_members.add(self.pending_inviter)
        elif isinstance(event, parser.GroupLeaveEvent):
            self.in_group = False
            self.party_members.clear()
            self.pending_inviter = None
        elif isinstance(event, parser.MemberJoinEvent):
            self.in_group = True
            self.party_members.add(event.member)
        elif isinstance(event, parser.MemberLeaveEvent):
            self.party_members.discard(event.member)
            if not self.party_members:
                self.in_group = False
        elif isinstance(event, parser.PetEvent):
            self.register_pet(event.pet, event.master)
        elif isinstance(event, parser.CharmCastEvent):
            self._begin_charm_cast(event.caster, event.ts)
        elif isinstance(event, parser.CharmedEvent):
            self._apply_charmed(event.mob, event.ts)
        elif isinstance(event, parser.CharmEndEvent):
            self._apply_charm_end(event.mob, event.ts)
        elif isinstance(event, parser.CharmCastFailedEvent):
            self._apply_charm_cast_failed()
        elif isinstance(event, parser.EscapeCastEvent):
            self._begin_escape_cast(event.caster, event.ts)
        elif isinstance(event, parser.EscapeCastFailedEvent):
            self._apply_escape_cast_failed()
        elif isinstance(event, parser.ZoneChangeEvent):
            self._apply_zone_change(event.ts)
        elif isinstance(event, parser.GenericCastEvent):
            self._begin_generic_cast(event.caster, event.spell, event.ts)
        elif isinstance(event, parser.GenericCastFailedEvent):
            self._apply_generic_cast_failed(event.spell)
        elif isinstance(event, parser.BuffWornOffEvent):
            self._apply_buff_worn_off(event.spell, event.candidates, event.ts)
        elif isinstance(event, parser.AbilityEntryEvent):
            self._pending_ability_name = event.name
        elif isinstance(event, parser.AbilityDescriptionEvent):
            self._apply_ability_description(event.text)
        elif isinstance(event, parser.HealEvent):
            self._record_heal(event)
        elif isinstance(event, parser.DamageEvent):
            self._record_damage(event)

    def _ensure_active_segment(self, ts: float):
        idle = self.last_activity_ts is None or (ts - self.last_activity_ts) > RESET_INACTIVITY
        if self.active is None or idle:
            self._close_active(ts)
            self.active = Segment(index=self._next_index, start=ts)
            self._next_index += 1
        self.last_activity_ts = ts
        self.last_wall_update = time.time()

    def _record_damage(self, event: parser.DamageEvent):
        actor_kind = self._classify(event.actor)
        target_kind = self._classify(event.target) if event.target else "other"

        deals_damage = actor_kind in {"you", "pet", "party"} or (
            actor_kind == "other" and self.track_others and _looks_like_player(event.actor)
        )
        takes_damage = target_kind in {"you", "pet", "party"} or (
            target_kind == "other" and self.track_others and _looks_like_player(event.target)
        )
        if not deals_damage and not takes_damage:
            return

        self.matched_lines += 1
        ts = event.ts if event.ts is not None else (self.last_activity_ts or time.time())
        self._ensure_active_segment(ts)

        if deals_damage:
            if event.target:
                self.active.mobs_fought.add(event.target)
            name = self._combatant_name(actor_kind, event.actor)
            self.active.record("damage", name, actor_kind, event.amount, event.ability, event.is_crit,
                                target=event.target)
        if takes_damage:
            if event.actor:
                self.active.mobs_fought.add(event.actor)
            name = self._combatant_name(target_kind, event.target)
            self.active.record("taken", name, target_kind, event.amount, event.ability, event.is_crit,
                                target=event.actor)

    def _record_heal(self, event: parser.HealEvent):
        actor_kind = self._classify(event.actor)
        if actor_kind == "other" and not (self.track_others and _looks_like_player(event.actor)):
            return
        if self.active is None:
            return  # not tied to any encounter (e.g. idle regen ticks) — nothing to attach it to
        name = self._combatant_name(actor_kind, event.actor)
        self.active.record("healing", name, actor_kind, event.amount, event.ability, False, target=event.target)

    def _close_active(self, end_ts: float):
        if self.active is not None:
            self.active.end = end_ts
            self.segments.append(self.active)
            if len(self.segments) > MAX_HISTORY:
                self.segments = self.segments[-MAX_HISTORY:]
            self.active = None

    def close_dangling(self):
        """Close whatever's active using the last known clock — call after a
        full-history replay finishes, since there's no live 'now' to wait on."""
        with self.lock:
            end = self.last_activity_ts if self.last_activity_ts is not None else time.time()
            self._close_active(end)

    def tick(self):
        """Live-mode housekeeping: close a stale encounter once real wall-clock
        time has passed with no new damage lines arriving at all."""
        with self.lock:
            if self.active is not None and self.last_wall_update is not None:
                if time.time() - self.last_wall_update >= RESET_INACTIVITY:
                    self._close_active(self.last_activity_ts if self.last_activity_ts is not None else time.time())

    def reset_all(self):
        with self.lock:
            self.segments.clear()
            self.active = None
            self.last_activity_ts = None
            self.last_wall_update = None
            self._next_index = 1
            self.matched_lines = 0

    @property
    def encounter_count(self) -> int:
        return len(self.segments) + (1 if self.active is not None else 0)


def replay_full_history(state: MeterState, watcher: LogWatcher) -> int:
    """Rewind the current log to the start, parse it all synchronously, then
    close any trailing encounter using the log's own clock. Returns the
    number of lines processed."""
    state.reset_all()
    watcher.seek_start()
    count = 0
    while True:
        line = watcher.readline()
        if line is None:
            break
        state.ingest_line(line)
        count += 1
    state.close_dangling()
    return count
