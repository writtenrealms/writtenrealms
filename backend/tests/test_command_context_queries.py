from spawns.handlers.registry import (
    ActorNotFoundError,
    PlayerNotFoundError,
    _resolve_command_actor,
)
from spawns.models import Player, PlayerConfig
from tests.base import WorldTestCase


class TestCommandActorQueries(WorldTestCase):
    def test_player_context_loads_runtime_world_room_and_zone_in_one_query(self):
        with self.assertNumQueries(1):
            actor, player, mob, room, zone, world = _resolve_command_actor(
                actor_type="player", actor_id=self.player.pk, payload={}
            )
            self.assertIs(actor, player)
            self.assertIsNone(mob)
            self.assertEqual(room, self.room)
            self.assertEqual(zone, self.zone)
            self.assertEqual(world, self.spawn_world)
            self.assertEqual(room.world_id, self.world.pk)
            self.assertIs(player.world, world)
            self.assertIs(player.room, room)
            self.assertIs(room.zone, zone)

    def test_mob_context_loads_runtime_world_room_and_zone_in_one_query(self):
        expected_mob = self.create_mob("Wanderer")

        with self.assertNumQueries(1):
            actor, player, mob, room, zone, world = _resolve_command_actor(
                actor_type="mob", actor_id=expected_mob.pk, payload={}
            )
            self.assertIs(actor, mob)
            self.assertIsNone(player)
            self.assertEqual(room, self.room)
            self.assertEqual(zone, self.zone)
            self.assertEqual(world, self.spawn_world)
            self.assertIs(mob.world, world)
            self.assertIs(mob.room, room)
            self.assertIs(room.zone, zone)

    def test_player_without_room_keeps_runtime_world(self):
        Player.objects.filter(pk=self.player.pk).update(room=None)

        with self.assertNumQueries(1):
            actor, player, mob, room, zone, world = _resolve_command_actor(
                actor_type="player", actor_id=self.player.pk, payload={}
            )
            self.assertIs(actor, player)
            self.assertIsNone(mob)
            self.assertIsNone(room)
            self.assertIsNone(zone)
            self.assertEqual(world, self.spawn_world)

    def test_missing_embodied_actors_preserve_not_found_errors(self):
        for actor_type, error_type in (
            ("player", PlayerNotFoundError),
            ("mob", ActorNotFoundError),
        ):
            with self.subTest(actor_type=actor_type), self.assertNumQueries(1):
                with self.assertRaises(error_type) as raised:
                    _resolve_command_actor(
                        actor_type=actor_type, actor_id=-1, payload={}
                    )
                self.assertEqual(raised.exception.actor_type, actor_type)
                self.assertEqual(raised.exception.actor_id, -1)


class TestPlayerConfigSaveQueries(WorldTestCase):
    def test_save_with_existing_config_does_not_load_config(self):
        player = Player.objects.get(pk=self.player.pk)
        config_id = player.config_id
        self.assertIsNotNone(config_id)
        self.assertNotIn("config", player._state.fields_cache)
        player.stamina += 1

        with self.assertNumQueries(1):
            player.save(update_fields=["stamina"])

        self.assertEqual(player.config_id, config_id)
        self.assertNotIn("config", player._state.fields_cache)

    def test_new_player_without_config_uses_existing_default(self):
        config_id = self.player.config_id
        config_count = PlayerConfig.objects.count()

        player = self.create_player("New arrival")

        player.refresh_from_db()
        self.assertEqual(player.config_id, config_id)
        self.assertEqual(PlayerConfig.objects.count(), config_count)

    def test_missing_default_config_is_created_and_assigned(self):
        Player.objects.update(config=None)
        PlayerConfig.objects.all().delete()

        player = self.create_player("First configured arrival")

        player.refresh_from_db()
        self.assertEqual(PlayerConfig.objects.count(), 1)
        self.assertIsNotNone(player.config_id)
        self.assertFalse(player.config.room_brief)
        self.assertFalse(player.config.combat_brief)
