import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dpsmeter.state import MeterState


def line(ts: str, rest: str) -> str:
    return f"[{ts}] {rest}"


class TestEncounterSegmentation(unittest.TestCase):
    def test_two_swings_within_window_form_one_encounter(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:46:01 2026", "You pierce a sturdy skeleton for 61 points of damage."))
        st.ingest_line(line("Tue Sep 08 15:46:05 2026", "You pierce a sturdy skeleton for 40 points of damage."))
        self.assertEqual(st.encounter_count, 1)
        self.assertEqual(st.active.damage["You"].total, 101)

    def test_gap_past_reset_window_starts_new_encounter(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:46:01 2026", "You pierce a sturdy skeleton for 61 points of damage."))
        st.ingest_line(line("Tue Sep 08 15:46:30 2026", "You pierce a rat for 10 points of damage."))
        self.assertEqual(len(st.segments), 1)
        self.assertEqual(st.segments[0].total, 61)
        self.assertEqual(st.active.total, 10)

    def test_replay_uses_log_clock_for_duration_not_wallclock(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:46:00 2026", "You pierce a sturdy skeleton for 100 points of damage."))
        st.ingest_line(line("Tue Sep 08 15:46:10 2026", "You pierce a sturdy skeleton for 100 points of damage."))
        # Force the encounter closed as replay code would, using the log clock.
        st.close_dangling()
        seg = st.segments[0]
        self.assertAlmostEqual(seg.duration(now=999999999), 10.0, places=2)


class TestClassification(unittest.TestCase):
    def test_pet_damage_tracked_under_pet_name(self):
        st = MeterState()
        st.set_character_name("Ashlyn")
        st.ingest_line(line("Tue Sep 08 15:45:57 2026", "Emberclaw says, 'My leader is Ashlyn.'"))
        st.ingest_line(line("Tue Sep 08 15:46:02 2026", "Emberclaw slashes a sturdy skeleton for 126 points of damage."))
        self.assertIn("Emberclaw (Ashlyn)", st.active.damage)
        self.assertEqual(st.active.damage["Emberclaw (Ashlyn)"].kind, "pet")

    def test_party_member_tracked_separately_from_strangers(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 17:04:52 2026", "Thistle invites you to join a group."))
        st.ingest_line(line("Tue Sep 08 17:04:54 2026", "You have joined the group."))
        st.ingest_line(line("Tue Sep 08 17:05:00 2026", "Thistle slashes a rat for 20 points of damage."))
        st.ingest_line(line("Tue Sep 08 17:05:01 2026", "A basalt gargoyle hits Stonewell for 27 points"))
        self.assertEqual(st.active.damage["Thistle"].kind, "party")
        self.assertNotIn("Stonewell", st.active.damage)  # stranger, filtered by default

    def test_track_others_flag_includes_stranger_players(self):
        st = MeterState(track_others=True)
        st.ingest_line(line("Tue Sep 08 17:05:01 2026", "Moonfire pierces a sturdy skeleton for 40 points of damage."))
        self.assertIn("Moonfire", st.active.damage)
        self.assertEqual(st.active.damage["Moonfire"].kind, "other")

    def test_track_others_flag_still_excludes_monsters(self):
        # A monster landing a hit on a party member is not "another player's DPS".
        st = MeterState(track_others=True)
        st.ingest_line(line("Tue Sep 08 17:05:01 2026", "A basalt gargoyle hits Stonewell for 27 points"))
        self.assertNotIn("A basalt gargoyle", st.active.damage if st.active else {})

    def test_party_xp_line_detects_group_even_without_join_line_seen(self):
        # Real scenario: the meter starts watching the log after the group
        # already formed, so no "invites you"/"has joined the group." line
        # is ever observed. The XP-gain line is the only signal we get.
        st = MeterState()
        self.assertFalse(st.in_group)
        st.ingest_line(line("Thu Sep 10 16:37:26 2026", "You gain party experience! (1.151%)"))
        self.assertTrue(st.in_group)

    def test_solo_xp_line_ends_party_even_without_leave_line_seen(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 17:04:52 2026", "Thistle invites you to join a group."))
        st.ingest_line(line("Tue Sep 08 17:04:54 2026", "You have joined the group."))
        st.ingest_line(line("Tue Sep 08 17:05:00 2026", "Thistle slashes a rat for 20 points of damage."))
        self.assertTrue(st.in_group)
        self.assertIn("Thistle", st.party_members)

        st.ingest_line(line("Tue Sep 08 17:10:00 2026", "You gain experience! (0.5%)"))
        self.assertFalse(st.in_group)
        self.assertEqual(st.party_members, set())

    def test_group_leave_clears_party(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 17:04:52 2026", "Thistle invites you to join a group."))
        st.ingest_line(line("Tue Sep 08 17:04:54 2026", "You have joined the group."))
        st.ingest_line(line("Tue Sep 08 17:05:00 2026", "You have been removed from the group."))
        self.assertEqual(st.party_members, set())
        self.assertFalse(st.in_group)


class TestAbilityBreakdown(unittest.TestCase):
    def test_crit_and_ability_tracking(self):
        st = MeterState()
        st.ingest_line(line(
            "Tue Sep 08 15:46:09 2026",
            "You hit a skeletal excavator for 384 points of magic damage by Garrison's Mighty Mana Shock. (Critical)",
        ))
        st.ingest_line(line("Tue Sep 08 15:46:10 2026", "You pierce a skeletal excavator for 20 points of damage."))
        you = st.active.damage["You"]
        self.assertEqual(you.total, 404)
        self.assertEqual(you.crits, 1)
        self.assertEqual(you.max_hit, 384)
        top = you.top_abilities()
        self.assertEqual(top[0][0], "Garrison's Mighty Mana Shock")
        self.assertEqual(top[1][0], "Melee")


class TestCharmedPets(unittest.TestCase):
    def test_charmed_pet_tracked_separately_from_your_own_kind_named_target(self):
        # You cast charm, the game confirms a specific mob got charmed, and it
        # starts fighting an identically-named mob. Both names are "an
        # elemental warrior" in the log — the pet's damage should land under
        # its own combatant row with kind "pet", not vanish or merge with you.
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))
        st.ingest_line(line("Wed Sep 09 20:50:50 2026",
                             "a rock golem slashes an elemental warrior for 40 points of damage."))
        self.assertIn("a rock golem (You)", st.active.damage)
        self.assertEqual(st.active.damage["a rock golem (You)"].kind, "pet")
        self.assertEqual(st.active.damage["a rock golem (You)"].total, 40)

    def test_charm_wearing_off_stops_further_tracking_as_pet(self):
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a dar ghoul knight has been charmed."))
        st.ingest_line(line("Wed Sep 09 20:50:50 2026",
                             "a dar ghoul knight slashes a rat for 40 points of damage."))
        self.assertIn("a dar ghoul knight (You)", st.active.damage)

        st.ingest_line(line("Wed Sep 09 20:51:00 2026",
                             "Your Cajoling Whispers spell has worn off of a dar ghoul knight."))
        self.assertNotIn("a dar ghoul knight", st.owned_pets)

        # Once released, that name is an ordinary hostile mob again — its
        # damage should not keep accumulating under a "pet" row.
        st.ingest_line(line("Wed Sep 09 20:51:05 2026",
                             "a dar ghoul knight slashes Grimtusk for 99 points of damage."))
        self.assertEqual(st.active.damage["a dar ghoul knight (You)"].total, 40)

    def test_charmed_confirmation_without_a_recent_cast_is_not_yours(self):
        # No "You begin casting <charm spell>" preceded this — could be
        # someone else's charm, or a stray confirmation. Don't claim it.
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))
        self.assertEqual(st.owned_pets, {})

    def test_interrupted_charm_does_not_leave_a_stale_window_open(self):
        # Real sequence pulled from the log: a charm attempt gets interrupted,
        # then (unrelated) some other mob gets charmed by someone else a few
        # seconds later while still inside what would have been the 10s
        # window. That charm must not be claimed as ours.
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:01 2026", "You begin casting Cajoling Whispers VII."))
        st.ingest_line(line("Wed Sep 09 20:50:02 2026", "Your Cajoling Whispers spell is interrupted."))
        st.ingest_line(line("Wed Sep 09 20:50:08 2026", "a rock golem has been charmed."))
        self.assertEqual(st.owned_pets, {})

    def test_resisted_charm_does_not_leave_a_stale_window_open(self):
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:13 2026", "You begin casting Cajoling Whispers VII."))
        st.ingest_line(line("Wed Sep 09 20:50:17 2026", "Your target is too high of a level for your charm spell."))
        st.ingest_line(line("Wed Sep 09 20:50:17 2026", "A rock golem resisted your Cajoling Whispers VII!"))
        st.ingest_line(line("Wed Sep 09 20:50:20 2026", "a rock golem has been charmed."))
        self.assertEqual(st.owned_pets, {})

    def test_successful_charm_after_earlier_failures_still_works(self):
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:01 2026", "You begin casting Cajoling Whispers VII."))
        st.ingest_line(line("Wed Sep 09 20:50:02 2026", "Your Cajoling Whispers spell is interrupted."))
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))
        self.assertIn("a rock golem", st.owned_pets)

    def test_permanent_pet_and_charmed_pet_coexist_as_separate_rows(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:45:57 2026", "Emberclaw says, 'My leader is Ashlyn.'"))
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))
        st.ingest_line(line("Wed Sep 09 20:50:50 2026", "Emberclaw slashes a rat for 10 points of damage."))
        st.ingest_line(line("Wed Sep 09 20:50:51 2026", "a rock golem slashes a rat for 20 points of damage."))
        self.assertEqual(st.active.damage["Emberclaw (You)"].kind, "pet")
        self.assertEqual(st.active.damage["a rock golem (You)"].kind, "pet")
        self.assertEqual(len([c for c in st.active.damage.values() if c.kind == "pet"]), 2)


class TestPartyMemberPets(unittest.TestCase):
    """Real scenario pulled straight from the log: a party member's pet
    shares the exact same 'says, My leader is X' line format as yours, and a
    party member's charm cast is phrased in third person ('X begins casting'
    vs 'You begin casting')."""

    def test_party_members_charm_is_tracked_under_their_name(self):
        st = MeterState()
        st.set_character_name("Ashlyn")
        st.ingest_line(line("Tue Sep 08 17:04:54 2026", "You have joined the group."))
        st.ingest_line(line("Tue Sep 08 18:40:58 2026", "Moonfire has joined the group."))
        st.ingest_line(line("Tue Sep 08 18:40:58 2026", "Moonfire begins casting Beguile."))
        st.ingest_line(line("Tue Sep 08 18:41:02 2026", "a hardened skeleton has been charmed."))
        st.ingest_line(line("Tue Sep 08 18:41:05 2026",
                             "a hardened skeleton slashes a rat for 30 points of damage."))
        self.assertIn("a hardened skeleton (Moonfire)", st.active.damage)
        self.assertEqual(st.active.damage["a hardened skeleton (Moonfire)"].kind, "pet")

    def test_strangers_charm_is_never_tracked(self):
        # "Oakenshield begins casting Charm." / "a Teir'Dal ranger has been
        # charmed." — a real line from the log, but Oakenshield is not in the
        # party, so this must never be claimed as a tracked pet.
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 18:38:41 2026", "Oakenshield begins casting Charm."))
        st.ingest_line(line("Tue Sep 08 18:38:43 2026", "a Teir`Dal ranger has been charmed."))
        self.assertEqual(st.owned_pets, {})

    def test_party_members_leader_trick_is_attributed_to_them_not_you(self):
        # Real bug reproduction: at line 150484 your own pet said "My leader
        # is Ashlyn.", then at 150810 "A rock golem says, 'My leader is
        # Moonfire.'" — the exact same species name, a party member's pet. The
        # old code unconditionally treated any such line as *your* pet,
        # silently corrupting your own pet tracking with Moonfire's pet instead.
        st = MeterState()
        st.set_character_name("Ashlyn")
        st.ingest_line(line("Tue Sep 08 17:04:54 2026", "You have joined the group."))
        st.ingest_line(line("Tue Sep 08 18:40:00 2026", "Moonfire has joined the group."))
        st.ingest_line(line("Wed Sep 09 21:01:39 2026", "A rock golem says, 'My leader is Moonfire.'"))
        self.assertEqual(st.owned_pets["a rock golem"].owner, "Moonfire")

    def test_your_leader_trick_still_resolves_to_you(self):
        st = MeterState()
        st.set_character_name("Ashlyn")
        st.ingest_line(line("Wed Sep 09 20:59:41 2026", "A rock golem says, 'My leader is Ashlyn.'"))
        self.assertEqual(st.owned_pets["a rock golem"].owner, "You")

    def test_identically_named_pets_from_two_owners_is_a_known_limit(self):
        # Once your pet and a party member's pet share the exact same name,
        # the log gives no per-instance ID — later "My leader is" text for
        # that name simply replaces the earlier mapping. Documenting this as
        # the expected (not silently-wrong) behavior, same class of limit as
        # the charmed-twin-mob case.
        st = MeterState()
        st.set_character_name("Ashlyn")
        st.ingest_line(line("Tue Sep 08 17:04:54 2026", "You have joined the group."))
        st.ingest_line(line("Tue Sep 08 18:40:00 2026", "Moonfire has joined the group."))
        st.ingest_line(line("Wed Sep 09 20:59:41 2026", "A rock golem says, 'My leader is Ashlyn.'"))
        self.assertEqual(st.owned_pets["a rock golem"].owner, "You")
        st.ingest_line(line("Wed Sep 09 21:01:39 2026", "A rock golem says, 'My leader is Moonfire.'"))
        self.assertEqual(st.owned_pets["a rock golem"].owner, "Moonfire")

    def test_leader_trick_from_a_non_party_stranger_is_ignored(self):
        st = MeterState()
        st.set_character_name("Ashlyn")
        st.ingest_line(line("Wed Sep 09 12:00:00 2026", "Some pet says, 'My leader is RandomStranger.'"))
        self.assertEqual(st.owned_pets, {})


class TestEvacSuccorReleasesCharmedPets(unittest.TestCase):
    def test_successful_evac_releases_charmed_pet_but_not_permanent_pet(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:45:57 2026", "Emberclaw says, 'My leader is Ashlyn.'"))
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))
        self.assertEqual(len(st.owned_pets), 2)

        st.ingest_line(line("Wed Sep 09 20:51:00 2026", "You begin casting Lesser Evacuate IV."))
        st.ingest_line(line("Wed Sep 09 20:51:15 2026", "You have entered Befallen 4 (Refined)."))

        self.assertNotIn("a rock golem", st.owned_pets)  # charmed pet: left behind
        self.assertIn("emberclaw", st.owned_pets)  # permanent pet: zones with you

    def test_real_captured_evac_timing_is_within_the_confirm_window(self):
        # Real sequence from the log: cast-begin to confirming zone-enter
        # took 23 seconds (cast time + "LOADING, PLEASE WAIT..."). A window
        # that's too tight here silently fails to release the pet.
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))

        st.ingest_line(line("Wed Sep 09 23:14:13 2026", "You begin casting Lesser Evacuate IV."))
        st.ingest_line(line("Wed Sep 09 23:14:22 2026", "Moonfire creates a mystic portal."))
        st.ingest_line(line("Wed Sep 09 23:14:36 2026", "You have entered The Ruins of Old Paineel."))

        self.assertNotIn("a rock golem", st.owned_pets)

    def test_cancelled_or_interrupted_evac_does_not_release_the_pet(self):
        # User-reported case: if the evac/succor cast is cancelled or
        # interrupted, the charm is untouched — this log format uses the same
        # "spell is interrupted" message for both a combat interrupt and a
        # manual cancel, so one check covers both.
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))

        st.ingest_line(line("Wed Sep 09 20:51:00 2026", "You begin casting Lesser Evacuate IV."))
        st.ingest_line(line("Wed Sep 09 20:51:02 2026", "Your Lesser Evacuate spell is interrupted."))
        # Any later, unrelated zone line (e.g. you just walk through a zone
        # line yourself) must not be mistaken for the cancelled evac completing.
        st.ingest_line(line("Wed Sep 09 20:51:15 2026", "You have entered Befallen 4 (Refined)."))

        self.assertIn("a rock golem", st.owned_pets)

    def test_ordinary_zoning_with_no_pending_escape_does_not_release_pet(self):
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))
        st.ingest_line(line("Wed Sep 09 20:51:15 2026", "You have entered Befallen 4 (Refined)."))
        self.assertIn("a rock golem", st.owned_pets)

    def test_party_members_evac_also_releases_your_charmed_pet(self):
        # Evac/succor ports the whole group, so whoever casts it doesn't
        # matter — everyone's charmed pet gets left behind, including yours.
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 18:40:00 2026", "Moonfire has joined the group."))
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))

        st.ingest_line(line("Wed Sep 09 20:51:00 2026", "Moonfire begins casting Succor."))
        st.ingest_line(line("Wed Sep 09 20:51:15 2026", "You have entered Befallen 4 (Refined)."))

        self.assertNotIn("a rock golem", st.owned_pets)

    def test_strangers_evac_does_not_affect_your_pet(self):
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))

        st.ingest_line(line("Wed Sep 09 20:51:00 2026", "SomeRandomStranger begins casting Evacuate."))
        st.ingest_line(line("Wed Sep 09 20:51:15 2026", "You have entered Befallen 4 (Refined)."))

        self.assertIn("a rock golem", st.owned_pets)


class TestNoZoneFiltering(unittest.TestCase):
    def test_segment_has_no_zone_attribute(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:46:01 2026", "You pierce a sturdy skeleton for 61 points of damage."))
        self.assertFalse(hasattr(st.active, "zone"))


class TestDamageTaken(unittest.TestCase):
    def test_mob_hitting_you_is_tracked_as_damage_taken(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:46:04 2026", "A basalt gargoyle hits YOU for 24 points"))
        self.assertIn("You", st.active.taken)
        self.assertEqual(st.active.taken["You"].total, 24)
        self.assertNotIn("A basalt gargoyle", st.active.damage)  # not outgoing damage

    def test_mob_hitting_party_member_is_tracked_as_damage_taken(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 17:04:54 2026", "Thistle has joined the group."))
        st.ingest_line(line("Tue Sep 08 17:05:01 2026", "A basalt gargoyle hits Thistle for 27 points"))
        self.assertIn("Thistle", st.active.taken)
        self.assertEqual(st.active.taken["Thistle"].total, 27)

    def test_damage_taken_respects_track_others_the_same_as_damage_dealt(self):
        st = MeterState()  # track_others defaults False
        st.ingest_line(line("Tue Sep 08 17:05:01 2026", "A basalt gargoyle hits Stonewell for 27 points"))
        self.assertIsNone(st.active)  # a stranger being hit by a mob starts nothing by default

    def test_damage_taken_alone_starts_an_encounter(self):
        # Getting hit before you've swung back still means you're in combat.
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:46:04 2026", "A basalt gargoyle hits YOU for 24 points"))
        self.assertIsNotNone(st.active)
        self.assertIn("a basalt gargoyle", {m.lower() for m in st.active.mobs_fought})

    def test_damage_taken_source_breaks_down_by_ability(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:46:04 2026", "A basalt gargoyle hits YOU for 24 points"))
        st.ingest_line(line("Tue Sep 08 15:46:05 2026",
                             "A basalt gargoyle hit YOU for 60 points of fire damage by Flame Breath."))
        you_taken = st.active.taken["You"]
        self.assertEqual(you_taken.total, 84)
        names = {name for name, _ in you_taken.top_abilities()}
        self.assertEqual(names, {"Melee", "Flame Breath"})


class TestHealing(unittest.TestCase):
    def test_heal_tracked_under_healer_kind(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:46:01 2026", "You pierce a sturdy skeleton for 61 points of damage."))
        st.ingest_line(line("Tue Sep 08 15:46:05 2026", "You healed Moonfire for 500 hit points by Superior Healing."))
        self.assertIn("You", st.active.healing)
        self.assertEqual(st.active.healing["You"].total, 500)

    def test_heal_outside_any_encounter_is_dropped(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 15:46:05 2026", "You healed Moonfire for 500 hit points by Superior Healing."))
        self.assertIsNone(st.active)

    def test_heal_by_target_breakdown(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 17:04:54 2026", "Thistle has joined the group."))
        st.ingest_line(line("Tue Sep 08 15:46:01 2026", "You pierce a sturdy skeleton for 61 points of damage."))
        st.ingest_line(line("Tue Sep 08 15:46:05 2026", "You healed Moonfire for 500 hit points by Superior Healing."))
        st.ingest_line(line("Tue Sep 08 15:46:06 2026", "You healed Thistle for 300 hit points by Superior Healing."))
        you_healing = st.active.healing["You"]
        self.assertEqual(you_healing.total, 800)
        targets = dict(you_healing.top_targets())
        self.assertEqual(targets["Moonfire"].total, 500)
        self.assertEqual(targets["Thistle"].total, 300)


class TestSpellDataFiltersInstantEffects(unittest.TestCase):
    """User-reported bug: 'Conflagration IX' (a damage nuke) sat in the
    Active buffs list forever showing "learning…", since a cast-begin line
    exists for every spell but a nuke never produces a wear-off line. Fixed
    using has_duration from the game's own spells_us.txt (formula/duration
    fields both 0 only for true instant effects), not a curated list."""

    def test_damage_nuke_is_never_tracked_as_a_buff(self):
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 21:36:16 2026", "You begin casting Conflagration IX."))
        self.assertEqual(st.active_buffs, {})

    def test_instant_heal_is_never_tracked_as_a_buff(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 17:41:20 2026", "You begin casting Superior Healing."))
        self.assertEqual(st.active_buffs, {})

    def test_real_buff_is_still_tracked(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 16:55:00 2026", "You begin casting Clarity."))
        self.assertIn("clarity", st.active_buffs)

    def test_real_charm_spell_still_tracked_as_a_buff_cast_too(self):
        # Charm itself is intercepted earlier by CharmCastEvent (a separate
        # system) — this just confirms the has_duration filter wouldn't
        # have excluded it either way, since charm genuinely has a duration.
        from dpsmeter import spelldata
        self.assertTrue(spelldata.get().has_duration("Cajoling Whispers"))

    def test_custom_fade_message_resolves_a_real_active_buff(self):
        # "Sacred Echo" only ever produces its own flavor text on expiry
        # ("The echo of healing fades away."), never the generic template —
        # this is the second half of the user's report: a buff that fell
        # off wasn't showing up in "What fell off" at all.
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 23:00:00 2026", "You begin casting Sacred Echo."))
        self.assertIn("sacred echo", st.active_buffs)
        st.ingest_line(line("Wed Sep 09 23:14:22 2026", "The echo of healing fades away."))
        self.assertNotIn("sacred echo", st.active_buffs)
        self.assertEqual(len(st.expired_buffs), 1)
        self.assertAlmostEqual(st.expired_buffs[0].actual_duration, 862.0, delta=1.0)


class TestGeneralBuffTracking(unittest.TestCase):
    def test_first_observation_learns_base_duration(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 16:55:00 2026", "You begin casting Clarity."))
        st.ingest_line(line("Tue Sep 08 17:11:40 2026", "Your Clarity spell has worn off."))  # 1000s later
        self.assertAlmostEqual(st.buff_base_durations["clarity"], 1000.0, delta=1.0)
        self.assertEqual(len(st.expired_buffs), 1)
        self.assertAlmostEqual(st.expired_buffs[0].actual_duration, 1000.0, delta=1.0)
        self.assertIsNone(st.expired_buffs[0].expected_duration)  # not known until AFTER this observation

    def test_second_cast_gets_a_live_countdown_from_learned_duration(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 16:55:00 2026", "You begin casting Clarity."))
        st.ingest_line(line("Tue Sep 08 17:11:40 2026", "Your Clarity spell has worn off."))
        st.ingest_line(line("Tue Sep 08 17:20:00 2026", "You begin casting Clarity."))
        cast_ts = st.active_buffs["clarity"][0]
        remaining = st.buff_remaining("clarity", now=cast_ts + 100)
        self.assertAlmostEqual(remaining, 900.0, delta=1.0)  # 1000s learned duration - 100s elapsed

    def test_aa_bonus_extends_learned_duration_for_future_casts(self):
        st = MeterState()
        st.aa_bonus_override = 0.50  # rank 4, +50%, set before either cast
        st.ingest_line(line("Tue Sep 08 16:55:00 2026", "You begin casting Clarity."))
        st.ingest_line(line("Tue Sep 08 17:11:40 2026", "Your Clarity spell has worn off."))  # 1000s WITH +50%
        # Base (AA-normalized) duration should be back-calculated to ~666.7s
        self.assertAlmostEqual(st.buff_base_durations["clarity"], 1000 / 1.5, delta=1.0)

        st.ingest_line(line("Tue Sep 08 17:20:00 2026", "You begin casting Clarity."))
        cast_ts = st.active_buffs["clarity"][0]
        remaining_at_cast = st.buff_remaining("clarity", now=cast_ts)
        self.assertAlmostEqual(remaining_at_cast, 1000.0, delta=1.0)  # base * 1.5 again, still +50%

    def test_ability_list_output_sets_detected_aa_bonus(self):
        st = MeterState()
        st.ingest_line(line("Thu Sep 10 20:41:08 2026", "Ability #21: Spell Casting Reinforcement"))
        st.ingest_line(line(
            "Thu Sep 10 20:41:08 2026",
            "Description: This passive ability increases the duration of beneficial spells that you cast by 50%.",
        ))
        self.assertAlmostEqual(st.aa_bonus_detected, 0.50)
        self.assertAlmostEqual(st.current_aa_bonus(), 0.50)

    def test_unrelated_ability_description_does_not_set_bonus(self):
        st = MeterState()
        st.ingest_line(line("Thu Sep 10 20:41:08 2026", "Ability #55: Permanent Illusion"))
        st.ingest_line(line(
            "Thu Sep 10 20:41:08 2026",
            "Description: This passive ability extends the duration of your beneficial illusion spells to 16.6 hours.",
        ))
        self.assertIsNone(st.aa_bonus_detected)

    def test_manual_override_wins_over_detected(self):
        st = MeterState()
        st.aa_bonus_detected = 0.50
        st.aa_bonus_override = 0.05
        self.assertAlmostEqual(st.current_aa_bonus(), 0.05)

    def test_interrupted_cast_does_not_get_learned_as_a_duration(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 16:55:00 2026", "You begin casting Clarity."))
        st.ingest_line(line("Tue Sep 08 16:55:02 2026", "Your Clarity spell is interrupted."))
        st.ingest_line(line("Tue Sep 08 17:11:40 2026", "Your Clarity spell has worn off."))
        # No matching active cast for this wear-off (it was cleared by the
        # interrupt) — must not fabricate a bogus duration from stale state.
        self.assertNotIn("clarity", st.buff_base_durations)
        self.assertEqual(st.expired_buffs, [])

    def test_what_fell_off_fastest(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 16:00:00 2026", "You begin casting Clarity."))
        st.ingest_line(line("Tue Sep 08 16:16:40 2026", "Your Clarity spell has worn off."))  # 1000s
        st.ingest_line(line("Tue Sep 08 16:20:00 2026", "You begin casting Celerity."))
        st.ingest_line(line("Tue Sep 08 16:20:30 2026", "Your Celerity spell has worn off."))  # 30s
        fastest = min(st.expired_buffs, key=lambda b: b.actual_duration)
        self.assertEqual(fastest.spell, "Celerity")


class TestCharmLossAudioSignal(unittest.TestCase):
    def test_natural_charm_end_logs_a_loss_event_for_your_own_pet_only(self):
        st = MeterState()
        st.ingest_line(line("Tue Sep 08 18:40:00 2026", "Moonfire has joined the group."))
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))
        st.ingest_line(line("Tue Sep 08 16:10:04 2026", "Moonfire begins casting Beguile."))
        st.ingest_line(line("Tue Sep 08 16:10:06 2026", "a dar ghoul knight has been charmed."))
        self.assertEqual(len(st.charm_loss_events), 0)

        st.ingest_line(line("Wed Sep 09 20:51:00 2026",
                             "Your Cajoling Whispers spell has worn off of a rock golem."))
        self.assertEqual(len(st.charm_loss_events), 1)  # yours only

        st.ingest_line(line("Tue Sep 08 16:10:10 2026", "Your Beguile spell has worn off of a dar ghoul knight."))
        self.assertEqual(len(st.charm_loss_events), 1)  # Moonfire's release doesn't count as yours

    def test_evac_releasing_your_charm_also_logs_a_loss_event(self):
        st = MeterState()
        st.ingest_line(line("Wed Sep 09 20:50:42 2026", "You begin casting Cajoling Whispers VIII."))
        st.ingest_line(line("Wed Sep 09 20:50:45 2026", "a rock golem has been charmed."))
        st.ingest_line(line("Wed Sep 09 20:51:00 2026", "You begin casting Lesser Evacuate IV."))
        st.ingest_line(line("Wed Sep 09 20:51:15 2026", "You have entered Befallen 4 (Refined)."))
        self.assertEqual(len(st.charm_loss_events), 1)


if __name__ == "__main__":
    unittest.main()
