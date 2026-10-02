"""Typed effect reads preserve live spatial authority and mixed-target behavior."""

from django.db import connection
from django.test.utils import CaptureQueriesContext

from spawns.actions.effects import (
    _spatially_valid_encounter_effect_q,
    active_combat_effects,
    active_combatant_effects,
)
from spawns.models import ActiveEffect, CombatEncounter, CombatParticipant, Mob, Player
from tests.base import WorldTestCase
from tests.combat_fixtures import create_combat_encounter


class EffectQueryTargetTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.mob = self.create_mob("Effect source", health=100, health_max=100)
        self.encounter = create_combat_encounter(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=self.mob,
        )
        self.effect = ActiveEffect.objects.create(
            world=self.spawn_world,
            encounter=self.encounter,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            source_mob=self.mob,
            target_player=self.player,
            effect="root",
            label="Rooted",
            remaining_rounds=3,
            duration_rounds=3,
            primitives=[{"type": "action_rule", "rule": "prevent", "actions": ["flee"]}],
        )

    def _mob_effect(self):
        return ActiveEffect.objects.create(
            world=self.spawn_world,
            encounter=self.encounter,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            source_player=self.player,
            target_mob=self.mob,
            effect="stun",
            label="Stunned",
        )

    def test_player_effect_keeps_mob_source_without_joining_mob_table(self):
        with CaptureQueriesContext(connection) as queries:
            payloads = active_combat_effects(self.player)
        self.assertEqual(len(queries), 1)
        self.assertNotIn('JOIN "spawns_mob"', queries[0]["sql"])
        self.assertEqual(len(payloads), 1)
        effect = payloads[0]
        self.assertEqual(effect["id"], self.effect.pk)
        self.assertEqual(effect["encounter_id"], self.encounter.pk)
        self.assertEqual(effect["source"], {"type": "mob", "id": self.mob.pk})
        self.assertEqual(effect["target"], {"type": "player", "id": self.player.pk})
        self.assertEqual(effect["remaining_rounds"], 3)
        self.assertEqual(effect["primitives"], self.effect.primitives)

    def test_stale_player_room_does_not_admit_effect(self):
        other_room = self.room.create_at("east")
        Player.objects.filter(pk=self.player.pk).update(room=other_room)
        self.assertEqual(self.player.room_id, self.room.pk)
        self.assertEqual(active_combat_effects(self.player), [])

    def test_stale_player_world_does_not_admit_effect_in_shared_room(self):
        Player.objects.filter(pk=self.player.pk).update(world=self.world)
        self.assertEqual(self.player.world_id, self.spawn_world.pk)
        self.assertEqual(active_combat_effects(self.player), [])

    def test_effect_world_must_match_player_world(self):
        ActiveEffect.objects.filter(pk=self.effect.pk).update(world=self.world)
        self.assertEqual(active_combat_effects(self.player), [])

    def test_encounter_world_must_match_player_world(self):
        CombatEncounter.objects.filter(pk=self.encounter.pk).update(world=self.world)
        self.assertEqual(active_combat_effects(self.player), [])

    def test_inactive_membership_does_not_admit_effect(self):
        CombatParticipant.objects.filter(player=self.player).update(is_active=False)
        self.assertEqual(active_combat_effects(self.player), [])

    def test_player_read_still_excludes_paused_and_finished_encounters(self):
        for status in (CombatEncounter.STATUS_PAUSED, CombatEncounter.STATUS_FINISHED):
            with self.subTest(status=status):
                CombatEncounter.objects.filter(pk=self.encounter.pk).update(status=status)
                self.assertEqual(active_combat_effects(self.player), [])

    def test_mixed_read_keeps_both_target_types_and_paused_effects(self):
        mob_effect = self._mob_effect()
        for status in (CombatEncounter.STATUS_ACTIVE, CombatEncounter.STATUS_PAUSED):
            with self.subTest(status=status):
                CombatEncounter.objects.filter(pk=self.encounter.pk).update(status=status)
                with self.assertNumQueries(1):
                    effects = active_combatant_effects([self.player, self.mob])
                self.assertEqual([row["id"] for row in effects[self.player.key]], [self.effect.pk])
                self.assertEqual([row["id"] for row in effects[self.mob.key]], [mob_effect.pk])

    def test_mixed_read_still_excludes_dead_or_pending_mobs(self):
        self._mob_effect()
        for values in ({"health": 0}, {"health": 100, "is_pending_deletion": True}):
            with self.subTest(values=values):
                Mob.objects.filter(pk=self.mob.pk).update(**values)
                effects = active_combatant_effects([self.player, self.mob])
                self.assertEqual(effects[self.mob.key], [])
                self.assertEqual([row["id"] for row in effects[self.player.key]], [self.effect.pk])

    def test_typed_mob_predicate_retains_mob_spatial_checks(self):
        mob_effect = self._mob_effect()
        with CaptureQueriesContext(connection) as queries:
            effects = list(ActiveEffect.objects.filter(
                target_mob=self.mob,
            ).filter(_spatially_valid_encounter_effect_q(target_type="mob")))
        self.assertEqual([row.pk for row in effects], [mob_effect.pk])
        self.assertEqual(len(queries), 1)
        self.assertNotIn('JOIN "spawns_player"', queries[0]["sql"])
        Mob.objects.filter(pk=self.mob.pk).update(room=self.room.create_at("east"))
        self.assertFalse(ActiveEffect.objects.filter(
            target_mob=self.mob,
        ).filter(_spatially_valid_encounter_effect_q(target_type="mob")).exists())
