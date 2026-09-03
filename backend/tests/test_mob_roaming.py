from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext

from builders.models import Path, PathRoom, SpawnPlan, SpawnPlanRun, SpawnPlacement
from config import constants as api_consts
from spawns.models import CombatEncounter, DoorState, Mob
from spawns.tasks import run_mob_roaming
from tests.base import WorldTestCase
from worlds.models import Door, Doorway, Room, RoomFlag, World, WorldConfig, Zone
from tests.utils import capture_game_messages


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

    def _cohort(self):
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="sparring-patrols",
            name="Sparring Patrols",
        )
        run = SpawnPlanRun.objects.create(
            spawn_world=self.spawn_world,
            plan=plan,
            seed="test",
        )
        leader_placement = SpawnPlacement.objects.create(
            run=run,
            entry_slug="sparabaras",
            slot_index=0,
            room=self.room,
            source_type="mobdefinition",
            source_slug="sparabara",
            state={
                "cohort_slug": "sparring-path-patrol",
                "cohort_role": "leader",
            },
        )
        follower_placement = SpawnPlacement.objects.create(
            run=run,
            entry_slug="archers",
            slot_index=0,
            room=self.room,
            source_type="mobdefinition",
            source_slug="persian-archer",
            parent_entry_slug="sparabaras",
            parent_slot_index=0,
            state={
                "cohort_slug": "sparring-path-patrol",
                "cohort_role": "follower",
            },
        )
        group_id = "cohort:sparring-path-patrol:0"
        leader = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a sparabara",
            keywords="sparabara",
            roams=self.zone,
            group_id=group_id,
            spawn_placement=leader_placement,
        )
        follower = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a persian archer",
            keywords="archer",
            roams=self.zone,
            group_id=group_id,
            spawn_placement=follower_placement,
        )
        return leader, follower

    def _placed_roamer(
        self,
        *,
        room,
        roams,
        plan_default_roam_chance,
        plan_zone=None,
        mob_roam_chance=0,
        slug="layered-roaming",
        spawn_world=None,
    ):
        plan = SpawnPlan.objects.create(
            world=room.world,
            zone=plan_zone or room.zone,
            slug=slug,
            name=slug.replace("-", " ").title(),
            default_roam_chance=plan_default_roam_chance,
        )
        run = SpawnPlanRun.objects.create(
            spawn_world=spawn_world or self.spawn_world,
            plan=plan,
            seed="test",
        )
        placement = SpawnPlacement.objects.create(
            run=run,
            entry_slug="patrol",
            slot_index=0,
            room=room,
            source_type="mobdefinition",
            source_slug="patrol",
        )
        return Mob.objects.create(
            world=spawn_world or self.spawn_world,
            room=room,
            name="a layered patrol",
            roam_chance=mob_roam_chance,
            roams=roams,
            spawn_placement=placement,
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

    def test_roaming_rechecks_door_after_selecting_exit(self):
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        doorway = Doorway.objects.create(
            world=self.world,
            default_state=api_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=doorway,
            direction=api_consts.DIRECTION_EAST,
            from_room=self.room,
            to_room=destination,
        )
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a cautious patrol",
            roams=self.zone,
        )

        def close_door_then_choose(options):
            DoorState.objects.update_or_create(
                doorway=doorway,
                world=self.spawn_world,
                defaults={"state": api_consts.DOOR_STATE_CLOSED},
            )
            return options[0]

        with patch(
            "spawns.tasks.random.choice",
            side_effect=close_door_then_choose,
        ):
            roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(mob.room_id, self.room.id)

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

    def test_roaming_hostile_mobs_aggro_players_in_destination_room(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        self.player.room = destination
        self.player.in_game = True
        self.player.health = 30
        self.player.save(update_fields=["room", "in_game", "health"])
        archer = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a persian archer",
            keywords="persian archer",
            health=20,
            health_max=20,
            attack_power=4,
            aggression=api_consts.MOB_AGGRESSION_ALL,
            target_priority=-1,
            roams=self.zone,
        )
        sparabara = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a sparabara",
            keywords="sparabara",
            health=20,
            health_max=20,
            attack_power=4,
            aggression=api_consts.MOB_AGGRESSION_ALL,
            target_priority=1,
            roams=self.zone,
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                with capture_game_messages() as messages:
                    roamed = run_mob_roaming()

        archer.refresh_from_db()
        sparabara.refresh_from_db()
        self.assertEqual(roamed, 2)
        self.assertEqual(archer.room_id, destination.id)
        self.assertEqual(sparabara.room_id, destination.id)
        active_encounters = CombatEncounter.objects.filter(
            player=self.player,
            mob__in=[archer, sparabara],
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(active_encounters.count(), 2)
        self.assertEqual(schedule_mock.call_count, 2)

        engage_messages = [
            msg["message"]
            for msg in messages
            if msg["player_key"] == self.player.key
            and msg["message"].get("type") == "cmd.kill.success"
        ]
        self.assertEqual(len(engage_messages), 2)
        self.assertEqual(
            {message["data"]["target"]["key"] for message in engage_messages},
            {archer.key, sparabara.key},
        )
        self.assertTrue(
            all(
                message["data"]["actor"]["target"]["key"] == sparabara.key
                for message in engage_messages
            )
        )

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

    def test_mob_roam_chance_overrides_plan_zone_and_world_defaults(self):
        self.world.config.default_roam_chance = 0
        self.world.config.save(update_fields=["default_roam_chance"])
        self.zone.default_roam_chance = 0
        self.zone.save(update_fields=["default_roam_chance"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = self._placed_roamer(
            room=self.room,
            roams=self.zone,
            plan_default_roam_chance=0,
            mob_roam_chance=100,
        )

        roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 1)
        self.assertEqual(mob.room_id, destination.id)

    def test_plan_roam_default_overrides_target_zone_and_world_defaults(self):
        self.world.config.default_roam_chance = 0
        self.world.config.save(update_fields=["default_roam_chance"])
        self.zone.default_roam_chance = 0
        self.zone.save(update_fields=["default_roam_chance"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = self._placed_roamer(
            room=self.room,
            roams=self.zone,
            plan_default_roam_chance=100,
        )

        roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 1)
        self.assertEqual(mob.room_id, destination.id)

    def test_target_zone_roam_default_overrides_world_default(self):
        self.world.config.default_roam_chance = 0
        self.world.config.save(update_fields=["default_roam_chance"])
        self.zone.default_roam_chance = 100
        self.zone.save(update_fields=["default_roam_chance"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = self._placed_roamer(
            room=self.room,
            roams=self.zone,
            plan_default_roam_chance=None,
        )

        roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 1)
        self.assertEqual(mob.room_id, destination.id)

    def test_null_plan_and_zone_roam_defaults_fall_back_to_world(self):
        self.zone.default_roam_chance = None
        self.zone.save(update_fields=["default_roam_chance"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = self._placed_roamer(
            room=self.room,
            roams=self.zone,
            plan_default_roam_chance=None,
        )

        roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 1)
        self.assertEqual(mob.room_id, destination.id)

    def test_zero_plan_roam_default_disables_zone_and_world_fallbacks(self):
        self.zone.default_roam_chance = 100
        self.zone.save(update_fields=["default_roam_chance"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = self._placed_roamer(
            room=self.room,
            roams=self.zone,
            plan_default_roam_chance=0,
        )

        roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(mob.room_id, self.room.id)

    def test_zero_plan_roam_default_disables_cohort_roaming(self):
        self.zone.default_roam_chance = 100
        self.zone.save(update_fields=["default_roam_chance"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        leader, follower = self._cohort()
        plan = leader.spawn_placement.run.plan
        plan.default_roam_chance = 0
        plan.save(update_fields=["default_roam_chance"])

        roamed = run_mob_roaming()

        leader.refresh_from_db()
        follower.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(leader.room_id, self.room.id)
        self.assertEqual(follower.room_id, self.room.id)

    def test_zero_zone_roam_default_disables_world_fallback(self):
        self.zone.default_roam_chance = 0
        self.zone.save(update_fields=["default_roam_chance"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = self._placed_roamer(
            room=self.room,
            roams=self.zone,
            plan_default_roam_chance=None,
        )

        roamed = run_mob_roaming()

        mob.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(mob.room_id, self.room.id)

    def test_plan_roam_default_edit_affects_existing_mob_next_heartbeat(self):
        self.world.config.default_roam_chance = 0
        self.world.config.save(update_fields=["default_roam_chance"])
        self.zone.default_roam_chance = 0
        self.zone.save(update_fields=["default_roam_chance"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = self._placed_roamer(
            room=self.room,
            roams=self.zone,
            plan_default_roam_chance=0,
        )

        self.assertEqual(run_mob_roaming(), 0)
        mob.refresh_from_db()
        self.assertEqual(mob.room_id, self.room.id)

        plan = mob.spawn_placement.run.plan
        plan.default_roam_chance = 100
        plan.save(update_fields=["default_roam_chance"])

        self.assertEqual(run_mob_roaming(), 1)
        mob.refresh_from_db()
        self.assertEqual(mob.room_id, destination.id)

    def test_target_zone_roam_default_edit_affects_existing_mob_next_heartbeat(self):
        self.world.config.default_roam_chance = 0
        self.world.config.save(update_fields=["default_roam_chance"])
        self.zone.default_roam_chance = 0
        self.zone.save(update_fields=["default_roam_chance"])
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        mob = self._placed_roamer(
            room=self.room,
            roams=self.zone,
            plan_default_roam_chance=None,
        )

        self.assertEqual(run_mob_roaming(), 0)
        mob.refresh_from_db()
        self.assertEqual(mob.room_id, self.room.id)

        self.zone.default_roam_chance = 100
        self.zone.save(update_fields=["default_roam_chance"])

        self.assertEqual(run_mob_roaming(), 1)
        mob.refresh_from_db()
        self.assertEqual(mob.room_id, destination.id)

    def test_roam_chance_resolution_query_count_does_not_grow_per_mob(self):
        self.zone.default_roam_chance = 0
        self.zone.save(update_fields=["default_roam_chance"])
        self._placed_roamer(
            room=self.room,
            roams=self.zone,
            plan_default_roam_chance=None,
            slug="query-baseline",
        )
        ContentType.objects.get_for_models(Path, Zone)

        with CaptureQueriesContext(connection) as baseline_queries:
            self.assertEqual(run_mob_roaming(), 0)

        for index in range(40):
            self._placed_roamer(
                room=self.room,
                roams=self.zone,
                plan_default_roam_chance=0 if index % 2 else None,
                slug=f"query-scale-{index}",
            )
        ContentType.objects.get_for_models(Path, Zone)

        with CaptureQueriesContext(connection) as scaled_queries:
            self.assertEqual(run_mob_roaming(), 0)

        self.assertLessEqual(len(baseline_queries), 6)
        self.assertLessEqual(len(scaled_queries), 6)
        self.assertLessEqual(
            len(scaled_queries),
            len(baseline_queries) + 1,
        )

    def test_zone_and_path_roam_defaults_use_target_zone_not_plan_zone(self):
        self.world.config.default_roam_chance = 0
        self.world.config.save(update_fields=["default_roam_chance"])
        self.zone.default_roam_chance = 0
        self.zone.save(update_fields=["default_roam_chance"])
        target_zone = self.world.zones.create(
            name="Patrol Grounds",
            default_roam_chance=100,
        )
        zone_origin = self._room(
            name="Zone Origin",
            x=10,
            y=0,
            zone=target_zone,
        )
        zone_destination = self._room(
            name="Zone Destination",
            x=11,
            y=0,
            zone=target_zone,
        )
        zone_origin.east = zone_destination
        zone_origin.save(update_fields=["east"])
        path_origin = self._room(
            name="Path Origin",
            x=20,
            y=0,
            zone=target_zone,
        )
        path_destination = self._room(
            name="Path Destination",
            x=21,
            y=0,
            zone=target_zone,
        )
        path_origin.east = path_destination
        path_origin.save(update_fields=["east"])
        path = Path.objects.create(
            world=self.world,
            zone=target_zone,
            name="Cross-Zone Patrol",
        )
        PathRoom.objects.create(path=path, room=path_origin)
        PathRoom.objects.create(path=path, room=path_destination)
        zone_mob = self._placed_roamer(
            room=zone_origin,
            roams=target_zone,
            plan_default_roam_chance=None,
            plan_zone=self.zone,
            slug="zone-target-patrol",
        )
        path_mob = self._placed_roamer(
            room=path_origin,
            roams=path,
            plan_default_roam_chance=None,
            plan_zone=self.zone,
            slug="path-target-patrol",
        )

        roamed = run_mob_roaming()

        zone_mob.refresh_from_db()
        path_mob.refresh_from_db()
        self.assertEqual(roamed, 2)
        self.assertEqual(zone_mob.room_id, zone_destination.id)
        self.assertEqual(path_mob.room_id, path_destination.id)

    def test_instance_plan_and_zone_defaults_override_inherited_world_default(self):
        self.world.config.default_roam_chance = 100
        self.world.config.save(update_fields=["default_roam_chance"])
        instance_template = World.objects.new_world(
            name="Layered Roaming Instance",
            author=self.user,
            config=WorldConfig.objects.create(default_roam_chance=100),
            is_multiplayer=True,
            instance_of=self.world,
        )
        plan_target_zone = instance_template.zones.get()
        plan_target_zone.default_roam_chance = 100
        plan_target_zone.save(update_fields=["default_roam_chance"])
        plan_origin = plan_target_zone.rooms.get()
        plan_destination = Room.objects.create(
            world=instance_template,
            zone=plan_target_zone,
            name="Plan Destination",
            x=plan_origin.x + 1,
            y=plan_origin.y,
            z=plan_origin.z,
        )
        plan_origin.east = plan_destination
        plan_origin.save(update_fields=["east"])

        zone_target = instance_template.zones.create(
            name="Quiet Instance Zone",
            default_roam_chance=0,
        )
        zone_origin = Room.objects.create(
            world=instance_template,
            zone=zone_target,
            name="Zone Origin",
            x=10,
            y=0,
            z=0,
        )
        zone_destination = Room.objects.create(
            world=instance_template,
            zone=zone_target,
            name="Zone Destination",
            x=11,
            y=0,
            z=0,
        )
        zone_origin.east = zone_destination
        zone_origin.save(update_fields=["east"])

        spawned_instance = instance_template.create_spawn_world(
            instance_ref="layered-roaming-instance",
            leader=self.player,
        )
        spawned_instance.lifecycle = api_consts.WORLD_LIFECYCLE_RUNNING
        spawned_instance.save(update_fields=["lifecycle"])
        plan_mob = self._placed_roamer(
            room=plan_origin,
            roams=plan_target_zone,
            plan_default_roam_chance=0,
            slug="instance-plan-default",
            spawn_world=spawned_instance,
        )
        zone_mob = self._placed_roamer(
            room=zone_origin,
            roams=zone_target,
            plan_default_roam_chance=None,
            slug="instance-zone-default",
            spawn_world=spawned_instance,
        )

        roamed = run_mob_roaming()

        plan_mob.refresh_from_db()
        zone_mob.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(plan_mob.room_id, plan_origin.id)
        self.assertEqual(zone_mob.room_id, zone_origin.id)

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

    def test_cohort_roaming_skips_whole_cohort_when_follower_is_in_active_combat(self):
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        leader, follower = self._cohort()
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=follower,
            status=CombatEncounter.STATUS_ACTIVE,
        )

        roamed = run_mob_roaming(active_combat_mob_ids={follower.id})

        leader.refresh_from_db()
        follower.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(leader.room_id, self.room.id)
        self.assertEqual(follower.room_id, self.room.id)

    def test_cohort_roaming_rechecks_door_after_selecting_exit(self):
        destination = self._room(name="East Room", x=1, y=0)
        self.room.east = destination
        self.room.save(update_fields=["east"])
        doorway = Doorway.objects.create(
            world=self.world,
            default_state=api_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=doorway,
            direction=api_consts.DIRECTION_EAST,
            from_room=self.room,
            to_room=destination,
        )
        leader, follower = self._cohort()

        def close_door_then_choose(options):
            DoorState.objects.update_or_create(
                doorway=doorway,
                world=self.spawn_world,
                defaults={"state": api_consts.DOOR_STATE_CLOSED},
            )
            return options[0]

        with patch(
            "spawns.tasks.random.choice",
            side_effect=close_door_then_choose,
        ):
            roamed = run_mob_roaming()

        leader.refresh_from_db()
        follower.refresh_from_db()
        self.assertEqual(roamed, 0)
        self.assertEqual(leader.room_id, self.room.id)
        self.assertEqual(follower.room_id, self.room.id)
