from unittest.mock import patch

from django.utils import timezone

from spawns.actions.mob_movement import load_player_escape_encounters
from spawns.actions.pvp import active_pvp_participation
from spawns.combat_encounters import engage_locked, locked_combat
from spawns.combat_rounds import detach_actor
from spawns.models import CombatEncounter, DuelMatch
from tests.base import WorldTestCase
from tests.combat_fixtures import create_combat_encounter


class TestCombatMembershipQueries(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.health = 100
        self.player.in_game = True
        self.player.save(update_fields=['health', 'in_game'])
        self.mob = self.create_mob('Opponent', health=100, health_max=100)

    def _encounter(self, **kwargs):
        return create_combat_encounter(
            world=self.spawn_world, room=self.room, player=self.player,
            mob=self.mob, status=CombatEncounter.STATUS_ACTIVE, **kwargs,
        )

    def _escape(self):
        return load_player_escape_encounters(
            player=self.player, origin_room_id=self.room.pk,
        )

    def test_owned_actor_without_membership_needs_no_discovery_queries(self):
        with locked_combat(keys=[self.player.key]):
            with self.assertNumQueries(0):
                self.assertEqual(self._escape(), ())
                self.assertIsNone(active_pvp_participation(self.player, room=self.room))
                self.assertIsNone(active_pvp_participation(self.player, lock=True))

    def test_without_context_each_helper_uses_its_existing_query(self):
        with self.assertNumQueries(1):
            self.assertEqual(self._escape(), ())
        with self.assertNumQueries(1):
            self.assertIsNone(active_pvp_participation(self.player, room=self.room))

    def test_unrelated_context_cannot_hide_existing_membership(self):
        expected = self._encounter()
        other = self.create_player('Other')
        with locked_combat(keys=[other.key]):
            with self.assertNumQueries(1):
                encounters = self._escape()
            self.assertEqual([row.pk for row in encounters], [expected.pk])
            with self.assertNumQueries(1):
                self.assertIsNone(active_pvp_participation(self.player, room=self.room))

    def test_positive_context_preserves_opponent_query_and_results(self):
        expected = self._encounter()
        with locked_combat(keys=[self.player.key]):
            with self.assertNumQueries(1):
                encounters = self._escape()
            self.assertEqual([row.pk for row in encounters], [expected.pk])
            self.assertEqual(
                [member.mob_id for member in encounters[0]._escape_opponents],
                [self.mob.pk],
            )
            with self.assertNumQueries(1):
                self.assertIsNone(active_pvp_participation(self.player, room=self.room))

    def test_positive_pvp_membership_preserves_query_and_filters(self):
        match = DuelMatch.objects.create(
            base_world=self.world, template_world=self.world,
            expires_at=timezone.now(),
        )
        expected = self._encounter(duel_match=match)
        other_room = self.room.create_at('east')
        with locked_combat(keys=[self.player.key]):
            with self.assertNumQueries(1):
                member = active_pvp_participation(self.player, room=self.room, lock=True)
            self.assertEqual(member.encounter_id, expected.pk)
            with self.assertNumQueries(1):
                self.assertIsNone(active_pvp_participation(self.player, room=other_room))

    def test_join_after_empty_lookup_is_visible_within_same_context(self):
        with locked_combat(keys=[self.player.key, self.mob.key]) as context:
            with self.assertNumQueries(0):
                self.assertEqual(self._escape(), ())
            expected, *_ = engage_locked(
                context, context.actors[self.player.key], context.actors[self.mob.key],
            )
            with self.assertNumQueries(1):
                encounters = self._escape()
            self.assertEqual([row.pk for row in encounters], [expected.pk])

    def test_stale_membership_is_detached_before_empty_result(self):
        self._encounter()
        with locked_combat(keys=[self.player.key]) as context:
            context.actors[self.player.key].health = 0
            with patch('spawns.combat_rounds.detach_actor', wraps=detach_actor) as detach:
                self.assertEqual(self._escape(), ())
            self.assertTrue(any(call.args[1] == self.player.key for call in detach.call_args_list))
            self.assertIsNone(context.participant(self.player.key))
            with self.assertNumQueries(0):
                self.assertIsNone(active_pvp_participation(self.player, room=self.room))
