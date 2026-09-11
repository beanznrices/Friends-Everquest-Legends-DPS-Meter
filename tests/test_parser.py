"""Regression tests built from real lines pulled out of a live EverQuest
Legends character log (sampled with grep, never the whole multi-megabyte
file loaded at once). Character/player names have been replaced with
fictional ones; timestamps and game text are otherwise unmodified."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dpsmeter import parser


class TestTimestamp(unittest.TestCase):
    def test_parses_eq_timestamp(self):
        ts = parser.parse_timestamp("Tue Sep 08 15:45:50 2026")
        self.assertIsNotNone(ts)

    def test_garbage_returns_none(self):
        self.assertIsNone(parser.parse_timestamp("not a timestamp"))


class TestDamageParsing(unittest.TestCase):
    def test_plain_melee_no_ability(self):
        line = "[Tue Sep 08 15:46:01 2026] You pierce a sturdy skeleton for 61 points of damage."
        ev = parser.parse_line(line)
        self.assertIsInstance(ev, parser.DamageEvent)
        self.assertEqual(ev.actor, "You")
        self.assertEqual(ev.target, "sturdy skeleton")
        self.assertEqual(ev.amount, 61)
        self.assertIsNone(ev.ability)
        self.assertFalse(ev.is_crit)

    def test_singular_point(self):
        line = "[Tue Sep 08 15:46:02 2026] Emberclaw bashes a sturdy skeleton for 1 point of damage."
        ev = parser.parse_line(line)
        self.assertEqual(ev.amount, 1)
        self.assertEqual(ev.actor, "Emberclaw")

    def test_spell_damage_with_ability_and_crit(self):
        line = ("[Tue Sep 08 15:46:09 2026] You hit a skeletal excavator for 384 points of magic "
                "damage by Garrison's Mighty Mana Shock. (Critical)")
        ev = parser.parse_line(line)
        self.assertEqual(ev.amount, 384)
        self.assertEqual(ev.ability, "Garrison's Mighty Mana Shock")
        self.assertTrue(ev.is_crit)

    def test_four_digit_amount_not_truncated(self):
        # Real regression: \d{1,3}(?:,\d{3})* parsed "1567" as "156" — this
        # log never comma-formats individual hit amounts (confirmed: 0
        # comma-formatted single-hit lines, 414 real 4+ digit ones in one
        # session log), so the old pattern silently dropped a trailing digit
        # off every hit >= 1000.
        line = "[Wed Sep 09 21:00:03 2026] You hit an elemental warrior for 1567 points of fire damage by Conflagration."
        ev = parser.parse_line(line)
        self.assertEqual(ev.amount, 1567)

    def test_five_digit_amount_not_truncated(self):
        line = "[Wed Sep 09 15:56:59 2026] You hit ice boned skeleton for 13260 points of magic damage by Test."
        ev = parser.parse_line(line)
        self.assertEqual(ev.amount, 13260)

    def test_incoming_damage_to_self_is_kept_for_damage_taken_tracking(self):
        # Used to be dropped entirely, back when the meter only cared about
        # outgoing damage. Damage-taken tracking needs this line kept.
        line = "[Tue Sep 08 15:46:04 2026] A basalt gargoyle hits YOU for 24 points"
        ev = parser.parse_line(line)
        self.assertIsInstance(ev, parser.DamageEvent)
        self.assertEqual(ev.actor, "A basalt gargoyle")
        self.assertEqual(ev.target, "YOU")
        self.assertEqual(ev.amount, 24)

    def test_reflexive_target_still_ignored(self):
        line = "[Tue Sep 08 12:00:00 2026] a rock golem hits itself for 5 points of damage."
        self.assertIsNone(parser.parse_line(line))

    def test_incoming_damage_to_other_player_is_kept(self):
        line = "[Tue Sep 08 15:46:04 2026] A basalt gargoyle hits Stonewell for 27 points"
        ev = parser.parse_line(line)
        self.assertIsInstance(ev, parser.DamageEvent)
        self.assertEqual(ev.target, "Stonewell")
        self.assertEqual(ev.amount, 27)

    def test_less_common_melee_verbs_are_matched(self):
        for verb, line in [
            ("smites", "[Tue Sep 08 12:00:00 2026] Ashlyn smites a necro neophyte for 55 points of damage."),
            ("shoots", "[Tue Sep 08 12:00:00 2026] Ashlyn shoots a froglok for 42 points of damage."),
            ("strikes", "[Tue Sep 08 12:00:00 2026] Ashlyn strikes a ghoul for 33 points of damage."),
            ("bites", "[Tue Sep 08 12:00:00 2026] A shin ghoul bites Ashlyn for 12 points of damage."),
            ("frenzies", "[Tue Sep 08 12:00:00 2026] Emberclaw frenzies a hardened skeleton for 9 points of damage."),
        ]:
            ev = parser.parse_line(line)
            self.assertIsNotNone(ev, f"verb {verb!r} failed to match")
            self.assertGreater(ev.amount, 0)

    def test_damage_shield_attributed_to_you(self):
        line = "[Tue Sep 08 17:28:25 2026] Skeleton L`rodd is burned by YOUR flames for 3 points of non-melee damage."
        ev = parser.parse_line(line)
        self.assertIsInstance(ev, parser.DamageEvent)
        self.assertEqual(ev.actor, "You")
        self.assertEqual(ev.target, "Skeleton L`rodd")
        self.assertEqual(ev.amount, 3)
        self.assertEqual(ev.ability, "flames")

    def test_damage_shield_attributed_to_other_actor(self):
        line = "[Tue Sep 08 16:15:22 2026] Emberclaw is pierced by a sturdy skeleton's thorns for 6 points of non-melee damage."
        ev = parser.parse_line(line)
        self.assertIsInstance(ev, parser.DamageEvent)
        self.assertEqual(ev.actor, "a sturdy skeleton")
        self.assertEqual(ev.target, "Emberclaw")
        self.assertEqual(ev.ability, "thorns")


class TestCharmEvents(unittest.TestCase):
    def test_charm_cast_recognized(self):
        ev = parser.parse_line("[Wed Sep 09 20:50:42 2026] You begin casting Cajoling Whispers VIII.")
        self.assertIsInstance(ev, parser.CharmCastEvent)
        self.assertEqual(ev.spell, "Cajoling Whispers VIII")

    def test_non_charm_cast_is_a_generic_buff_cast(self):
        # Not ignored: general buff-duration tracking wants every cast, not
        # just charm/escape. See TestGenericBuffTracking.
        ev = parser.parse_line("[Wed Sep 09 20:50:42 2026] You begin casting Superior Healing.")
        self.assertIsInstance(ev, parser.GenericCastEvent)

    def test_charmed_confirmation(self):
        ev = parser.parse_line("[Wed Sep 09 20:50:45 2026] a rock golem has been charmed.")
        self.assertIsInstance(ev, parser.CharmedEvent)
        self.assertEqual(ev.mob, "a rock golem")

    def test_charm_worn_off(self):
        line = "[Wed Sep 09 20:32:21 2026] Your Cajoling Whispers spell has worn off of a dar ghoul knight."
        ev = parser.parse_line(line)
        self.assertIsInstance(ev, parser.CharmEndEvent)
        self.assertEqual(ev.mob, "a dar ghoul knight")

    def test_charm_interrupted_is_a_cast_failure(self):
        ev = parser.parse_line("[Wed Sep 09 20:50:02 2026] Your Cajoling Whispers spell is interrupted.")
        self.assertIsInstance(ev, parser.CharmCastFailedEvent)

    def test_unrelated_spell_interrupted_is_not_a_charm_failure(self):
        ev = parser.parse_line("[Wed Sep 09 20:50:09 2026] Your Clarity spell is interrupted.")
        self.assertNotIsInstance(ev, parser.CharmCastFailedEvent)
        self.assertIsInstance(ev, parser.GenericCastFailedEvent)

    def test_charm_resisted_is_a_cast_failure(self):
        ev = parser.parse_line("[Wed Sep 09 20:50:17 2026] A rock golem resisted your Cajoling Whispers VII!")
        self.assertIsInstance(ev, parser.CharmCastFailedEvent)

    def test_target_too_high_level_is_a_cast_failure(self):
        line = "[Wed Sep 09 20:50:17 2026] Your target is too high of a level for your charm spell."
        self.assertIsInstance(parser.parse_line(line), parser.CharmCastFailedEvent)

    def test_unrelated_spell_wearing_off_is_not_a_charm_end(self):
        # "Rest the Dead" / "Dominate Undead" (necro pet spells) and ordinary
        # buffs also produce "Your X spell has worn off of Y." lines — these
        # feed general buff tracking (BuffWornOffEvent), not charm-end.
        for line in [
            "[Wed Sep 09 14:27:31 2026] Your Rest the Dead spell has worn off of an urd ghoul wizard.",
            "[Wed Sep 09 14:42:22 2026] Your Dominate Undead spell has worn off of a greater ice bones.",
            "[Tue Sep 08 17:19:01 2026] Your Snails Healing spell has worn off of Duskrend.",
        ]:
            ev = parser.parse_line(line)
            self.assertNotIsInstance(ev, parser.CharmEndEvent)
            self.assertIsInstance(ev, parser.BuffWornOffEvent)


class TestEscapeEvents(unittest.TestCase):
    """Evacuate/Succor/Exodus — matched by keyword like charm spells, so every
    rank and zone-specific variant is covered without an exact-name list."""

    def test_lesser_evacuate_recognized(self):
        ev = parser.parse_line("[Tue Sep 08 16:10:04 2026] You begin casting Lesser Evacuate IV.")
        self.assertIsInstance(ev, parser.EscapeCastEvent)
        self.assertEqual(ev.caster, "You")
        self.assertEqual(ev.spell, "Lesser Evacuate IV")

    def test_zone_specific_evacuate_recognized(self):
        ev = parser.parse_line("[Wed Sep 09 17:03:13 2026] You begin casting Evacuate: Greater Faydark.")
        self.assertIsInstance(ev, parser.EscapeCastEvent)
        self.assertEqual(ev.spell, "Evacuate: Greater Faydark")

    def test_succor_and_exodus_recognized_by_keyword(self):
        for spell in ["Succor", "Lesser Succor", "Greater Succor", "Exodus"]:
            ev = parser.parse_line(f"[Wed Sep 09 17:03:13 2026] You begin casting {spell}.")
            self.assertIsInstance(ev, parser.EscapeCastEvent, f"{spell!r} not recognized")

    def test_party_members_evac_cast_recognized_third_person(self):
        ev = parser.parse_line("[Wed Sep 09 17:03:13 2026] Moonfire begins casting Succor.")
        self.assertIsInstance(ev, parser.EscapeCastEvent)
        self.assertEqual(ev.caster, "Moonfire")

    def test_evac_interrupted_is_a_cast_failure(self):
        ev = parser.parse_line("[Wed Sep 09 17:03:15 2026] Your Lesser Evacuate spell is interrupted.")
        self.assertIsInstance(ev, parser.EscapeCastFailedEvent)

    def test_zone_change_recognized(self):
        ev = parser.parse_line("[Tue Sep 08 16:10:19 2026] You have entered Befallen 4 (Refined).")
        self.assertIsInstance(ev, parser.ZoneChangeEvent)


class TestHealing(unittest.TestCase):
    def test_heal_with_overheal_parens(self):
        line = "[Tue Sep 08 17:41:20 2026] You healed Moonfire for 101 (1507) hit points by Superior Healing."
        ev = parser.parse_line(line)
        self.assertIsInstance(ev, parser.HealEvent)
        self.assertEqual(ev.actor, "You")
        self.assertEqual(ev.target, "Moonfire")
        self.assertEqual(ev.amount, 101)  # effective heal, not the overheal-inclusive number
        self.assertEqual(ev.ability, "Superior Healing")

    def test_heal_without_overheal_parens(self):
        line = "[Tue Sep 08 17:41:20 2026] You healed Moonfire for 1008 hit points by Greater Healing."
        ev = parser.parse_line(line)
        self.assertEqual(ev.amount, 1008)

    def test_reflexive_heal_target(self):
        line = "[Tue Sep 08 17:41:20 2026] a large plague rat healed itself for 0 (8) hit points by Lifespike."
        ev = parser.parse_line(line)
        self.assertIsInstance(ev, parser.HealEvent)
        self.assertEqual(ev.actor, "a large plague rat")
        self.assertEqual(ev.target, "itself")
        self.assertEqual(ev.amount, 0)


class TestGenericBuffTracking(unittest.TestCase):
    def test_ordinary_beneficial_cast_is_generic_not_charm_or_escape(self):
        ev = parser.parse_line("[Tue Sep 08 16:55:00 2026] You begin casting Clarity.")
        self.assertIsInstance(ev, parser.GenericCastEvent)
        self.assertEqual(ev.caster, "You")
        self.assertEqual(ev.spell, "Clarity")

    def test_third_person_generic_cast(self):
        ev = parser.parse_line("[Tue Sep 08 16:55:00 2026] Moonfire begins casting Celerity.")
        self.assertIsInstance(ev, parser.GenericCastEvent)
        self.assertEqual(ev.caster, "Moonfire")

    def test_generic_cast_interrupted(self):
        ev = parser.parse_line("[Tue Sep 08 16:55:09 2026] Your Clarity spell is interrupted.")
        self.assertIsInstance(ev, parser.GenericCastFailedEvent)
        self.assertEqual(ev.spell, "Clarity")

    def test_self_buff_worn_off_no_target_clause(self):
        ev = parser.parse_line("[Tue Sep 08 17:00:00 2026] Your Clarity spell has worn off.")
        self.assertIsInstance(ev, parser.BuffWornOffEvent)
        self.assertEqual(ev.spell, "Clarity")

    def test_buff_worn_off_with_target_clause_still_captured(self):
        # Duration is a property of the spell, not who it landed on.
        ev = parser.parse_line("[Tue Sep 08 17:00:00 2026] Your Clarity spell has worn off of Moonfire.")
        self.assertIsInstance(ev, parser.BuffWornOffEvent)
        self.assertEqual(ev.spell, "Clarity")

    def test_charm_worn_off_is_not_also_a_generic_buff_event(self):
        # Charm-specific handling must win; this must not double-fire.
        line = "[Wed Sep 09 20:32:21 2026] Your Cajoling Whispers spell has worn off of a dar ghoul knight."
        ev = parser.parse_line(line)
        self.assertIsInstance(ev, parser.CharmEndEvent)

    def test_custom_fade_message_recognized_via_real_spell_data(self):
        # "The echo of healing fades away." never matches "Your <spell>
        # spell has worn off[...]" — it's Sacred Echo's own flavor text
        # from spells_us_str.txt, only resolvable via spelldata's reverse
        # lookup. Real line, straight out of the log.
        ev = parser.parse_line("[Wed Sep 09 23:14:22 2026] The echo of healing fades away.")
        self.assertIsInstance(ev, parser.BuffWornOffEvent)

    def test_another_real_custom_fade_message(self):
        ev = parser.parse_line("[Thu Sep 10 21:30:24 2026] Your vulnerability fades.")
        self.assertIsInstance(ev, parser.BuffWornOffEvent)

    def test_unrecognized_text_is_not_treated_as_a_fade_message(self):
        ev = parser.parse_line("[Thu Sep 10 21:30:24 2026] This is not a real EQ Legends message at all.")
        self.assertIsNone(ev)


class TestAbilityListParsing(unittest.TestCase):
    def test_ability_entry_and_description(self):
        ev1 = parser.parse_line("[Thu Sep 10 20:41:08 2026] Ability #21: Spell Casting Reinforcement")
        self.assertIsInstance(ev1, parser.AbilityEntryEvent)
        self.assertEqual(ev1.name, "Spell Casting Reinforcement")

        line2 = ("[Thu Sep 10 20:41:08 2026] Description: This passive ability increases the duration "
                 "of beneficial spells that you cast by 50%.")
        ev2 = parser.parse_line(line2)
        self.assertIsInstance(ev2, parser.AbilityDescriptionEvent)
        self.assertIn("50%", ev2.text)

    def test_unprefixed_continuation_line_is_ignored(self):
        # Multi-line ability descriptions wrap onto a raw line with no
        # "[timestamp]" prefix at all — must not crash or misparse.
        line = "Spells that grant invulnerability and combat abilities are exempt from this extension."
        self.assertIsNone(parser.parse_line(line))


class TestNonDamageEvents(unittest.TestCase):
    def test_you_slain_a_mob(self):
        ev = parser.parse_line("[Tue Sep 08 15:46:09 2026] You have slain a skeletal excavator!")
        self.assertIsInstance(ev, parser.SlainEvent)
        self.assertEqual(ev.mob, "skeletal excavator")
        self.assertEqual(ev.killer, "You")

    def test_someone_else_gets_the_kill(self):
        ev = parser.parse_line("[Tue Sep 08 15:46:21 2026] A large plague rat has been slain by Emberclaw!")
        self.assertIsInstance(ev, parser.SlainEvent)
        self.assertEqual(ev.killer, "Emberclaw")

    def test_party_experience_line_means_grouped(self):
        ev = parser.parse_line("[Thu Sep 10 16:37:26 2026] You gain party experience! (1.151%)")
        self.assertIsInstance(ev, parser.PartyStatusEvent)
        self.assertTrue(ev.in_party)

    def test_solo_experience_line_means_not_grouped(self):
        ev = parser.parse_line("[Thu Sep 10 16:35:04 2026] You gain experience! (0.979%)")
        self.assertIsInstance(ev, parser.PartyStatusEvent)
        self.assertFalse(ev.in_party)

    def test_pet_leader_trick(self):
        ev = parser.parse_line("[Tue Sep 08 15:45:57 2026] Emberclaw says, 'My leader is Ashlyn.'")
        self.assertIsInstance(ev, parser.PetEvent)
        self.assertEqual(ev.pet, "Emberclaw")
        self.assertEqual(ev.master, "Ashlyn")

    def test_group_join_and_leave(self):
        self.assertIsInstance(
            parser.parse_line("[Tue Sep 08 17:04:54 2026] You have joined the group."),
            parser.GroupJoinEvent,
        )
        self.assertIsInstance(
            parser.parse_line("[Tue Sep 08 17:05:00 2026] You have been removed from the group."),
            parser.GroupLeaveEvent,
        )

    def test_member_join_and_leave(self):
        ev = parser.parse_line("[Tue Sep 08 17:05:00 2026] Thistle has left the group.")
        self.assertIsInstance(ev, parser.MemberLeaveEvent)
        self.assertEqual(ev.member, "Thistle")

    def test_irrelevant_line_returns_none(self):
        self.assertIsNone(parser.parse_line(
            "[Tue Sep 08 15:45:50 2026] a large plague rat's skin turns hard as wood."
        ))


if __name__ == "__main__":
    unittest.main()
