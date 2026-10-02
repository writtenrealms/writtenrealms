"""Heartbeat combat tags collect both effect endpoints with one spatial read."""

from unittest.mock import patch

from config import constants
from spawns.actions.effects import combat_tagged_actor_ids
from spawns.models import ActiveEffect, CombatEncounter, Mob, Player
from tests.base import WorldTestCase
from tests.combat_fixtures import create_combat_encounter
from worlds.models import World


class CombatTaggedActorLoadingTests(WorldTestCase):
    def setUp(self):
        reconciliation = patch("spawns.tasks.reconcile_combat_room")
        reconciliation.start()
        self.addCleanup(reconciliation.stop)
        super().setUp()
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        self.mob = self.create_mob("Combat mob", health=100, health_max=100)
        self.encounter = create_combat_encounter(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=self.mob,
        )

    def _effect(self, target, *, source=None, scope=ActiveEffect.SCOPE_CHARACTER, **values):
        return ActiveEffect.objects.create(
            world=target.world,
            encounter=self.encounter if scope == ActiveEffect.SCOPE_ENCOUNTER else None,
            source_player=source if isinstance(source, Player) else None,
            source_mob=source if isinstance(source, Mob) else None,
            target_player=target if isinstance(target, Player) else None,
            target_mob=target if isinstance(target, Mob) else None,
            scope=scope,
            effect="dot",
            label="Damage over time",
            is_hostile=True,
            **values,
        )

    def test_mixed_endpoints_scopes_and_duplicates_use_one_query(self):
        source_player = self.create_player("Character effect source")
        target_mob = self.create_mob("Character effect target", health=100, health_max=100)
        target_player = self.create_player("Source-free effect target")
        target_player.in_game = True
        target_player.save(update_fields=["in_game"])
        self._effect(self.mob, source=self.player, scope=ActiveEffect.SCOPE_ENCOUNTER)
        self._effect(self.player, source=self.mob, scope=ActiveEffect.SCOPE_ENCOUNTER)
        self._effect(target_mob, source=source_player)
        self._effect(target_player)
        expected = (
            {self.player.id, source_player.id, target_player.id},
            {self.mob.id, target_mob.id},
        )
        with self.assertNumQueries(1):
            self.assertEqual(combat_tagged_actor_ids(), expected)

        # Repeated effects and participant joins must not add endpoints or
        # turn one heartbeat discovery into a query per effect.
        for _ in range(40):
            self._effect(self.mob, source=self.player, scope=ActiveEffect.SCOPE_ENCOUNTER)
        for status in (CombatEncounter.STATUS_ACTIVE, CombatEncounter.STATUS_PAUSED):
            with self.subTest(status=status):
                CombatEncounter.objects.filter(pk=self.encounter.pk).update(status=status)
                with self.assertNumQueries(1):
                    self.assertEqual(combat_tagged_actor_ids(), expected)

    def test_world_scope_filters_both_endpoints(self):
        self._effect(self.mob, source=self.player)
        with self.assertNumQueries(1):
            self.assertEqual(
                combat_tagged_actor_ids(world_ids=[self.spawn_world.pk]),
                ({self.player.pk}, {self.mob.pk}),
            )
        with self.assertNumQueries(1):
            self.assertEqual(combat_tagged_actor_ids(world_ids=[self.world.pk]), (set(), set()))
        with self.assertNumQueries(0):
            self.assertEqual(combat_tagged_actor_ids(world_ids=[]), (set(), set()))

    def test_nonhostile_effect_and_stopped_world_do_not_tag_either_endpoint(self):
        effect = self._effect(self.mob, source=self.player)
        ActiveEffect.objects.filter(pk=effect.pk).update(is_hostile=False)
        self.assertEqual(combat_tagged_actor_ids(), (set(), set()))
        ActiveEffect.objects.filter(pk=effect.pk).update(is_hostile=True)
        World.objects.filter(pk=self.spawn_world.pk).update(lifecycle=constants.WORLD_LIFECYCLE_STOPPED)
        self.assertEqual(combat_tagged_actor_ids(), (set(), set()))

    def test_invalid_character_target_excludes_its_source_too(self):
        for target, source, model, changes in (
            (self.player, self.mob, Player, {"in_game": False}),
            (self.player, self.mob, Player, {"room_id": None}),
            (self.player, self.mob, Player, {"world_id": self.world.pk}),
            (self.mob, self.player, Mob, {"health": 0}),
            (self.mob, self.player, Mob, {"is_pending_deletion": True}),
            (self.mob, self.player, Mob, {"world_id": self.world.pk}),
        ):
            with self.subTest(target=target.key, changes=changes):
                previous = {field: getattr(target, field) for field in changes}
                effect = self._effect(target, source=source)
                model.objects.filter(pk=target.pk).update(**changes)
                with self.assertNumQueries(1):
                    self.assertEqual(combat_tagged_actor_ids(), (set(), set()))
                model.objects.filter(pk=target.pk).update(**previous)
                effect.delete()

    def test_encounter_effect_requires_current_room_world_and_active_membership(self):
        other_room = self.room.create_at("east")
        for target, source, model in (
            (self.player, self.mob, Player),
            (self.mob, self.player, Mob),
        ):
            effect = self._effect(target, source=source, scope=ActiveEffect.SCOPE_ENCOUNTER)
            for changes in ({"room_id": other_room.pk}, {"world_id": self.world.pk}):
                with self.subTest(target=target.key, changes=changes):
                    previous = {field: getattr(target, field) for field in changes}
                    model.objects.filter(pk=target.pk).update(**changes)
                    self.assertEqual(combat_tagged_actor_ids(), (set(), set()))
                    model.objects.filter(pk=target.pk).update(**previous)
            member = self.encounter.participants.get(**{"player" if model is Player else "mob": target})
            self.encounter.participants.filter(pk=member.pk).update(is_active=False)
            self.assertEqual(combat_tagged_actor_ids(), (set(), set()))
            self.encounter.participants.filter(pk=member.pk).update(is_active=True)
            effect.delete()

    def test_finished_encounter_excludes_both_endpoints(self):
        self._effect(self.mob, source=self.player, scope=ActiveEffect.SCOPE_ENCOUNTER)
        self.encounter.participants.update(is_active=False, current_target=None)
        CombatEncounter.objects.filter(pk=self.encounter.pk).update(status=CombatEncounter.STATUS_FINISHED)
        self.assertEqual(combat_tagged_actor_ids(), (set(), set()))
