"""Real spell classification, sourced from the game's own client data files
(`spells_us.txt` / `spells_us_str.txt`, sitting in the install root — the
same classic EQ/EQEmu text format used by prior-art parsers for this game).

Two things self-learning alone can't know, that this file can:

1. **Whether a cast even has a duration at all.** Damage nukes, instant
   heals, and escape spells produce a "You begin casting X." line just like
   real buffs do, but never a "wears off" line — they'd sit in the tracker
   forever as "learning…". Fields 11/12 of a spells_us.txt row (the
   buff-duration formula and raw duration) are both 0 only for true instant
   effects; verified against 6 known spells spanning nuke/heal/escape (all
   0/0) and buff/debuff/charm (all nonzero) before trusting this.

2. **The exact "wears off" text**, which isn't always the generic
   "Your <spell> spell has worn off." template this project started with.
   Many spells define their own flavor text ("The echo of healing fades
   away.", "Your valor fades.") in spells_us_str.txt's SPELLGONE column,
   joined to spells_us.txt by spell ID (same key in both files — verified
   directly: id 74005 in spells_us.txt is "Sacred Echo"; row 74005 in
   spells_us_str.txt has SPELLGONE "The echo of healing fades away.", which
   is exactly the line real players see and exactly what the old
   generic-template-only regex could never have matched).

Read-only, loaded once and cached. Missing/unreadable files degrade to "no
opinion" everywhere (has_duration() returns None, fade lookups miss) rather
than breaking anything — this is a strict enhancement over pure self-learning,
never a hard dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from . import config

# This game's "merge to a higher tier" mechanic (e.g. "Cajoling Whispers"
# merged up to "Cajoling Whispers VIII") produces cast lines with a rank
# suffix that doesn't exist as a separate row in spells_us.txt — only the
# base spell does. Stripped as a fallback lookup key, never the primary one.
_TRAILING_RANK_RE = re.compile(r"\s+[IVXLCDM]+$")


@dataclass(frozen=True)
class SpellInfo:
    name: str
    has_duration: bool
    fade_message: Optional[str]


class SpellDatabase:
    def __init__(self, by_name: Dict[str, SpellInfo], by_fade_message: Dict[str, List[str]]):
        self._by_name = by_name
        self._by_fade_message = by_fade_message

    def lookup(self, spell_name: str) -> Optional[SpellInfo]:
        key = spell_name.strip().lower()
        info = self._by_name.get(key)
        if info is not None:
            return info
        stripped = _TRAILING_RANK_RE.sub("", spell_name.strip()).lower()
        if stripped != key:
            return self._by_name.get(stripped)
        return None

    def has_duration(self, spell_name: str) -> Optional[bool]:
        """True/False if known, None if this spell isn't in the database at
        all (unknown spells are never filtered out — only a confirmed 'no
        duration' answer excludes something from tracking)."""
        info = self.lookup(spell_name)
        return info.has_duration if info is not None else None

    def spells_for_fade_message(self, text: str) -> List[str]:
        """Every spell whose expiry uses this exact flavor text — often more
        than one: an entire spell line (e.g. Echo of Health -> Celestial
        Echo -> Sacred Echo) frequently reuses the same "fades" text across
        its ranks. Ambiguous on its own; the caller (state.py) disambiguates
        using which of these is actually pending."""
        return self._by_fade_message.get(text.strip(), [])


def _parse_spells_us(path: Path) -> Dict[str, SpellInfo]:
    by_id: Dict[str, tuple] = {}  # id -> (name, has_duration)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            fields = line.rstrip("\n").split("^", 13)
            if len(fields) < 13:
                continue
            spell_id, name = fields[0], fields[1]
            try:
                formula, duration = int(fields[11]), int(fields[12])
            except ValueError:
                continue
            by_id[spell_id] = (name, formula != 0 or duration != 0)
    return by_id


def _parse_spells_us_str(path: Path) -> Dict[str, str]:
    fade_by_id: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("^", 5)
            if len(fields) < 6:
                continue
            # The line ends "...^SPELLGONE^", so field 5 (maxsplit stops
            # here) still carries that trailing delimiter — strip it, not
            # just whitespace, or every fade message silently fails to
            # match its real in-game text.
            spell_id, fade = fields[0], fields[5].rstrip("^").strip()
            if fade:
                fade_by_id[spell_id] = fade
    return fade_by_id


def load(install_dir: Optional[str] = None) -> SpellDatabase:
    root = Path(install_dir) if install_dir else Path(config.default_log_dir()).parent
    spells_path = root / "spells_us.txt"
    strings_path = root / "spells_us_str.txt"

    by_name: Dict[str, SpellInfo] = {}
    by_fade_message: Dict[str, List[str]] = {}

    if spells_path.exists():
        spells_by_id = _parse_spells_us(spells_path)
        fade_by_id = _parse_spells_us_str(strings_path) if strings_path.exists() else {}

        for spell_id, (name, has_dur) in spells_by_id.items():
            fade = fade_by_id.get(spell_id)
            key = name.strip().lower()
            if key not in by_name:  # first row wins on duplicate names (lower ranks usually come first)
                by_name[key] = SpellInfo(name=name.strip(), has_duration=has_dur, fade_message=fade)
            if fade:
                candidates = by_fade_message.setdefault(fade, [])
                if name.strip() not in candidates:
                    candidates.append(name.strip())

    return SpellDatabase(by_name, by_fade_message)


_cached: Optional[SpellDatabase] = None


def get() -> SpellDatabase:
    global _cached
    if _cached is None:
        _cached = load()
    return _cached
