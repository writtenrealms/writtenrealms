from builders.models import Path, PathRoom
from config import constants as api_consts
from spawns.models import CombatEncounter, Mob
from spawns.tasks import run_mob_roaming
from tests.base import WorldTestCase
from worlds.models import Room, RoomFlag
from wr2_tests.utils import capture_game_messages


class TestMobRoaming(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.spawn_world.lifecycle = api_consts.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=["lifecycle"])
        self.world.config.default_roam_chance = 100
        self.world.config.save(update_fields=["default_roam_chance"])

    def _room(self, *, name: str, x: int, y: int, z: int = 0, zone=None):
        return Room.objects.create(
            world=self.world,
            zone=zone or self.zone,
            name=name,
            x=x,
            y=y,
            z=z,
        )

    def test_zone_roaming_moves_mob_with_roams_target(self):
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a patrol",
            roams=self.zone,
        )
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        observer = self.create_player("Observer", room=destination)
        observer.in_game = True
        observer.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 1)
        self.assertEqual(mob.room_id, destination.id)
        self.assertTrue(
            any(
                msg["player_key"] == self.player.key
                and msg["message"]["type"] == "notification.movement.exit"
                and msg["message"]["data"]["direction"] == "east"
                for msg in messages
            )
        )
        self.assertTrue(
            any(
                msg["player_key"] == observer.key
                and msg["message"]["type"] == "notification.movement.enter"
                and msg["message"]["data"]["direction"] == "west"
                for msg in messages
            )
        )

    def test_roaming_notifications_from_same_heartbeat_share_group(self):
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a patrol",
            roams=self.zone,
        )
        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a scout",
            roams=self.zone,
        )
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            roamed = run_mob_roaming()

        self.assertEqual(roamed, 2)
        movement_messages = [
            msg["message"]
            for msg in messages
            if msg["player_key"] == self.player.key
            and msg["message"]["type"] == "notification.movement.exit"
        ]
        self.assertEqual(len(movement_messages), 2)
        groups = {message.get("group") for message in movement_messages}
        self.assertEqual(len(groups), 1)
        group = groups.pop()
        self.assertIsNotNone(group)
        self.assertTrue(group.startswith("heartbeat.mob_roaming."))

    def test_room_loaded_mob_without_roams_target_stays_static(self):
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a sentry",
        )

        roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(mob.room_id, self.room.id)

    def test_path_roaming_only_uses_rooms_on_path(self):
        off_path = self._room(name="East Room", x=1, y=0)
        on_path = self._room(name="North Room", x=0, y=1)
        self.room.east = off_path
        self.room.north = on_path
        self.room.save(update_fields=["east", "north"])
        path = Path.objects.create(world=self.world, zone=self.zone, name="Patrol Path")
        PathRoom.objects.create(path=path, room=self.room)
        PathRoom.objects.create(path=path, room=on_path)
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a path guard",
            roams=path,
        )

        roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 1)
        self.assertEqual(mob.room_id, on_path.id)

    def test_roaming_skips_no_roam_destination(self):
        destination = self._room(name="East Room", x=1, y=0)
        RoomFlag.objects.create(room=destination, code=api_consts.ROOM_FLAG_NO_ROAM)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a blocked patrol",
            roams=self.zone,
        )

        roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(mob.room_id, self.room.id)

    def test_world_default_roam_chance_zero_disables_roaming(self):
        self.world.config.default_roam_chance = 0
        self.world.config.save(update_fields=["default_roam_chance"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a cautious patrol",
            roams=self.zone,
        )

        roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(mob.room_id, self.room.id)

    def test_roaming_skips_mobs_in_active_combat(self):
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a fighting patrol",
            roams=self.zone,
        )
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )

        roamed = run_mob_roaming(active_combat_mob_ids={mob.id})

        mob.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(mob.room_id, self.room.id)
