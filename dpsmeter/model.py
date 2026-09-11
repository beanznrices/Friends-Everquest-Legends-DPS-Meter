"""Encounter/combatant data model.

Every combatant — you, your pet, each party member — is a CombatantStats
keyed by name in one dict, so a full raid group scales for free and each
one carries both a per-ability and a per-target breakdown.

A Segment tracks three independent metrics per combatant — damage dealt,
healing given, and damage taken — as three separate CombatantStats maps
rather than three fields bolted onto one. They share the exact same
CombatantStats/AbilityStats shape, so the GUI's table, sorting, and
breakdown panel all work unmodified regardless of which metric is showing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

Kind = str  # "you" | "pet" | "party" | "other"
Metric = str  # "damage" | "healing" | "taken"


@dataclass
class AbilityStats:
    hits: int = 0
    crits: int = 0
    total: int = 0
    max_hit: int = 0

    @property
    def avg_hit(self) -> float:
        return self.total / self.hits if self.hits else 0.0

    @property
    def crit_rate(self) -> float:
        return self.crits / self.hits if self.hits else 0.0


@dataclass
class CombatantStats:
    name: str
    kind: Kind
    total: int = 0
    hits: int = 0
    crits: int = 0
    max_hit: int = 0
    abilities: Dict[str, AbilityStats] = field(default_factory=dict)
    by_target: Dict[str, AbilityStats] = field(default_factory=dict)

    def add(self, amount: int, ability: Optional[str], is_crit: bool, target: Optional[str] = None):
        self.total += amount
        self.hits += 1
        if is_crit:
            self.crits += 1
        if amount > self.max_hit:
            self.max_hit = amount

        self._bump(self.abilities, ability or "Melee", amount, is_crit)
        if target:
            self._bump(self.by_target, target, amount, is_crit)

    @staticmethod
    def _bump(bucket: Dict[str, AbilityStats], key: str, amount: int, is_crit: bool):
        a = bucket.setdefault(key, AbilityStats())
        a.hits += 1
        a.total += amount
        if is_crit:
            a.crits += 1
        if amount > a.max_hit:
            a.max_hit = amount

    @property
    def avg_hit(self) -> float:
        return self.total / self.hits if self.hits else 0.0

    @property
    def crit_rate(self) -> float:
        return self.crits / self.hits if self.hits else 0.0

    def top_abilities(self) -> List[tuple]:
        return sorted(self.abilities.items(), key=lambda kv: kv[1].total, reverse=True)

    def top_targets(self) -> List[tuple]:
        return sorted(self.by_target.items(), key=lambda kv: kv[1].total, reverse=True)


@dataclass
class Segment:
    """One encounter: a contiguous burst of combat, closed either by a kill
    line or by an inactivity gap."""

    index: int
    start: float  # log-clock epoch seconds
    end: Optional[float] = None
    mobs_fought: Set[str] = field(default_factory=set)
    damage: Dict[str, CombatantStats] = field(default_factory=dict)
    healing: Dict[str, CombatantStats] = field(default_factory=dict)
    taken: Dict[str, CombatantStats] = field(default_factory=dict)

    _METRIC_MAP = {"damage": "damage", "healing": "healing", "taken": "taken"}

    @property
    def mob_name(self) -> str:
        if not self.mobs_fought:
            return "Unknown Encounter"
        names = sorted(self.mobs_fought)
        if len(names) > 4:
            return " & ".join(names[:4]) + f" (+{len(names) - 4} more)"
        return " & ".join(names)

    def combatants_for(self, metric: Metric) -> Dict[str, CombatantStats]:
        return getattr(self, self._METRIC_MAP.get(metric, "damage"))

    def record(self, metric: Metric, name: str, kind: Kind, amount: int,
               ability: Optional[str], is_crit: bool, target: Optional[str] = None):
        bucket = self.combatants_for(metric)
        c = bucket.get(name)
        if c is None:
            c = CombatantStats(name=name, kind=kind)
            bucket[name] = c
        c.add(amount, ability, is_crit, target=target)

    def total_for(self, metric: Metric) -> int:
        return sum(c.total for c in self.combatants_for(metric).values())

    @property
    def total(self) -> int:
        return self.total_for("damage")

    def duration(self, now: float) -> float:
        """`now` is the caller's chosen clock — log-time during a replay,
        wall-clock while live — so this stays correct in both modes."""
        end = self.end if self.end is not None else now
        return max(end - self.start, 0.001)

    def dps(self, now: float) -> float:
        return self.total / self.duration(now)

    def ranked(self, metric: Metric = "damage") -> List[CombatantStats]:
        return sorted(self.combatants_for(metric).values(), key=lambda c: c.total, reverse=True)
