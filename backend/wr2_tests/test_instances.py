from unittest.mock import patch

from django.utils import timezone

from builders.models import (
    AbilityDefinition,
    ItemDefinition,
    MobDefinition,
    SpawnEntry,
    SpawnPlan,
    SpawnPlanRun,
)
from config import constants as adv_consts
from core.computations import compute_stats
from core.scoped_state import (
    STATE_SCOPE_ROOM,
    STATE_SCOPE_WORLD,
    STATE_SCOPE_ZONE,
    get_state_snapshot,
    replace_state_snapshot,
)
from spawns.models import CombatEncounter, Item, Mob
from tests.base import WorldTestCase
from worlds.models import (
    InstanceAssignment,
    InstanceParticipant,
    InstanceRun,
    World,
    WorldConfig,
)
from worlds.tasks import monitor_worlds
from wr2_tests.utils import apply_basic_stat_system, capture_game_messages, dispatch_text_command


class TestInstanceRuntimeFoundation(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=["is_multiplayer"])

        self.instance_config = WorldConfig.objects.create()
        self.instance_template = World.objects.new_world(
            name="Sunken Hold",
            author=self.user,
            config=self.instance_config,
            is_multiplayer=True,
            instance_of=self.world,
        )
        self.instance_room = self.instance_template.config.starting_room

    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _link_current_room_to_instance(self):
        self.room.transfer_to = self.instance_room
        self.room.save(update_fields=["transfer_to"])

    def _enter(self, player=None, *, ref=None, member_ids=None):
        return World.enter_instance(
            player=player or self.player,
            transfer_to_id=self.instance_room.id,
            transfer_from_id=self.room.id,
            ref=ref,
            member_ids=member_ids,
        )

    def _definition(self, slug, name, item_type):
        return ItemDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            item_type=item_type,
        )

    def _ability(self, *, slug="battle-focus", name="Battle Focus", verbs=None):
        return AbilityDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            command_verbs=verbs or ["focus"],
            action_type="utility",
            target={
                "type": "self",
                "default": "self",
                "allow_out_of_combat": True,
            },
            availability={"classes": [], "min_level": 1},
            requirements={},
            cost={},
            cast_time={},
            cooldown={"rounds": 0},
            components=[],
        )

    def test_enter_instance_creates_run_and_leader_participant(self):
        member = self.create_player("Member")

        spawned_instance = self._enter(member_ids=[member.id])

        self.player.refresh_from_db()
        run = InstanceRun.objects.get()
        participant = InstanceParticipant.objects.get(run=run, player=self.player)

        self.assertEqual(spawned_instance, run.spawned_world)
        self.assertEqual(run.base_world, self.world)
        self.assertEqual(run.template_world, self.instance_template)
        self.assertEqual(run.leader, self.player)
        self.assertEqual(run.status, InstanceRun.STATUS_ACTIVE)
        self.assertEqual(run.initial_member_ids, [member.id])
        self.assertEqual(run.ref, spawned_instance.instance_ref)
        self.assertEqual(self.player.world, spawned_instance)
        self.assertEqual(self.player.room, self.instance_room)
        self.assertEqual(participant.role, InstanceParticipant.ROLE_LEADER)
        self.assertEqual(participant.transfer_from, self.room)
        self.assertIsNone(participant.exited_at)

        assignment = InstanceAssignment.objects.get(instance=spawned_instance, player=self.player)
        self.assertEqual(assignment.transfer_from, self.room)

    def test_enter_instance_starts_spawn_world_and_runs_spawn_plans(self):
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="instance-guard",
            name="an instance guard",
            mob_type=adv_consts.MOB_TYPE_CONSTRUCT,
            base_properties={"health_max": 10},
        )
        plan = SpawnPlan.objects.create(
            world=self.instance_template,
            zone=self.instance_room.zone,
            slug="instance-population",
            name="Instance Population",
            respawn_policy={"mode": "none"},
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="guard",
            source="mobdefinition.instance-guard",
            target={"room": f"room@{self.instance_room.x},{self.instance_room.y},{self.instance_room.z}"},
            count=1,
        )

        spawned_instance = self._enter()

        spawned_instance.refresh_from_db()
        self.assertEqual(spawned_instance.lifecycle, adv_consts.WORLD_LIFECYCLE_RUNNING)
        self.assertIsNotNone(spawned_instance.last_spawn_plan_run_ts)
        mob = Mob.objects.get(world=spawned_instance, definition=mob_definition)
        self.assertEqual(mob.room, self.instance_room)

    def test_instance_state_sync_inherits_base_world_ability_definitions(self):
        self._ability()
        self.player.known_abilities = ["battle-focus"]
        self.player.ability_hotkeys = {"1": "battle-focus"}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys"])

        self._enter()
        self.player.refresh_from_db()

        from spawns.state_payloads import build_state_sync

        payload = build_state_sync(self.player).model_dump()
        definitions = payload["world"]["abilities"]["definitions"]

        self.assertEqual(payload["actor"]["known_abilities"], ["battle-focus"])
        self.assertEqual(payload["actor"]["ability_hotkeys"], {"1": "battle-focus"})
        self.assertIn("battle-focus", definitions)
        self.assertEqual(definitions["battle-focus"]["name"], "Battle Focus")

    def test_instance_ability_resolvers_inherit_base_world_definitions(self):
        ability = self._ability()
        self.player.known_abilities = ["battle-focus"]
        self.player.ability_hotkeys = {"1": "battle-focus"}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys"])

        self._enter()
        self.player.refresh_from_db()

        from spawns.actions.abilities import resolve_ability_for_command, resolve_ability_for_hotkey
        from spawns.actions.combat import _ability_definition_for_player

        self.assertEqual(resolve_ability_for_command(self.player.world, "focus"), ability)
        self.assertEqual(resolve_ability_for_hotkey(self.player, "1"), ability)
        self.assertEqual(_ability_definition_for_player(self.player, "battle-focus"), ability)

    def test_instance_state_sync_uses_base_world_stats_with_empty_template_config(self):
        apply_basic_stat_system(self.world)
        self.player.attributes = {
            "brawn": 15,
            "grit": 30,
            "focus": 5,
        }
        expected_stats = compute_stats(
            self.player.level,
            self.player.archetype,
            char=self.player,
            world=self.world,
        )
        current_health = max(expected_stats["health_max"] - 4, 1)
        self.player.health = current_health
        self.player.save(update_fields=["attributes", "health"])

        spawned_instance = self._enter()
        self.player.refresh_from_db()

        from spawns.state_payloads import build_state_sync

        payload = build_state_sync(self.player).model_dump()
        actor = payload["actor"]

        self.assertEqual(self.player.world, spawned_instance)
        self.assertEqual(actor["health"], current_health)
        self.assertEqual(actor["health_max"], expected_stats["health_max"])
        self.assertEqual(actor["attack_power"], expected_stats["attack_power"])
        self.assertEqual(actor["attributes"]["brawn"], expected_stats["brawn"])
        self.assertEqual(payload["world"]["labels"]["order"]["attributes"], ["brawn", "grit", "focus"])

    def test_instance_combat_pacing_uses_base_world_config(self):
        apply_basic_stat_system(self.world)
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self.instance_template.config.combat_resolution_interval = 0
        self.instance_template.config.save(update_fields=["combat_resolution_interval"])
        spawned_instance = self._enter()
        self.player.refresh_from_db()
        mob = Mob.objects.create(
            world=spawned_instance,
            room=self.instance_room,
            name="Instance Rat",
            keywords="rat",
            health=50,
            health_max=50,
            attack_power=4,
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, "kill rat")

        encounter = CombatEncounter.objects.get(player=self.player, mob=mob)
        mob.refresh_from_db()
        self.assertEqual(encounter.resolution_interval, 1.5)
        self.assertEqual(mob.health, 50)
        self.assertIsNotNone(self._message_by_type(messages, "cmd.kill.success"))
        self.assertIsNone(self._message_by_type(messages, "notification.combat.attack"))
        schedule_mock.assert_called_once()

    def test_group_member_joins_existing_run_by_reference(self):
        member = self.create_player("Member")
        leader_instance = self._enter(member_ids=[member.id])
        run = leader_instance.instance_run

        member_instance = self._enter(player=member, ref=run.ref)

        member.refresh_from_db()
        self.assertEqual(member_instance, leader_instance)
        self.assertEqual(InstanceRun.objects.count(), 1)
        self.assertEqual(run.participants.count(), 2)
        self.assertEqual(member.world, leader_instance)
        self.assertEqual(member.room, self.instance_room)

        participant = InstanceParticipant.objects.get(run=run, player=member)
        self.assertEqual(participant.role, InstanceParticipant.ROLE_MEMBER)
        self.assertEqual(participant.transfer_from, self.room)
        self.assertIsNone(participant.exited_at)

    def test_member_solo_run_is_exited_when_joining_group_run(self):
        member = self.create_player("Member")
        leader_instance = self._enter()
        solo_instance = self._enter(player=member)
        solo_run = solo_instance.instance_run

        group_instance = self._enter(player=member, ref=leader_instance.instance_run.ref)

        self.assertEqual(group_instance, leader_instance)
        solo_participant = InstanceParticipant.objects.get(run=solo_run, player=member)
        group_participant = InstanceParticipant.objects.get(
            run=leader_instance.instance_run,
            player=member,
        )
        self.assertIsNotNone(solo_participant.exited_at)
        self.assertIsNone(group_participant.exited_at)

    def test_enter_and_leave_move_nested_inventory_and_equipment_recursively(self):
        bag_def = self._definition("canvas-pack", "canvas pack", adv_consts.ITEM_TYPE_CONTAINER)
        gem_def = self._definition("blue-gem", "blue gem", adv_consts.ITEM_TYPE_INERT)
        sword_def = self._definition("iron-sword", "iron sword", adv_consts.ITEM_TYPE_EQUIPPABLE)

        bag = Item.objects.create(
            world=self.spawn_world,
            definition=bag_def,
            definition_slug_snapshot=bag_def.slug,
            container=self.player,
            name="canvas pack",
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        pouch = Item.objects.create(
            world=self.spawn_world,
            definition=bag_def,
            definition_slug_snapshot=bag_def.slug,
            container=bag,
            name="small pouch",
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        gem = Item.objects.create(
            world=self.spawn_world,
            definition=gem_def,
            definition_slug_snapshot=gem_def.slug,
            container=pouch,
            name="blue gem",
            type=adv_consts.ITEM_TYPE_INERT,
        )
        sword = Item.objects.create(
            world=self.spawn_world,
            definition=sword_def,
            definition_slug_snapshot=sword_def.slug,
            container=self.player.equipment,
            name="iron sword",
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
        )
        self.player.equipment.weapon = sword
        self.player.equipment.save(update_fields=["weapon"])

        spawned_instance = self._enter()
        for item in (bag, pouch, gem, sword):
            item.refresh_from_db()
            self.assertEqual(item.world, spawned_instance)
            self.assertIsNotNone(item.definition_id)

        World.leave_instance(player=self.player)
        self.player.refresh_from_db()
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertEqual(self.player.room, self.room)

        for item in (bag, pouch, gem, sword):
            item.refresh_from_db()
            self.assertEqual(item.world, self.spawn_world)
            self.assertIsNotNone(item.definition_id)

    def test_leave_instance_marks_participant_exited_without_deleting_run(self):
        spawned_instance = self._enter()
        run = spawned_instance.instance_run

        World.leave_instance(player=self.player)

        self.player.refresh_from_db()
        run.refresh_from_db()
        participant = run.participants.get(player=self.player)

        self.assertEqual(InstanceRun.objects.count(), 1)
        self.assertEqual(run.spawned_world, spawned_instance)
        self.assertEqual(run.status, InstanceRun.STATUS_ACTIVE)
        self.assertIsNotNone(run.last_active_at)
        self.assertIsNotNone(participant.exited_at)
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertEqual(self.player.room, self.room)

    def test_monitor_keeps_recently_vacated_instance_running(self):
        spawned_instance = self._enter()
        World.leave_instance(player=self.player)
        run = spawned_instance.instance_run
        run.last_active_at = timezone.now() - timezone.timedelta(minutes=4)
        run.save(update_fields=["last_active_at"])

        with patch("worlds.tasks.WorldSmith.stop") as mock_stop:
            monitor_worlds()

        mock_stop.assert_not_called()

    def test_monitor_stops_vacated_instance_after_idle_grace(self):
        spawned_instance = self._enter()
        World.leave_instance(player=self.player)
        run = spawned_instance.instance_run
        run.last_active_at = timezone.now() - timezone.timedelta(minutes=6)
        run.save(update_fields=["last_active_at"])

        with patch("worlds.tasks.WorldSmith.stop") as mock_stop:
            monitor_worlds()

        mock_stop.assert_called_once()

    def test_enter_command_uses_current_room_instance_link(self):
        self._link_current_room_to_instance()

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "enter")

        self.player.refresh_from_db()
        run = InstanceRun.objects.get()
        state_message = self._message_by_type(messages, "cmd.state.sync.success")

        self.assertIsNotNone(state_message)
        self.assertEqual(self.player.world, run.spawned_world)
        self.assertEqual(self.player.room, self.instance_room)
        self.assertEqual(state_message["data"]["world"]["instance_of_id"], self.world.id)
        self.assertEqual(state_message["data"]["world"]["instance_ref"], run.ref)
        self.assertEqual(state_message["data"]["room"]["id"], self.instance_room.id)

    def test_enter_command_without_instance_link_returns_error(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "enter")

        self.player.refresh_from_db()
        message = self._message_by_type(messages, "cmd.enter.error")

        self.assertIsNotNone(message)
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertEqual(self.player.room, self.room)
        self.assertIn("no instance entrance", message["text"].lower())

    def test_enter_command_with_ref_joins_existing_run(self):
        self._link_current_room_to_instance()
        member = self.create_player("Member")

        leader_instance = self._enter()
        ref = leader_instance.instance_run.ref

        with capture_game_messages() as messages:
            dispatch_text_command(member.id, "enter %s" % ref)

        member.refresh_from_db()
        state_message = self._message_by_type(messages, "cmd.state.sync.success")
        participant = InstanceParticipant.objects.get(
            run=leader_instance.instance_run,
            player=member,
        )

        self.assertIsNotNone(state_message)
        self.assertEqual(member.world, leader_instance)
        self.assertEqual(member.room, self.instance_room)
        self.assertEqual(participant.role, InstanceParticipant.ROLE_MEMBER)
        self.assertEqual(InstanceRun.objects.count(), 1)

    def test_leave_command_returns_to_instance_entrance(self):
        self._link_current_room_to_instance()
        spawned_instance = self._enter()

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "leave")

        self.player.refresh_from_db()
        state_message = self._message_by_type(messages, "cmd.state.sync.success")
        participant = spawned_instance.instance_run.participants.get(player=self.player)

        self.assertIsNotNone(state_message)
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertEqual(self.player.room, self.room)
        self.assertIsNotNone(participant.exited_at)
        self.assertIsNone(state_message["data"]["world"]["instance_of_id"])

    def test_leave_command_outside_instance_returns_error(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "leave")

        message = self._message_by_type(messages, "cmd.leave.error")

        self.assertIsNotNone(message)
        self.assertIn("not in an instance", message["text"].lower())

    def test_reset_command_requires_builder_character(self):
        spawned_instance = self._enter()
        mob = Mob.objects.create(
            world=spawned_instance,
            room=self.instance_room,
            name="Instance Rat",
            keywords="rat",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/reset")

        message = self._message_by_type(messages, "cmd./reset.error")

        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["error"], "Builder permissions required.")
        self.assertTrue(Mob.objects.filter(pk=mob.pk).exists())

    def test_reset_command_rebuilds_current_instance(self):
        self.player.is_builder = True
        self.player.save(update_fields=["is_builder"])
        side_room = self.instance_room.create_at(adv_consts.DIRECTION_NORTH)
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="reset-guard",
            name="a reset guard",
            mob_type=adv_consts.MOB_TYPE_CONSTRUCT,
            base_properties={"health_max": 10},
        )
        plan = SpawnPlan.objects.create(
            world=self.instance_template,
            zone=self.instance_room.zone,
            slug="reset-population",
            name="Reset Population",
            respawn_policy={"mode": "none"},
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="guard",
            source="mobdefinition.reset-guard",
            target={
                "room": (
                    f"room@{self.instance_room.x},"
                    f"{self.instance_room.y},"
                    f"{self.instance_room.z}"
                )
            },
            count=1,
        )
        spawned_instance = self._enter()
        initial_guard = Mob.objects.get(
            world=spawned_instance,
            definition=mob_definition,
        )
        extra_mob = Mob.objects.create(
            world=spawned_instance,
            room=self.instance_room,
            name="Extra Rat",
            keywords="rat",
        )
        ground_item = Item.objects.create(
            world=spawned_instance,
            container=self.instance_room,
            name="Ground Rock",
        )
        bag = Item.objects.create(
            world=spawned_instance,
            container=self.player,
            name="Canvas Pack",
            type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        nested_item = Item.objects.create(
            world=spawned_instance,
            container=bag,
            name="Blue Gem",
        )
        CombatEncounter.objects.create(
            world=spawned_instance,
            room=self.instance_room,
            player=self.player,
            mob=initial_guard,
        )
        replace_state_snapshot(STATE_SCOPE_WORLD, spawned_instance, {"lever_pulled": True})
        replace_state_snapshot(STATE_SCOPE_ZONE, self.instance_room.zone, {"fog": True})
        replace_state_snapshot(STATE_SCOPE_ROOM, self.instance_room, {"door_opened": True})
        self.player.room = side_room
        self.player.save(update_fields=["room"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/reset")

        self.player.refresh_from_db()
        reset_message = self._message_by_type(messages, "cmd./reset.success")
        state_message = self._message_by_type(messages, "cmd.state.sync.success")
        replacement_guard = Mob.objects.get(world=spawned_instance, definition=mob_definition)

        self.assertIsNotNone(reset_message)
        self.assertEqual(reset_message["data"]["reset_scope"], "instance")
        self.assertTrue(reset_message["data"]["template_scoped_state_reset"])
        self.assertIsNotNone(state_message)
        self.assertEqual(self.player.world, spawned_instance)
        self.assertEqual(self.player.room, self.instance_room)
        self.assertEqual(state_message["data"]["room"]["id"], self.instance_room.id)
        self.assertFalse(Mob.objects.filter(pk=initial_guard.pk).exists())
        self.assertFalse(Mob.objects.filter(pk=extra_mob.pk).exists())
        self.assertNotEqual(replacement_guard.pk, initial_guard.pk)
        self.assertFalse(Item.objects.filter(pk=ground_item.pk).exists())
        self.assertTrue(Item.objects.filter(pk=bag.pk).exists())
        self.assertTrue(Item.objects.filter(pk=nested_item.pk).exists())
        self.assertFalse(CombatEncounter.objects.filter(world=spawned_instance).exists())
        self.assertEqual(get_state_snapshot(STATE_SCOPE_WORLD, spawned_instance), {})
        self.assertEqual(get_state_snapshot(STATE_SCOPE_ZONE, self.instance_room.zone), {})
        self.assertEqual(get_state_snapshot(STATE_SCOPE_ROOM, self.instance_room), {})
        self.assertEqual(
            SpawnPlanRun.objects.filter(
                spawn_world=spawned_instance,
                status=SpawnPlanRun.STATUS_RESET,
            ).count(),
            1,
        )
        self.assertEqual(
            SpawnPlanRun.objects.filter(
                spawn_world=spawned_instance,
                status=SpawnPlanRun.STATUS_ACTIVE,
            ).count(),
            1,
        )

    def test_instance_command_reports_entrance_or_active_run(self):
        self._link_current_room_to_instance()

        with capture_game_messages() as entrance_messages:
            dispatch_text_command(self.player.id, "instance")

        entrance_message = self._message_by_type(entrance_messages, "cmd.instance.success")
        self.assertEqual(entrance_message["data"]["status"], "entrance")
        self.assertIn("Use `enter`", entrance_message["text"])

        spawned_instance = self._enter()

        with capture_game_messages() as active_messages:
            dispatch_text_command(self.player.id, "instance")

        active_message = self._message_by_type(active_messages, "cmd.instance.success")
        self.assertEqual(active_message["data"]["status"], "inside")
        self.assertEqual(active_message["data"]["instance_ref"], spawned_instance.instance_run.ref)
        self.assertIn("Use `leave`", active_message["text"])
