"""Pure parsing of EverQuest Legends combat log lines.

No per-line I/O, no GUI, no threading — just regex-in, event-out. Kept
isolated so it can be unit tested against real captured log lines without
spinning up tkinter or a file watcher. The one exception is `spelldata`,
which reads the game's own spell data files — but only once, lazily, cached
module-wide; see spelldata.py for why (custom "wears off" flavor text like
"The echo of healing fades away." can't be matched by any generic pattern).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import List, Optional, Union

from . import spelldata

# ─────────────────────────────────────────── timestamp ────────────────────────────────────────────

# EQ log lines look like: [Tue Sep 08 15:45:50 2026] <rest of line>
_LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s?(?P<rest>.*)$")
_TS_FORMAT = "%a %b %d %H:%M:%S %Y"


def parse_timestamp(raw: str) -> Optional[float]:
    """Convert an EQ log timestamp string to a local epoch float, or None."""
    try:
        return time.mktime(time.strptime(raw, _TS_FORMAT))
    except ValueError:
        return None


# ─────────────────────────────────────────── damage verbs ─────────────────────────────────────────

# Every melee/combat-art verb observed in practice — including smite/shoot/
# strike/bite/frenzy, common ranger/monk/druid-pet/beastlord swings that are
# easy to miss if you only test against a handful of sample log lines.
_VERB_ALT = (
    r"hits?|slash(?:es)?|cleaves?|bash(?:es)?|pierces?|crush(?:es)?|"
    r"attacks?|kicks?|punch(?:es)?|backstabs?|smites?|shoots?|strikes?|"
    r"claws?|gores?|stings?|bites?|mauls?|rends?|gouges?|frenz(?:y|ies)"
)

_DAMAGE_RE = re.compile(
    rf"^(?P<actor>.+?)\s+(?:{_VERB_ALT})\s+"
    rf"(?:a |an |the )?(?P<target>.+?)\s+for\s+"
    # A leading digit followed by any digits/commas — NOT \d{1,3}(?:,\d{3})*,
    # which silently truncates any 4+ digit number this log doesn't
    # comma-format (confirmed: 0 comma-formatted hits, 414 real 4+ digit
    # ones in a single session log — "1567" was being parsed as "156").
    rf"(?P<amount>\d[\d,]*)\s+points?",
    re.IGNORECASE,
)

# Damage-shield / reflect style lines are phrased backwards from a normal
# swing: "<target> is burned by YOUR flames for 6 points of non-melee damage."
# or "<target> is pierced by Grimjaw's thorns for 6 points ...". Easy to miss
# entirely if you only match the ordinary "<actor> <verb> <target>" shape.
_DAMAGE_SHIELD_RE = re.compile(
    r"^(?P<target>.+?)\s+is\s+"
    r"(?:pierced|burned|shocked|frozen|zapped|shredded|stung|bitten|crushed)\s+by\s+"
    r"(?:YOUR\s+(?P<self_source>.+?)|(?P<actor>.+?)'s\s+(?P<their_source>.+?))\s+for\s+"
    r"(?P<amount>\d[\d,]*)\s+points?",
    re.IGNORECASE,
)

_ABILITY_TAIL_RE = re.compile(r"^\s*of\s+(?:[a-z]+\s+)?damage(?:\s+by\s+(?P<ability>[^.()]+))?", re.IGNORECASE)
_ABILITY_ONLY_RE = re.compile(r"^\s*by\s+(?P<ability>[^.()]+)", re.IGNORECASE)
_DTYPE_RE = re.compile(r"^\s*of\s+(?P<dtype>[a-z]+)\s+damage", re.IGNORECASE)
_CRIT_RE = re.compile(r"\((Critical|Lucky|Flurry)\)", re.IGNORECASE)

# "<actor> healed <target> for N (M) hit points by <ability>." — the first
# number is effective healing (what matters for HPS); the parenthesized one,
# when present, is overheal-inclusive and not what we want. Always past
# tense in this log format — there's no present-tense "heals" event form.
_HEAL_RE = re.compile(
    r"^(?P<actor>.+?)\s+healed\s+(?P<target>.+?)\s+for\s+"
    r"(?P<amount>\d[\d,]*)(?:\s*\(\d[\d,]*\))?\s+hit points"
    r"(?:\s+by\s+(?P<ability>[^.()]+))?",
    re.IGNORECASE,
)

_SLAIN_RE = re.compile(r"^You have slain\s+(?:a |an |the )?(?P<mob>.+?)!", re.IGNORECASE)
_SLAIN_BY_RE = re.compile(r"^(?P<mob>.+?)\s+has been slain by\s+(?P<killer>.+?)!", re.IGNORECASE)

# The most reliable signal for "am I currently in a group" — EQ appends " party"
# to the XP-gain message whenever the kill's XP was actually split with a group,
# and only that message, regardless of whether an explicit "invites you to join
# a group." / "has joined the group." line was ever seen (e.g. you were already
# grouped before the meter started watching the log).
_XP_PARTY_RE = re.compile(r"^You gain party experience!", re.IGNORECASE)
_XP_SOLO_RE = re.compile(r"^You gain experience!", re.IGNORECASE)

# Charm: a charmed mob keeps its ordinary mob name (e.g. "a rock golem"),
# indistinguishable in text from any other mob sharing that name — so we only
# ever treat a name as "your charmed pet" for the window between a charm-type
# cast you began and the game's own charm confirmation, and we stop the moment
# it breaks. There's no log-side unique ID, so a same-named hostile mob
# fighting alongside your charmed pet at the same time is fundamentally
# unresolvable from the text log alone; this narrows that window as much as
# the log allows rather than pretending to solve it outright.
_CAST_BEGIN_SELF_RE = re.compile(r"^You begin casting (?P<spell>.+?)\.", re.IGNORECASE)
# Third person: "<name> begins casting <spell>." — a party member's own charm
# cast. EQ's grammar is consistent about "begin" (you) vs "begins" (anyone
# else), which is what tells the two apart.
_CAST_BEGIN_OTHER_RE = re.compile(r"^(?P<caster>.+?)\s+begins casting (?P<spell>.+?)\.", re.IGNORECASE)
_CHARM_SPELL_RE = re.compile(r"cajol|beguile|allure|charm", re.IGNORECASE)
_CHARMED_RE = re.compile(r"^(?P<mob>.+?)\s+has been charmed\.", re.IGNORECASE)
# Plenty of non-charm spells also end with "Your <spell> spell has worn off
# of <target>." (Rest the Dead, Dominate Undead, healing/buff spells cast on
# other players, ...) — require the spell name itself to look like a charm
# spell so we don't treat an unrelated buff expiring as your charm breaking.
_CHARM_WORN_OFF_RE = re.compile(r"^Your (?P<spell>.+?) spell has worn off of (?P<mob>.+?)\.", re.IGNORECASE)

# General buff tracking (any spell, not just charm): "Your <spell> spell has
# worn off." (cast on yourself) or "...worn off of <target>." (cast on an
# ally — duration is a property of the spell, not who it landed on, so both
# forms feed the same self-learned duration; the target itself is discarded).
# Checked after the charm-specific pattern above, so charm (which always has
# a target and is checked first) never double-matches here.
_GENERIC_BUFF_WORN_OFF_RE = re.compile(r"^Your (?P<spell>.+?) spell has worn off(?:\s+of\s+.+?)?\.", re.IGNORECASE)

# `/alt list`-style output: an "Ability #N: <name>" line immediately followed
# by "Description: <text>" for that same ability. Generic on purpose — we
# only act on it (in state.py) when the name is one we care about, but the
# parsing itself doesn't need to know that.
_ABILITY_ENTRY_RE = re.compile(r"^Ability #\d+: (?P<name>.+)$", re.IGNORECASE)
_ABILITY_DESC_RE = re.compile(r"^Description: (?P<text>.+)$", re.IGNORECASE)

# Group escape spells (Evacuate/Succor/Exodus, every rank and zone-specific
# variant — "Lesser Evacuate IV", "Evacuate: North Karana", "Greater Succor",
# ...) port the caster's whole group to the zone-in point, or an entirely
# different zone. A charmed pet is not a real group member and gets left
# behind — same as if it were charmed and then you simply zoned away from it.
_ESCAPE_SPELL_RE = re.compile(r"evacuate|succor|exodus", re.IGNORECASE)
# Confirms an escape spell actually completed (as opposed to being
# interrupted/cancelled, which produces no zone line at all — see
# _CAST_INTERRUPTED_RE below). Deliberately not captured/retained beyond this
# one check: this isn't a return of the zone-filtering feature, just a
# same-line signal for "you just zoned."
_ZONE_ENTER_RE = re.compile(r"^You have entered .+?\.", re.IGNORECASE)

# A charm or escape cast can fail outright (interrupted — which, in this log
# format, covers both a combat interrupt AND you manually cancelling it;
# there's no separate "cancelled" message — or, charm-only, resisted/target
# too high level) with no confirmation ever coming. If we don't notice, the
# pending window from the failed cast stays open and something unrelated
# landing in that window (someone else's charm; an ordinary zone-line walk)
# would get misattributed as the result of your cast.
_CAST_INTERRUPTED_RE = re.compile(r"^Your (?P<spell>.+?) spell is interrupted\.", re.IGNORECASE)
_CHARM_RESISTED_RE = re.compile(r"resisted your (?P<spell>.+?)!", re.IGNORECASE)
_CHARM_TOO_HIGH_LEVEL_RE = re.compile(r"^Your target is too high of a level for your charm spell\.", re.IGNORECASE)

_INVITE_RE = re.compile(r"^(?P<inviter>.+?)\s+invites you to join a group\.", re.IGNORECASE)
_GROUP_JOIN_RE = re.compile(r"^You have joined the group\.", re.IGNORECASE)
_GROUP_LEAVE_RE = re.compile(
    r"^(?:You have been removed from the group\.|"
    r"You are not in a group\.|"
    r"You have left the group\.|"
    r"You disband the group\.|"
    r".+? has disbanded the group\.)",
    re.IGNORECASE,
)
_MEMBER_JOIN_RE = re.compile(r"^(?P<member>.+?)\s+has joined the group\.", re.IGNORECASE)
_MEMBER_LEAVE_RE = re.compile(r"^(?P<member>.+?)\s+has left the group\.", re.IGNORECASE)
_GROUP_TELL_RE = re.compile(r"^(?P<member>.+?)\s+tells the group,", re.IGNORECASE)
_PET_RE = re.compile(r"^(?P<pet>.+?)\s+says?,?\s*['‘]My leader is\s+(?P<master>.+?)['’]\.?", re.IGNORECASE)


# ─────────────────────────────────────────── events ───────────────────────────────────────────────

@dataclass(frozen=True)
class DamageEvent:
    ts: Optional[float]
    actor: str
    target: str
    amount: int
    ability: Optional[str]
    is_crit: bool


@dataclass(frozen=True)
class HealEvent:
    ts: Optional[float]
    actor: str
    target: str
    amount: int
    ability: Optional[str]


@dataclass(frozen=True)
class SlainEvent:
    ts: Optional[float]
    mob: str
    killer: Optional[str]


@dataclass(frozen=True)
class PartyStatusEvent:
    ts: Optional[float]
    in_party: bool


@dataclass(frozen=True)
class PetEvent:
    ts: Optional[float]
    pet: str
    master: str


@dataclass(frozen=True)
class CharmCastEvent:
    ts: Optional[float]
    caster: str  # "You", or the third-person name that began the cast
    spell: str


@dataclass(frozen=True)
class CharmedEvent:
    ts: Optional[float]
    mob: str


@dataclass(frozen=True)
class CharmEndEvent:
    ts: Optional[float]
    mob: str


@dataclass(frozen=True)
class CharmCastFailedEvent:
    ts: Optional[float]


@dataclass(frozen=True)
class EscapeCastEvent:
    ts: Optional[float]
    caster: str
    spell: str


@dataclass(frozen=True)
class EscapeCastFailedEvent:
    ts: Optional[float]


@dataclass(frozen=True)
class ZoneChangeEvent:
    ts: Optional[float]


@dataclass(frozen=True)
class GenericCastEvent:
    """Any "begin(s) casting" line that isn't charm or escape — a candidate
    for general buff-duration learning."""
    ts: Optional[float]
    caster: str
    spell: str


@dataclass(frozen=True)
class GenericCastFailedEvent:
    ts: Optional[float]
    spell: str


@dataclass(frozen=True)
class BuffWornOffEvent:
    ts: Optional[float]
    # Exactly one of these is set. `spell` when the line named the spell
    # directly (the generic "Your <spell> spell has worn off[...]"
    # template — unambiguous). `candidates` when it came from matching a
    # custom flavor-text fade message instead, which can't name a single
    # spell on its own — an entire spell line (e.g. Echo of Health ->
    # Celestial Echo -> Sacred Echo) often shares the exact same fade text,
    # so the actual spell can only be resolved against which one is
    # currently pending, which only state.py knows.
    spell: Optional[str] = None
    candidates: Optional[List[str]] = None


@dataclass(frozen=True)
class AbilityEntryEvent:
    ts: Optional[float]
    name: str


@dataclass(frozen=True)
class AbilityDescriptionEvent:
    ts: Optional[float]
    text: str


@dataclass(frozen=True)
class InviteEvent:
    ts: Optional[float]
    inviter: str


@dataclass(frozen=True)
class GroupJoinEvent:
    ts: Optional[float]


@dataclass(frozen=True)
class GroupLeaveEvent:
    ts: Optional[float]


@dataclass(frozen=True)
class MemberJoinEvent:
    ts: Optional[float]
    member: str


@dataclass(frozen=True)
class MemberLeaveEvent:
    ts: Optional[float]
    member: str


Event = Union[
    DamageEvent,
    HealEvent,
    SlainEvent,
    PartyStatusEvent,
    PetEvent,
    CharmCastEvent,
    CharmedEvent,
    CharmEndEvent,
    CharmCastFailedEvent,
    EscapeCastEvent,
    EscapeCastFailedEvent,
    ZoneChangeEvent,
    GenericCastEvent,
    GenericCastFailedEvent,
    BuffWornOffEvent,
    AbilityEntryEvent,
    AbilityDescriptionEvent,
    InviteEvent,
    GroupJoinEvent,
    GroupLeaveEvent,
    MemberJoinEvent,
    MemberLeaveEvent,
]

# "you" was deliberately excluded here in earlier versions, back when the
# meter only cared about outgoing damage — but that meant "a mob hits YOU
# for N points" could never become a DamageEvent at all, which is exactly
# the line damage-taken tracking needs. Reflexive words stay excluded: a mob
# hitting itself isn't damage taken by anything we track.
_SELF_TARGET_WORDS = {"yourself", "himself", "herself", "itself", "themself"}


def _parse_damage(ts: Optional[float], rest: str) -> Optional[DamageEvent]:
    m = _DAMAGE_RE.match(rest)
    if m:
        target = m.group("target").strip()
        if target.lower() in _SELF_TARGET_WORDS or target.lower().startswith("your "):
            return None
        tail = rest[m.end():]
        ability = None
        tail_match = _ABILITY_TAIL_RE.match(tail)
        if tail_match:
            ability = tail_match.group("ability")
        else:
            ability_match = _ABILITY_ONLY_RE.match(tail)
            if ability_match:
                ability = ability_match.group("ability")
        if ability:
            ability = ability.strip().rstrip(".")
        return DamageEvent(
            ts=ts,
            actor=m.group("actor").strip(),
            target=target,
            amount=int(m.group("amount").replace(",", "")),
            ability=ability,
            is_crit=bool(_CRIT_RE.search(tail)),
        )

    m = _DAMAGE_SHIELD_RE.match(rest)
    if m:
        tail = rest[m.end():]
        if m.group("self_source") is not None:
            actor = "You"
            ability = m.group("self_source").strip()
        else:
            actor = m.group("actor").strip()
            ability = m.group("their_source").strip()
        return DamageEvent(
            ts=ts,
            actor=actor,
            target=m.group("target").strip(),
            amount=int(m.group("amount").replace(",", "")),
            ability=ability,
            is_crit=bool(_CRIT_RE.search(tail)),
        )

    return None


def parse_line(line: str) -> Optional[Event]:
    """Parse one raw log line into a single Event, or None if uninteresting."""
    m = _LINE_RE.match(line)
    if not m:
        return None
    ts = parse_timestamp(m.group("ts"))
    rest = m.group("rest").strip()
    if not rest:
        return None

    if _XP_PARTY_RE.match(rest):
        return PartyStatusEvent(ts=ts, in_party=True)

    if _XP_SOLO_RE.match(rest):
        return PartyStatusEvent(ts=ts, in_party=False)

    m2 = _SLAIN_RE.match(rest)
    if m2:
        return SlainEvent(ts=ts, mob=m2.group("mob").strip(), killer="You")

    m2 = _SLAIN_BY_RE.match(rest)
    if m2:
        return SlainEvent(ts=ts, mob=m2.group("mob").strip(), killer=m2.group("killer").strip())

    m2 = _INVITE_RE.match(rest)
    if m2:
        return InviteEvent(ts=ts, inviter=m2.group("inviter").strip())

    if _GROUP_JOIN_RE.match(rest):
        return GroupJoinEvent(ts=ts)

    if _GROUP_LEAVE_RE.match(rest):
        return GroupLeaveEvent(ts=ts)

    m2 = _MEMBER_JOIN_RE.match(rest)
    if m2:
        return MemberJoinEvent(ts=ts, member=m2.group("member").strip())

    m2 = _MEMBER_LEAVE_RE.match(rest)
    if m2:
        return MemberLeaveEvent(ts=ts, member=m2.group("member").strip())

    m2 = _GROUP_TELL_RE.match(rest)
    if m2:
        return MemberJoinEvent(ts=ts, member=m2.group("member").strip())

    m2 = _PET_RE.match(rest)
    if m2:
        return PetEvent(ts=ts, pet=m2.group("pet").strip(), master=m2.group("master").strip().rstrip("."))

    m2 = _CAST_BEGIN_SELF_RE.match(rest)
    if m2:
        spell = m2.group("spell").strip()
        if _CHARM_SPELL_RE.search(spell):
            return CharmCastEvent(ts=ts, caster="You", spell=spell)
        if _ESCAPE_SPELL_RE.search(spell):
            return EscapeCastEvent(ts=ts, caster="You", spell=spell)
        return GenericCastEvent(ts=ts, caster="You", spell=spell)

    m2 = _CAST_BEGIN_OTHER_RE.match(rest)
    if m2:
        spell = m2.group("spell").strip()
        caster = m2.group("caster").strip()
        if _CHARM_SPELL_RE.search(spell):
            return CharmCastEvent(ts=ts, caster=caster, spell=spell)
        if _ESCAPE_SPELL_RE.search(spell):
            return EscapeCastEvent(ts=ts, caster=caster, spell=spell)
        return GenericCastEvent(ts=ts, caster=caster, spell=spell)

    m2 = _CHARMED_RE.match(rest)
    if m2:
        return CharmedEvent(ts=ts, mob=m2.group("mob").strip())

    m2 = _CHARM_WORN_OFF_RE.match(rest)
    if m2 and _CHARM_SPELL_RE.search(m2.group("spell")):
        return CharmEndEvent(ts=ts, mob=m2.group("mob").strip())

    m2 = _GENERIC_BUFF_WORN_OFF_RE.match(rest)
    if m2:
        return BuffWornOffEvent(ts=ts, spell=m2.group("spell").strip())

    m2 = _CAST_INTERRUPTED_RE.match(rest)
    if m2:
        spell = m2.group("spell")
        if _CHARM_SPELL_RE.search(spell):
            return CharmCastFailedEvent(ts=ts)
        if _ESCAPE_SPELL_RE.search(spell):
            return EscapeCastFailedEvent(ts=ts)
        return GenericCastFailedEvent(ts=ts, spell=spell.strip())

    m2 = _CHARM_RESISTED_RE.search(rest)
    if m2 and _CHARM_SPELL_RE.search(m2.group("spell")):
        return CharmCastFailedEvent(ts=ts)

    if _CHARM_TOO_HIGH_LEVEL_RE.match(rest):
        return CharmCastFailedEvent(ts=ts)

    if _ZONE_ENTER_RE.match(rest):
        return ZoneChangeEvent(ts=ts)

    # Custom "wears off" flavor text ("The echo of healing fades away.",
    # "Your valor fades.") that the generic "Your <spell> spell has worn
    # off[...]" template above never matches — see spelldata.py. Checked
    # this late so it can never preempt a more specific pattern; a spell
    # whose fade text happens to also read like some other kind of line
    # would already have been claimed above.
    candidates = spelldata.get().spells_for_fade_message(rest)
    if candidates:
        if len(candidates) == 1:
            return BuffWornOffEvent(ts=ts, spell=candidates[0])
        return BuffWornOffEvent(ts=ts, candidates=candidates)

    m2 = _ABILITY_ENTRY_RE.match(rest)
    if m2:
        return AbilityEntryEvent(ts=ts, name=m2.group("name").strip())

    m2 = _ABILITY_DESC_RE.match(rest)
    if m2:
        return AbilityDescriptionEvent(ts=ts, text=m2.group("text").strip())

    m2 = _HEAL_RE.match(rest)
    if m2:
        ability = m2.group("ability")
        if ability:
            ability = ability.strip().rstrip(".")
        return HealEvent(
            ts=ts,
            actor=m2.group("actor").strip(),
            target=m2.group("target").strip(),
            amount=int(m2.group("amount").replace(",", "")),
            ability=ability,
        )

    return _parse_damage(ts, rest)
