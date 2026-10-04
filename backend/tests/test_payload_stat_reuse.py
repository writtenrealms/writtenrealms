"""Explicit payload snapshots reuse computed data without retaining stale state."""

from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext

from builders.models import Faction
from core.stat_system import compute_stats
from spawns.actions.effects import active_character_effects
from spawns.combat_encounters import locked_combat
from spawns.models import ActiveEffect
from spawns.state_payloads import (
    get_player_with_related,
    serialize_actor,
    serialize_char_from_player,
    serialize_room,
)
from tests.base import WorldTestCase
from tests.utils import apply_basic_stat_system


class PayloadStatReuseTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

    def _effect(self):
        return ActiveEffect.objects.create(
            world=self.spawn_world,
            target_player=self.player,
            source_player=self.player,
            effect="fortitude",
            label="Fortitude",
            remaining_rounds=3,
            duration_rounds=3,
            primitives=[{"type": "stat_modifier", "stat": "health_max", "amount": 7}],
        )

    @staticmethod
    def _stats(player, **kwargs):
        return compute_stats(player.level, player.archetype, char=player, world=player.world, **kwargs)

    def test_explicit_effects_include_empty_snapshot_and_do_not_persist(self):
        self._effect()
        player = get_player_with_related(self.player.pk)
        effects = active_character_effects(player)
        ordinary = self._stats(player)
        with self.assertNumQueries(0):
            reused = self._stats(player, character_effects=effects)
            without_effects = self._stats(player, character_effects=[])
        self.assertEqual(reused, ordinary)
        self.assertEqual(reused["health_max"], without_effects["health_max"] + 7)
        self.assertEqual(self._stats(player), ordinary)

    def test_explicit_effect_snapshot_bypasses_combat_stats_cache(self):
        self._effect()
        with locked_combat(keys=[self.player.key]) as context:
            player = context.actors[self.player.key]
            ordinary = self._stats(player)
            saved_cache = dict(context.stats_cache)
            without_effects = self._stats(player, character_effects=[])
            self.assertEqual(ordinary["health_max"], without_effects["health_max"] + 7)
            self.assertEqual(context.stats_cache, saved_cache)
            self.assertEqual(self._stats(player), ordinary)

    def test_actor_serialization_retains_existing_combat_stat_cache(self):
        self._effect()
        player = get_player_with_related(self.player.pk)
        with locked_combat(keys=[player.key]) as context:
            expected = self._stats(context.actors[player.key])
            with patch(
                "core.stat_system._compute_stats",
                side_effect=AssertionError("Combat stats should already be computed"),
            ):
                actor = serialize_actor(player, player.room)
            self.assertEqual(actor.health_max, expected["health_max"])

    def test_actor_and_self_char_share_effectful_stats_without_second_effect_read(self):
        effect = self._effect()
        player = get_player_with_related(self.player.pk)
        with CaptureQueriesContext(connection) as queries:
            actor = serialize_actor(player, player.room)
        character_effect_reads = [
            query["sql"] for query in queries
            if 'FROM "spawns_activeeffect"' in query["sql"]
            and '"spawns_combatencounter"' not in query["sql"]
        ]
        self.assertEqual(len(character_effect_reads), 1)
        self.assertEqual([row["id"] for row in actor.active_effects], [effect.pk])
        ordinary_char = serialize_char_from_player(player).model_dump()
        with CaptureQueriesContext(connection) as queries:
            reused_char = serialize_char_from_player(player, actor_payload=actor).model_dump()
        self.assertEqual(reused_char, ordinary_char)
        self.assertEqual(reused_char["health_max"], actor.health_max)
        self.assertFalse(any('FROM "spawns_activeeffect"' in query["sql"] for query in queries))

    def test_room_actor_reuse_preserves_other_player_stats_and_default_faction(self):
        self._effect()
        observer = self.create_player("Observer")
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        faction = Faction.objects.create(
            world=self.world, name="Local folk", code="folk", type="core",
            playable=True, is_default=True,
        )
        player = get_player_with_related(self.player.pk)
        actor = serialize_actor(player, player.room)
        arguments = (player.room, {player.room_id: player.room.key}, {})
        ordinary = serialize_room(*arguments, viewer=player, runtime_world=player.world)
        reused = serialize_room(*arguments, viewer=player, runtime_world=player.world, actor_payload=actor)
        # Fresh projections carry their own action-ordering timestamp.
        self.assertGreaterEqual(reused.actions_revision, ordinary.actions_revision)
        self.assertEqual(reused.model_dump(exclude={'actions_revision'}),
                         ordinary.model_dump(exclude={'actions_revision'}))
        chars = {char.id: char for char in reused.chars}
        self.assertEqual(chars[player.pk].health_max, actor.health_max)
        self.assertEqual(chars[player.pk].health_max, chars[observer.pk].health_max + 7)
        self.assertEqual(chars[player.pk].core_faction, faction.code)

    def test_default_faction_fallback_prefers_default_then_existing_order_in_one_read(self):
        older = Faction.objects.create(
            world=self.world, name="First folk", code="first", type="core", playable=True,
        )
        newer = Faction.objects.create(
            world=self.world, name="Default folk", code="default", type="core",
            playable=True, is_default=True,
        )
        player = get_player_with_related(self.player.pk)
        with self.assertNumQueries(1):
            self.assertEqual(player.factions["core"], newer.code)
        Faction.objects.filter(pk=newer.pk).update(is_default=False)
        with self.assertNumQueries(1):
            self.assertEqual(player.factions["core"], older.code)
        Faction.objects.filter(pk__in=[older.pk, newer.pk]).delete()
        with self.assertNumQueries(1):
            self.assertNotIn("core", player.factions)
