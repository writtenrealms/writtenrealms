from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.utils import timezone

from builders.models import (
    AbilityDefinition,
    ItemDefinition,
    MobDefinition,
    SpawnEntry,
    SpawnPlan,
    SpawnPlanRun,
    Trigger,
)
from config import constants as adv_consts
from core.computations import compute_stats
from core.scoped_state import (
    STATE_SCOPE_ROOM,
    STATE_SCOPE_WORLD,
    STATE_SCOPE_ZONE,
    get_state_snapshot,
    replace_initial_state_snapshot,
    replace_state_snapshot,
)
from spawns.models import (
    ActiveEffect,
    CombatEncounter,
    DuelMatch,
    DuelParticipant,
    Item,
    Mob,
)
from spawns.events import flush_game_event_outbox
from tests.base import WorldTestCase
from worlds.models import (
    InstanceAssignment,
    InstanceParticipant,
    InstanceRun,
    World,
    WorldConfig,
)
from worlds.tasks import monitor_worlds
from worlds.instances import (
    create_fresh_instance_run,
    enter_players_into_run,
    player_carried_item_ids,
    reset_instance,
    transfer_instance_participant,
)
from tests.utils import apply_basic_stat_system, capture_game_messages, dispatch_text_command


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
        self.assertEqual(participant.return_runtime_world, self.spawn_world)
        self.assertIsNone(participant.exited_at)
        self.assertIsNone(participant.exit_reason)

        assignment = InstanceAssignment.objects.get(instance=spawned_instance, player=self.player)
        self.assertEqual(assignment.transfer_from, self.room)

    def test_room_enter_event_fires_for_instance_entry_and_leave(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        room_content_type = ContentType.objects.get_for_model(
            self.instance_room.__class__,
        )
        Trigger.objects.create(
            world=self.instance_template,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=room_content_type,
            target_id=self.instance_room.id,
            event=adv_consts.TRIGGER_EVENT_ENTER,
            script="/cmd room -- /echo -- The instance threshold opens.",
            display_action_in_room=False,
            gate_delay=0,
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=room_content_type,
            target_id=self.room.id,
            event=adv_consts.TRIGGER_EVENT_ENTER,
            script="/cmd room -- /echo -- The return threshold opens.",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as entry_messages:
            self._enter()
            flush_game_event_outbox()

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.instance_room.id)
        entry_echoes = [
            entry["message"]
            for entry in entry_messages
            if entry["message"].get("type") == "cmd./echo.success"
            and "instance threshold" in entry["message"].get("text", "")
        ]
        self.assertEqual(len(entry_echoes), 1)

        location_sequence_before_reset = self.player.location_sequence
        with capture_game_messages() as reset_messages:
            reset_instance(player=self.player)
            flush_game_event_outbox()

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.instance_room.id)
        self.assertEqual(
            self.player.location_sequence,
            location_sequence_before_reset + 1,
        )
        reset_echoes = [
            entry["message"]
            for entry in reset_messages
            if entry["message"].get("type") == "cmd./echo.success"
            and "instance threshold" in entry["message"].get("text", "")
        ]
        self.assertEqual(len(reset_echoes), 1)

        with capture_game_messages() as leave_messages:
            World.leave_instance(player=self.player)
            flush_game_event_outbox()

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        leave_echoes = [
            entry["message"]
            for entry in leave_messages
            if entry["message"].get("type") == "cmd./echo.success"
            and "return threshold" in entry["message"].get("text", "")
        ]
        self.assertEqual(len(leave_echoes), 1)

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

    def test_instance_state_sync_inherits_base_duel_announcement_policy(self):
        self.world.config.announce_duel_results = True
        self.world.config.save(update_fields=["announce_duel_results"])
        self.instance_template.config.announce_duel_results = False
        self.instance_template.config.save(update_fields=["announce_duel_results"])

        self._enter()
        self.player.refresh_from_db()

        from spawns.state_payloads import build_state_sync

        payload = build_state_sync(self.player).model_dump()

        self.assertTrue(payload["world"]["announce_duel_results"])

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
        self.assertEqual(participant.return_runtime_world, self.spawn_world)
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
        self.assertEqual(
            solo_participant.exit_reason,
            InstanceParticipant.EXIT_REASON_REPLACED,
        )
        self.assertIsNone(solo_participant.return_runtime_world_id)
        self.assertIsNone(group_participant.exited_at)

    def test_fresh_run_creator_never_reuses_an_active_leader_run(self):
        first_run = create_fresh_instance_run(
            self.instance_template,
            leader=self.player,
        )
        second_run = create_fresh_instance_run(
            self.instance_template,
            leader=self.player,
        )

        self.assertNotEqual(first_run.id, second_run.id)
        self.assertNotEqual(first_run.spawned_world_id, second_run.spawned_world_id)
        self.assertEqual(
            InstanceRun.objects.filter(
                template_world=self.instance_template,
                leader=self.player,
            ).count(),
            2,
        )

    def test_completed_leader_run_is_not_reused_for_a_new_entry(self):
        first_instance = self._enter()
        first_run = first_instance.instance_run
        World.leave_instance(player=self.player)
        first_run.status = InstanceRun.STATUS_COMPLETED
        first_run.completed_at = timezone.now()
        first_run.save(update_fields=["status", "completed_at"])

        second_instance = self._enter()

        self.assertNotEqual(second_instance.id, first_instance.id)
        self.assertNotEqual(second_instance.instance_run.id, first_run.id)
        self.assertEqual(second_instance.instance_run.status, InstanceRun.STATUS_ACTIVE)

    def test_completed_run_reference_cannot_be_reentered(self):
        member = self.create_player("Member")
        first_instance = self._enter()
        first_run = first_instance.instance_run
        World.leave_instance(player=self.player)
        first_run.status = InstanceRun.STATUS_COMPLETED
        first_run.completed_at = timezone.now()
        first_run.save(update_fields=["status", "completed_at"])

        with self.assertRaises(RuntimeError):
            self._enter(player=member, ref=first_run.ref)

        member.refresh_from_db()
        self.assertEqual(member.world, self.spawn_world)
        self.assertEqual(member.room, self.room)
        self.assertFalse(
            InstanceParticipant.objects.filter(
                run=first_run,
                player=member,
            ).exists()
        )

    def test_enter_players_into_fresh_run_moves_both_atomically(self):
        member = self.create_player("Member")
        run = create_fresh_instance_run(
            self.instance_template,
            leader=self.player,
            member_ids=[member.id],
        )

        entered_run = enter_players_into_run(
            run,
            players_and_transfer_rooms=[
                (self.player, self.room),
                (member, self.room),
            ],
            entry_room=self.instance_room,
        )

        self.player.refresh_from_db()
        member.refresh_from_db()
        entered_run.refresh_from_db()
        self.assertEqual(self.player.world_id, run.spawned_world_id)
        self.assertEqual(member.world_id, run.spawned_world_id)
        self.assertEqual(self.player.room, self.instance_room)
        self.assertEqual(member.room, self.instance_room)
        self.assertEqual(
            set(
                run.participants.values_list(
                    "player_id",
                    "role",
                    "transfer_from_id",
                )
            ),
            {
                (
                    self.player.id,
                    InstanceParticipant.ROLE_LEADER,
                    self.room.id,
                ),
                (
                    member.id,
                    InstanceParticipant.ROLE_MEMBER,
                    self.room.id,
                ),
            },
        )
        self.assertEqual(
            set(
                InstanceAssignment.objects.filter(
                    instance=run.spawned_world,
                ).values_list("player_id", flat=True)
            ),
            {self.player.id, member.id},
        )
        self.assertIsNotNone(entered_run.last_active_at)
        self.assertEqual(
            entered_run.spawned_world.lifecycle,
            adv_consts.WORLD_LIFECYCLE_RUNNING,
        )

    def test_enter_players_into_run_rejects_stale_transfer_room_before_moving_anyone(self):
        member = self.create_player("Member")
        other_room = self.room.create_at(adv_consts.DIRECTION_NORTH)
        member.room = other_room
        member.save(update_fields=["room"])
        run = create_fresh_instance_run(
            self.instance_template,
            leader=self.player,
            member_ids=[member.id],
        )

        with self.assertRaisesRegex(RuntimeError, "moved away"):
            enter_players_into_run(
                run,
                players_and_transfer_rooms=[
                    (self.player, self.room),
                    (member, self.room),
                ],
                entry_room=self.instance_room,
            )

        self.player.refresh_from_db()
        member.refresh_from_db()
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertEqual(member.world, self.spawn_world)
        self.assertEqual(self.player.room, self.room)
        self.assertEqual(member.room, other_room)
        self.assertFalse(run.participants.exists())
        self.assertFalse(
            InstanceAssignment.objects.filter(instance=run.spawned_world).exists()
        )

    def test_failed_single_entry_rolls_back_participant_and_player_movement(self):
        with patch(
            "worlds.instances.move_player_carried_items_to_world",
            side_effect=RuntimeError("transfer failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "transfer failed"):
                self._enter()

        self.player.refresh_from_db()
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertEqual(self.player.room, self.room)
        self.assertFalse(
            InstanceParticipant.objects.filter(player=self.player).exists()
        )
        self.assertFalse(
            InstanceAssignment.objects.filter(player=self.player).exists()
        )

    def test_match_instance_rejects_generic_no_ref_entry(self):
        self.instance_template.config.pvp_mode = adv_consts.PVP_MODE_MATCH
        self.instance_template.config.save(update_fields=["pvp_mode"])

        with self.assertRaisesRegex(RuntimeError, "accepted duel"):
            self._enter()

        self.player.refresh_from_db()
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertEqual(InstanceRun.objects.count(), 0)

    def test_match_ref_admits_only_active_contestants(self):
        self.instance_template.config.pvp_mode = adv_consts.PVP_MODE_MATCH
        self.instance_template.config.save(update_fields=["pvp_mode"])
        member = self.create_player("Member")
        spectator = self.create_player("Spectator")
        outsider = self.create_player("Outsider")
        run = create_fresh_instance_run(
            self.instance_template,
            leader=self.player,
            member_ids=[member.id],
        )
        match = DuelMatch.objects.create(
            base_world=self.world,
            template_world=self.instance_template,
            entrance_room=self.room,
            run=run,
            challenger=self.player,
            challenged=member,
            status=DuelMatch.STATUS_ACTIVE,
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
            started_at=timezone.now(),
        )
        DuelParticipant.objects.create(
            match=match,
            player=self.player,
            role=DuelParticipant.ROLE_CONTESTANT,
            team=1,
        )
        DuelParticipant.objects.create(
            match=match,
            player=member,
            role=DuelParticipant.ROLE_CONTESTANT,
            team=2,
        )
        DuelParticipant.objects.create(
            match=match,
            player=spectator,
            role=DuelParticipant.ROLE_SPECTATOR,
            team=1,
        )

        member_instance = self._enter(player=member, ref=run.ref)

        self.assertEqual(member_instance, run.spawned_world)
        with self.assertRaisesRegex(RuntimeError, "private"):
            self._enter(player=spectator, ref=run.ref)
        with self.assertRaisesRegex(RuntimeError, "private"):
            self._enter(player=outsider, ref=run.ref)

    def test_active_match_privacy_survives_template_config_mutation(self):
        self.instance_template.config.pvp_mode = adv_consts.PVP_MODE_MATCH
        self.instance_template.config.save(update_fields=["pvp_mode"])
        member = self.create_player("Member")
        outsider = self.create_player("Outsider")
        run = create_fresh_instance_run(
            self.instance_template,
            leader=self.player,
            member_ids=[member.id],
        )
        match = DuelMatch.objects.create(
            base_world=self.world,
            template_world=self.instance_template,
            entrance_room=self.room,
            run=run,
            challenger=self.player,
            challenged=member,
            status=DuelMatch.STATUS_ACTIVE,
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
            started_at=timezone.now(),
        )
        DuelParticipant.objects.create(
            match=match,
            player=self.player,
            role=DuelParticipant.ROLE_CONTESTANT,
            team=1,
        )
        DuelParticipant.objects.create(
            match=match,
            player=member,
            role=DuelParticipant.ROLE_CONTESTANT,
            team=2,
        )
        self.instance_template.config.pvp_mode = adv_consts.PVP_MODE_DISABLED
        self.instance_template.config.save(update_fields=["pvp_mode"])

        with self.assertRaisesRegex(RuntimeError, "private"):
            self._enter(player=outsider, ref=run.ref)
        with self.assertRaisesRegex(RuntimeError, "accepted duel"):
            self._enter(player=outsider)
        self.assertEqual(InstanceRun.objects.count(), 1)

        entered = self._enter(player=member, ref=run.ref)
        self.assertEqual(entered, run.spawned_world)

    def test_match_entry_revalidates_run_after_startup_gap(self):
        self.instance_template.config.pvp_mode = adv_consts.PVP_MODE_MATCH
        self.instance_template.config.save(update_fields=["pvp_mode"])
        member = self.create_player("Member")
        run = create_fresh_instance_run(
            self.instance_template,
            leader=self.player,
            member_ids=[member.id],
        )
        match = DuelMatch.objects.create(
            base_world=self.world,
            template_world=self.instance_template,
            entrance_room=self.room,
            run=run,
            challenger=self.player,
            challenged=member,
            status=DuelMatch.STATUS_ACTIVE,
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
            started_at=timezone.now(),
        )
        DuelParticipant.objects.create(
            match=match,
            player=self.player,
            role=DuelParticipant.ROLE_CONTESTANT,
            team=1,
        )
        DuelParticipant.objects.create(
            match=match,
            player=member,
            role=DuelParticipant.ROLE_CONTESTANT,
            team=2,
        )

        def complete_before_move(_run):
            DuelMatch.objects.filter(pk=match.id).update(
                status=DuelMatch.STATUS_COMPLETED,
                completed_at=timezone.now(),
            )
            InstanceRun.objects.filter(pk=run.id).update(
                status=InstanceRun.STATUS_COMPLETED,
                completed_at=timezone.now(),
            )

        with patch(
            "worlds.instances._ensure_spawned_instance_started",
            side_effect=complete_before_move,
        ), self.assertRaisesRegex(RuntimeError, "no longer active"):
            self._enter(player=member, ref=run.ref)

        member.refresh_from_db()
        self.assertEqual(member.world_id, self.spawn_world.id)
        self.assertEqual(member.room_id, self.room.id)

    def test_instance_entry_revalidates_player_room_after_startup_gap(self):
        other_room = self.room.create_at(adv_consts.DIRECTION_NORTH)

        def move_player_before_final_lock(_run):
            self.player.__class__.objects.filter(pk=self.player.id).update(
                room=other_room,
            )

        with patch(
            "worlds.instances._ensure_spawned_instance_started",
            side_effect=move_player_before_final_lock,
        ), self.assertRaisesRegex(RuntimeError, "moved away"):
            self._enter()

        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.spawn_world.id)
        self.assertEqual(self.player.room_id, other_room.id)

    def test_instance_entry_uses_authoritative_origin_not_stale_player_object(self):
        other_room = self.room.create_at(adv_consts.DIRECTION_NORTH)
        self.player.__class__.objects.filter(pk=self.player.id).update(
            room=other_room,
        )

        with self.assertRaisesRegex(RuntimeError, "valid instance entrance"):
            self._enter()

        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, self.spawn_world.id)
        self.assertEqual(self.player.room_id, other_room.id)
        self.assertFalse(InstanceRun.objects.exists())

    def test_match_ref_rejects_contestant_after_match_completion(self):
        self.instance_template.config.pvp_mode = adv_consts.PVP_MODE_MATCH
        self.instance_template.config.save(update_fields=["pvp_mode"])
        member = self.create_player("Member")
        run = create_fresh_instance_run(
            self.instance_template,
            leader=self.player,
            member_ids=[member.id],
        )
        match = DuelMatch.objects.create(
            base_world=self.world,
            template_world=self.instance_template,
            entrance_room=self.room,
            run=run,
            challenger=self.player,
            challenged=member,
            status=DuelMatch.STATUS_COMPLETED,
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
            completed_at=timezone.now(),
        )
        DuelParticipant.objects.create(
            match=match,
            player=member,
            role=DuelParticipant.ROLE_CONTESTANT,
            team=2,
        )

        with self.assertRaisesRegex(RuntimeError, "private"):
            self._enter(player=member, ref=run.ref)

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

    def test_carried_item_traversal_is_batched_instead_of_per_container(self):
        bag_def = self._definition(
            "travel-pack",
            "travel pack",
            adv_consts.ITEM_TYPE_CONTAINER,
        )
        gem_def = self._definition(
            "travel-stone",
            "travel stone",
            adv_consts.ITEM_TYPE_INERT,
        )
        expected_ids = set()
        for index in range(20):
            bag = Item.objects.create(
                world=self.spawn_world,
                definition=bag_def,
                definition_slug_snapshot=bag_def.slug,
                container=self.player,
                name=f"travel pack {index}",
                type=adv_consts.ITEM_TYPE_CONTAINER,
            )
            stone = Item.objects.create(
                world=self.spawn_world,
                definition=gem_def,
                definition_slug_snapshot=gem_def.slug,
                container=bag,
                name=f"travel stone {index}",
                type=adv_consts.ITEM_TYPE_INERT,
            )
            expected_ids.update((bag.id, stone.id))

        with patch(
            "spawns.models.Item.get_contained_ids",
            side_effect=AssertionError("per-container traversal used"),
        ):
            self.assertEqual(player_carried_item_ids(self.player), expected_ids)

    def test_character_effect_runtime_follows_player_into_and_out_of_instance(self):
        effect = ActiveEffect.objects.create(
            world=self.spawn_world,
            target_player=self.player,
            scope=ActiveEffect.SCOPE_CHARACTER,
            effect="blessing",
            category="buff",
            label="Blessing",
            remaining_rounds=3,
            duration_rounds=3,
        )

        spawned_instance = self._enter()
        effect.refresh_from_db()
        self.assertEqual(effect.world, spawned_instance)

        World.leave_instance(player=self.player)
        effect.refresh_from_db()
        self.assertEqual(effect.world, self.spawn_world)

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
        self.assertEqual(
            participant.exit_reason,
            InstanceParticipant.EXIT_REASON_LEFT,
        )
        self.assertIsNone(participant.return_runtime_world_id)
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertEqual(self.player.room, self.room)

    def test_leave_uses_the_participants_exact_recorded_runtime(self):
        spawned_instance = self._enter()
        participant = spawned_instance.instance_run.participants.get(
            player=self.player,
        )
        other_runtime = World.objects.create(
            name="Another Island Runtime",
            config=self.world.config,
            context=self.world,
            is_multiplayer=True,
        )
        self.assertEqual(participant.return_runtime_world, self.spawn_world)

        World.leave_instance(player=self.player)

        self.player.refresh_from_db()
        participant.refresh_from_db()
        self.assertIsNone(participant.return_runtime_world)
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertNotEqual(self.player.world, other_runtime)

    def test_exited_participant_does_not_block_return_runtime_deletion(self):
        alternate_runtime = World.objects.create(
            name="Temporary Island Runtime",
            config=self.world.config,
            context=self.world,
            is_multiplayer=False,
        )
        self.player.world = alternate_runtime
        self.player.save(update_fields=["world"])

        spawned_instance = self._enter()
        participant = spawned_instance.instance_run.participants.get(
            player=self.player,
        )
        self.assertEqual(participant.return_runtime_world, alternate_runtime)
        World.leave_instance(player=self.player)

        self.player.world = self.spawn_world
        self.player.save(update_fields=["world"])
        alternate_runtime_id = alternate_runtime.id
        alternate_runtime.delete()

        participant.refresh_from_db()
        self.assertIsNone(participant.return_runtime_world_id)
        self.assertFalse(
            World.objects.filter(pk=alternate_runtime_id).exists()
        )

    def test_database_enforces_participant_exit_shape(self):
        spawned_instance = self._enter()
        participant = spawned_instance.instance_run.participants.get(
            player=self.player,
        )

        invalid_active_updates = (
            {"return_runtime_world": None},
            {"exit_reason": InstanceParticipant.EXIT_REASON_LEFT},
        )
        for updates in invalid_active_updates:
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    InstanceParticipant.objects.filter(
                        pk=participant.pk,
                    ).update(**updates)

        World.leave_instance(player=self.player)
        participant.refresh_from_db()
        invalid_exited_updates = (
            {"exit_reason": None},
            {"return_runtime_world": self.spawn_world},
        )
        for updates in invalid_exited_updates:
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    InstanceParticipant.objects.filter(
                        pk=participant.pk,
                    ).update(**updates)

    def test_reentry_records_the_new_exact_return_runtime(self):
        spawned_instance = self._enter()
        run = spawned_instance.instance_run
        World.leave_instance(player=self.player)
        alternate_runtime = World.objects.create(
            name="Alternate Island Runtime",
            config=self.world.config,
            context=self.world,
            is_multiplayer=True,
        )
        self.player.world = alternate_runtime
        self.player.room = self.room
        self.player.save(update_fields=["world", "room"])

        self._enter()

        participant = run.participants.get(player=self.player)
        self.assertEqual(participant.return_runtime_world, alternate_runtime)
        self.assertIsNone(participant.exit_reason)
        World.leave_instance(player=self.player)
        self.player.refresh_from_db()
        participant.refresh_from_db()
        self.assertEqual(self.player.world, alternate_runtime)
        self.assertIsNone(participant.return_runtime_world_id)

    def test_atomic_participant_transfer_does_not_update_the_run(self):
        spawned_instance = self._enter()
        run = spawned_instance.instance_run
        participant = run.participants.get(player=self.player)
        original_last_active_at = run.last_active_at

        updated_player = transfer_instance_participant(
            participant=participant,
            destination_room=self.world.config.death_room,
            exit_reason=InstanceParticipant.EXIT_REASON_DEATH_DELEGATED,
            expected_origin_world_id=spawned_instance.id,
        )

        run.refresh_from_db()
        participant.refresh_from_db()
        self.assertEqual(run.last_active_at, original_last_active_at)
        self.assertEqual(updated_player.world, self.spawn_world)
        self.assertEqual(updated_player.room, self.world.config.death_room)
        self.assertIsNotNone(participant.exited_at)
        self.assertEqual(
            participant.exit_reason,
            InstanceParticipant.EXIT_REASON_DEATH_DELEGATED,
        )
        self.assertIsNone(participant.return_runtime_world_id)

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

    def test_monitor_does_not_treat_offline_instance_players_as_active(self):
        spawned_instance = self._enter()
        run = spawned_instance.instance_run
        self.player.in_game = False
        self.player.save(update_fields=["in_game"])
        old = timezone.now() - timezone.timedelta(minutes=6)
        self.spawn_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.lifecycle_change_ts = timezone.now()
        self.spawn_world.last_played_ts = old
        self.spawn_world.save(update_fields=[
            "lifecycle",
            "lifecycle_change_ts",
            "last_played_ts",
        ])
        run.last_active_at = old
        run.save(update_fields=["last_active_at"])

        with patch("worlds.tasks.WorldSmith") as smith:
            monitor_worlds()

        examined_world_ids = {
            call.args[0].id
            for call in smith.call_args_list
            if call.args
        }
        self.assertIn(self.spawn_world.id, examined_world_ids)
        self.assertIn(spawned_instance.id, examined_world_ids)

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

    def test_look_at_instance_entrance_includes_enter_action(self):
        self._link_current_room_to_instance()

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "look")

        look_message = self._message_by_type(messages, "cmd.look.success")

        self.assertIsNotNone(look_message)
        self.assertIn("enter", look_message["data"]["target"]["actions"])
        self.assertIn("Action available: enter", look_message["text"])

    def test_enter_command_without_instance_link_returns_error(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "enter")

        self.player.refresh_from_db()
        message = self._message_by_type(messages, "cmd.enter.error")

        self.assertIsNotNone(message)
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertEqual(self.player.room, self.room)
        self.assertIn("no instance entrance", message["text"].lower())

    def test_enter_command_cannot_bypass_match_acceptance(self):
        self._link_current_room_to_instance()
        self.instance_template.config.pvp_mode = adv_consts.PVP_MODE_MATCH
        self.instance_template.config.save(update_fields=["pvp_mode"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "enter")

        self.player.refresh_from_db()
        message = self._message_by_type(messages, "cmd.enter.error")
        self.assertIsNotNone(message)
        self.assertEqual(self.player.world, self.spawn_world)
        self.assertEqual(self.player.room, self.room)
        self.assertEqual(InstanceRun.objects.count(), 0)

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
        replace_state_snapshot(
            STATE_SCOPE_ZONE,
            self.instance_room.zone,
            {"fog": True},
            runtime_world=spawned_instance,
        )
        replace_state_snapshot(
            STATE_SCOPE_ROOM,
            self.instance_room,
            {"door_opened": True},
            runtime_world=spawned_instance,
        )
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
        self.assertTrue(reset_message["data"]["runtime_scoped_state_reset"])
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
        self.assertEqual(
            get_state_snapshot(
                STATE_SCOPE_ZONE,
                self.instance_room.zone,
                runtime_world=spawned_instance,
            ),
            {},
        )
        self.assertEqual(
            get_state_snapshot(
                STATE_SCOPE_ROOM,
                self.instance_room,
                runtime_world=spawned_instance,
            ),
            {},
        )
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

    def test_parallel_instance_runs_isolate_and_reset_scoped_state(self):
        replace_initial_state_snapshot(
            STATE_SCOPE_WORLD,
            self.instance_template,
            {"phase": "initial"},
        )
        replace_initial_state_snapshot(
            STATE_SCOPE_ZONE,
            self.instance_room.zone,
            {"alarm": 0},
        )
        replace_initial_state_snapshot(
            STATE_SCOPE_ROOM,
            self.instance_room,
            {"gate_open": False},
        )
        second_player = self.create_player("Second Leader")

        first_runtime = self._enter(player=self.player)
        second_runtime = self._enter(player=second_player)
        self.assertNotEqual(first_runtime.pk, second_runtime.pk)

        replace_state_snapshot(
            STATE_SCOPE_WORLD,
            first_runtime,
            {"phase": "first-live"},
        )
        replace_state_snapshot(
            STATE_SCOPE_ZONE,
            self.instance_room.zone,
            {"alarm": 1},
            runtime_world=first_runtime,
        )
        replace_state_snapshot(
            STATE_SCOPE_ROOM,
            self.instance_room,
            {"gate_open": True},
            runtime_world=first_runtime,
        )
        replace_state_snapshot(
            STATE_SCOPE_WORLD,
            second_runtime,
            {"phase": "second-live"},
        )
        replace_state_snapshot(
            STATE_SCOPE_ZONE,
            self.instance_room.zone,
            {"alarm": 2},
            runtime_world=second_runtime,
        )

        reset_instance(player=self.player)

        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_WORLD, first_runtime),
            {"phase": "initial"},
        )
        self.assertEqual(
            get_state_snapshot(
                STATE_SCOPE_ZONE,
                self.instance_room.zone,
                runtime_world=first_runtime,
            ),
            {"alarm": 0},
        )
        self.assertEqual(
            get_state_snapshot(
                STATE_SCOPE_ROOM,
                self.instance_room,
                runtime_world=first_runtime,
            ),
            {"gate_open": False},
        )
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_WORLD, second_runtime),
            {"phase": "second-live"},
        )
        self.assertEqual(
            get_state_snapshot(
                STATE_SCOPE_ZONE,
                self.instance_room.zone,
                runtime_world=second_runtime,
            ),
            {"alarm": 2},
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
