from unittest.mock import patch

from django.utils import timezone

from config import constants as adv_consts
from builders.models import AbilityDefinition
from core.combat_formulas import normalize_combat_system
from core.computations import compute_stats
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    get_state_value,
)
from spawns.actions.abilities import AbilityAction, UnlearnAbilityAction
from spawns.actions.base import ActionError
from spawns.actions.combat import (
    FleeAction,
    KillAction,
    resolve_combat_encounter_step,
    resolve_due_character_effects,
)
from spawns.actions.player_state import RestAction
from spawns.actions.pvp import reconcile_stale_pvp_encounters
from spawns.actions.effects import (
    build_character_effect,
    refresh_or_add_character_effect,
)
from spawns.duels import (
    DUELS_FOUGHT_STATE_KEY,
    DUELS_LOST_STATE_KEY,
    DUELS_WON_STATE_KEY,
)
from spawns.models import (
    ActiveEffect,
    CombatEncounter,
    CombatParticipant,
    DoorState,
    DuelMatch,
    DuelParticipant,
    GameEventOutbox,
    Mob,
)
from tests.base import WorldTestCase
from tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    dispatch_text_command,
)
from spawns.events import flush_game_event_outbox
from spawns.handlers import dispatch_command
from worlds.instances import create_fresh_instance_run
from worlds.models import Door, Doorway, InstanceRun, World, WorldConfig


class DuelCombatTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.world.config.combat_resolution_interval = -1
        self.world.config.pvp_mode = adv_consts.PVP_MODE_DISABLED
        self.world.config.combat_system = normalize_combat_system({
            "variance": {"enabled": False, "percent": 0},
            "profiles": {
                "basic_physical": {
                    "power_scale": 1,
                    "use_weapon_damage": False,
                    "can_dodge": False,
                    "can_crit": False,
                    "mitigation": {
                        "armor": False,
                        "resilience": False,
                    },
                    "minimum": 1,
                },
            },
        })
        self.world.config.save(update_fields=[
            "combat_resolution_interval",
            "pvp_mode",
            "combat_system",
        ])

        arena_config = WorldConfig.objects.create(
            pvp_mode=adv_consts.PVP_MODE_MATCH,
        )
        self.arena = World.objects.new_world(
            name="Duel Arena",
            author=self.user,
            config=arena_config,
            is_multiplayer=True,
            instance_of=self.world,
        )
        self.arena_room = self.arena.config.starting_room
        self.escape_room = self.arena_room.create_at("east")
        self.opponent = self.create_player(
            "Rival",
            user=self.create_user("rival@example.com"),
        )
        self.run = create_fresh_instance_run(
            self.arena,
            leader=self.player,
            member_ids=[self.opponent.id],
        )
        self.run.spawned_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        self.run.spawned_world.save(update_fields=["lifecycle"])
        self.match = DuelMatch.objects.create(
            base_world=self.world,
            template_world=self.arena,
            entrance_room=self.room,
            run=self.run,
            challenger=self.player,
            challenged=self.opponent,
            status=DuelMatch.STATUS_ACTIVE,
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
            started_at=timezone.now(),
        )
        DuelParticipant.objects.create(
            match=self.match,
            player=self.player,
            role=DuelParticipant.ROLE_CONTESTANT,
            team=1,
        )
        DuelParticipant.objects.create(
            match=self.match,
            player=self.opponent,
            role=DuelParticipant.ROLE_CONTESTANT,
            team=2,
        )

        for actor in (self.player, self.opponent):
            stats = compute_stats(
                actor.level,
                actor.archetype,
                char=actor,
                world=self.run.spawned_world,
            )
            actor.world = self.run.spawned_world
            actor.room = self.arena_room
            actor.health = max(100, int(stats["health_max"]))
            actor.energy = int(stats["energy_max"])
            actor.stamina = max(100, int(stats["stamina_max"]))
            actor.in_game = True
            actor.save(update_fields=[
                "world",
                "room",
                "health",
                "energy",
                "stamina",
                "in_game",
            ])

    def _scripted_room_kill(self, target):
        dispatch_command(
            command_type="text",
            actor_type="room",
            actor_id=self.arena_room.id,
            payload={
                "text": f"/kill {target.key}",
                "runtime_world_id": self.run.spawned_world_id,
            },
            script_source=True,
        )

    def _grant_ability(
        self,
        *,
        slug,
        name,
        target,
        components,
        cooldown,
    ):
        ability = AbilityDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            command_verbs=[slug],
            consumes_primary_action_on_resolve=True,
            target=target,
            availability={"classes": [], "min_level": 1},
            requirements={},
            cost={},
            cast_time={},
            cooldown=cooldown,
            components=components,
        )
        self.player.known_abilities = [
            *self.player.known_abilities,
            ability.slug,
        ]
        self.player.save(update_fields=["known_abilities"])
        return ability

    def _assert_transfer_finishes_pvp_encounter(self, target):
        KillAction().execute(self.player.id, "Rival")
        encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        participants = {
            participant.player_id: participant
            for participant in CombatParticipant.objects.filter(
                encounter=encounter,
            )
        }
        target_participant = participants[target.id]
        other_participant = next(
            participant
            for player_id, participant in participants.items()
            if player_id != target.id
        )

        target.refresh_from_db()
        reserved_flee_cost = 7
        stamina_before_reservation = target.stamina
        target.stamina -= reserved_flee_cost
        target.save(update_fields=["stamina"])
        target_participant.pending_flee = {
            "status": "preparing",
            "movement_cost": reserved_flee_cost,
        }
        target_participant.save(update_fields=["pending_flee"])
        other_participant.pending_ability = {
            "ability": "test-preparation",
            "status": "casting",
        }
        other_participant.save(update_fields=["pending_ability"])

        destination_ref = (
            f"room@{self.escape_room.x},"
            f"{self.escape_room.y},"
            f"{self.escape_room.z}"
        )
        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                actor_type="room",
                actor_id=self.arena_room.id,
                payload={
                    "text": f"/transfer {target.key} {destination_ref}",
                    "runtime_world_id": self.run.spawned_world_id,
                },
                script_source=True,
            )

        encounter.refresh_from_db()
        target.refresh_from_db()
        self.match.refresh_from_db()
        self.run.refresh_from_db()
        refreshed_participants = list(
            CombatParticipant.objects.filter(encounter=encounter)
        )

        self.assertEqual(target.room_id, self.escape_room.id)
        self.assertEqual(target.stamina, stamina_before_reservation)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertTrue(refreshed_participants)
        self.assertTrue(
            all(not participant.is_active for participant in refreshed_participants)
        )
        self.assertTrue(
            all(
                participant.pending_ability == {}
                and participant.pending_flee == {}
                for participant in refreshed_participants
            )
        )
        self.assertEqual(self.match.status, DuelMatch.STATUS_ACTIVE)
        self.assertEqual(self.run.status, InstanceRun.STATUS_ACTIVE)
        transfer_event = next(
            message["message"]
            for message in messages
            if message["message"]["type"] == "cmd./transfer.success"
        )
        self.assertIn(
            encounter.id,
            transfer_event["data"]["finished_encounter_ids"],
        )
        preparation_recipients = {
            message["player_key"]
            for message in messages
            if message["message"]["type"]
            == "player.ability_preparations.update"
        }
        self.assertEqual(
            preparation_recipients,
            {self.player.key, self.opponent.key},
        )

    def test_transfer_finishes_pvp_for_legacy_encounter_owner(self):
        self._assert_transfer_finishes_pvp_encounter(self.player)

    def test_transfer_finishes_pvp_for_other_participant(self):
        self._assert_transfer_finishes_pvp_encounter(self.opponent)

    def test_flee_finishes_only_spatial_encounter_and_allows_reengagement(self):
        KillAction().execute(self.player.id, "Rival")

        encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(
            CombatParticipant.objects.filter(
                encounter=encounter,
                is_active=True,
            ).count(),
            2,
        )

        FleeAction().execute(self.player.id)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, DuelMatch.STATUS_ACTIVE)

        FleeAction().execute(self.player.id)
        self.player.refresh_from_db()
        encounter.refresh_from_db()
        self.match.refresh_from_db()

        self.assertEqual(self.player.room_id, self.escape_room.id)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(self.match.status, DuelMatch.STATUS_ACTIVE)
        self.assertEqual(
            get_state_value(
                STATE_SCOPE_CHARACTER,
                self.player,
                DUELS_FOUGHT_STATE_KEY,
                0,
            ),
            0,
        )

        self.opponent.room = self.escape_room
        self.opponent.save(update_fields=["room"])
        KillAction().execute(self.player.id, "Rival")

        self.assertEqual(
            CombatEncounter.objects.filter(
                duel_match=self.match,
                status=CombatEncounter.STATUS_ACTIVE,
            ).count(),
            1,
        )

    def test_flee_rechecks_door_under_lock_before_room_change(self):
        from spawns.actions.doors import (
            lock_door_state_for_movement as real_door_lock,
        )

        doorway = Doorway.objects.create(
            world=self.arena,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=doorway,
            direction=adv_consts.DIRECTION_EAST,
            from_room=self.arena_room,
            to_room=self.escape_room,
        )
        starting_stamina = self.player.stamina
        KillAction().execute(self.player.id, "Rival")
        encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        FleeAction().execute(self.player.id)

        def close_door_then_lock(**kwargs):
            DoorState.objects.update_or_create(
                doorway=doorway,
                world=self.run.spawned_world,
                defaults={"state": adv_consts.DOOR_STATE_CLOSED},
            )
            return real_door_lock(**kwargs)

        with patch(
            "spawns.actions.combat.lock_door_state_for_movement",
            side_effect=close_door_then_lock,
        ) as lock_mock:
            result = FleeAction().execute(self.player.id)

        self.player.refresh_from_db()
        encounter.refresh_from_db()
        participant = CombatParticipant.objects.get(
            encounter=encounter,
            player=self.player,
        )
        error = next(event for event in result.events if event.type == "cmd.flee.error")
        self.assertEqual(error.data["code"], "closed_door")
        self.assertEqual(self.player.room_id, self.arena_room.id)
        self.assertEqual(self.player.stamina, starting_stamina)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_ACTIVE)
        self.assertEqual(participant.pending_flee, {})
        lock_mock.assert_called_once()

    def test_simultaneous_flee_refunds_nonmoving_players_reservation(self):
        self.world.config.combat_resolution_interval = 1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        starting_stamina = {
            self.player.id: self.player.stamina,
            self.opponent.id: self.opponent.stamina,
        }

        with patch(
            "spawns.actions.combat._schedule_encounter_resolution",
        ):
            KillAction().execute(self.player.id, "Rival")

        encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        FleeAction().execute(self.player.id)
        FleeAction().execute(self.opponent.id)
        first_step = resolve_combat_encounter_step(
            encounter.id,
            auto_advance=False,
        )
        second_step = resolve_combat_encounter_step(
            encounter.id,
            auto_advance=False,
        )

        self.player.refresh_from_db()
        self.opponent.refresh_from_db()
        encounter.refresh_from_db()
        moved = next(
            actor
            for actor in (self.player, self.opponent)
            if actor.room_id == self.escape_room.id
        )
        stayed = next(
            actor
            for actor in (self.player, self.opponent)
            if actor.room_id == self.arena_room.id
        )
        self.assertTrue(first_step.encounter_active)
        self.assertFalse(second_step.encounter_active)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertLess(moved.stamina, starting_stamina[moved.id])
        self.assertEqual(stayed.stamina, starting_stamina[stayed.id])
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, DuelMatch.STATUS_ACTIVE)

    def test_scripted_room_kill_resolves_duel_before_combat_starts(self):
        self._scripted_room_kill(self.player)

        self.match.refresh_from_db()
        self.run.refresh_from_db()
        self.player.refresh_from_db()
        self.opponent.refresh_from_db()

        self.assertEqual(self.match.status, DuelMatch.STATUS_COMPLETED)
        self.assertEqual(self.match.winner_id, self.opponent.id)
        self.assertEqual(self.match.loser_id, self.player.id)
        self.assertEqual(
            self.match.outcome.get("resolution"),
            "scripted_defeat",
        )
        self.assertEqual(self.run.status, InstanceRun.STATUS_COMPLETED)
        self.assertEqual(self.player.room_id, self.arena_room.id)
        self.assertEqual(self.opponent.room_id, self.arena_room.id)
        self.assertEqual(
            get_state_value(
                STATE_SCOPE_CHARACTER,
                self.player,
                DUELS_FOUGHT_STATE_KEY,
                0,
            ),
            1,
        )
        self.assertEqual(
            get_state_value(
                STATE_SCOPE_CHARACTER,
                self.player,
                DUELS_LOST_STATE_KEY,
                0,
            ),
            1,
        )
        self.assertEqual(
            get_state_value(
                STATE_SCOPE_CHARACTER,
                self.opponent,
                DUELS_WON_STATE_KEY,
                0,
            ),
            1,
        )

    def test_scripted_room_kill_finishes_active_duel_encounter(self):
        KillAction().execute(self.player.id, "Rival")
        encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )

        self._scripted_room_kill(self.opponent)

        self.match.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(self.match.status, DuelMatch.STATUS_COMPLETED)
        self.assertEqual(self.match.winner_id, self.player.id)
        self.assertEqual(self.match.loser_id, self.opponent.id)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertFalse(
            CombatParticipant.objects.filter(
                encounter=encounter,
                is_active=True,
            ).exists()
        )

    def test_round_zero_move_closes_encounter_and_reengages_in_one_command(self):
        self.world.config.combat_resolution_interval = 1
        self.world.config.save(update_fields=["combat_resolution_interval"])

        with patch(
            "spawns.actions.combat._schedule_encounter_resolution",
        ):
            KillAction().execute(self.player.id, "Rival")
        first_encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(first_encounter.round_number, 0)

        with self.captureOnCommitCallbacks(execute=True):
            dispatch_text_command(self.player.id, "east")

        first_encounter.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(
            first_encounter.status,
            CombatEncounter.STATUS_FINISHED,
        )
        self.assertEqual(self.player.room_id, self.escape_room.id)

        with self.captureOnCommitCallbacks(execute=True):
            dispatch_text_command(self.opponent.id, "east")
        self.opponent.refresh_from_db()
        self.assertEqual(self.opponent.room_id, self.escape_room.id)

        with patch(
            "spawns.actions.combat._schedule_encounter_resolution",
        ):
            KillAction().execute(self.player.id, "Rival")

        second_encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertNotEqual(second_encounter.id, first_encounter.id)
        self.assertEqual(second_encounter.room_id, self.escape_room.id)

    def test_reengage_reconciles_move_when_on_commit_cleanup_was_lost(self):
        self.world.config.combat_resolution_interval = 1
        self.world.config.save(update_fields=["combat_resolution_interval"])

        with patch("spawns.actions.combat._schedule_encounter_resolution"):
            KillAction().execute(self.player.id, "Rival")
        first_encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )

        with self.captureOnCommitCallbacks(execute=False):
            dispatch_text_command(self.player.id, "east")
        with self.captureOnCommitCallbacks(execute=False):
            dispatch_text_command(self.opponent.id, "east")

        first_encounter.refresh_from_db()
        self.player.refresh_from_db()
        self.opponent.refresh_from_db()
        self.assertEqual(
            first_encounter.status,
            CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(self.player.room_id, self.escape_room.id)
        self.assertEqual(self.opponent.room_id, self.escape_room.id)

        with patch("spawns.actions.combat._schedule_encounter_resolution"):
            KillAction().execute(self.player.id, "Rival")

        first_encounter.refresh_from_db()
        replacement = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(
            first_encounter.status,
            CombatEncounter.STATUS_FINISHED,
        )
        self.assertNotEqual(replacement.id, first_encounter.id)
        self.assertEqual(replacement.room_id, self.escape_room.id)

    def test_heartbeat_reconciles_stale_spatial_encounter(self):
        self.world.config.combat_resolution_interval = 1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        with patch("spawns.actions.combat._schedule_encounter_resolution"):
            KillAction().execute(self.player.id, "Rival")
        encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )

        with self.captureOnCommitCallbacks(execute=False):
            dispatch_text_command(self.player.id, "east")
        events = reconcile_stale_pvp_encounters()

        encounter.refresh_from_db()
        self.match.refresh_from_db()
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(self.match.status, DuelMatch.STATUS_ACTIVE)
        self.assertTrue(events)

    def test_lethal_command_uses_only_ordered_outbox_delivery(self):
        self.opponent.health = 1
        self.opponent.save(update_fields=["health"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill Rival")
            self.assertEqual(messages, [])
            outbox_rows = list(GameEventOutbox.objects.order_by("sequence"))
            outbox_types = [row.event_type for row in outbox_rows]
            self.assertIn("notification.combat.attack", outbox_types)
            self.assertIn("notification.duel.completed", outbox_types)
            expected_attack_deliveries = sorted(
                (
                    recipient,
                    str(row.event_id),
                )
                for row in outbox_rows
                if row.event_type == "notification.combat.attack"
                for recipient in row.recipients
            )
            flush_game_event_outbox()

        attack_messages = [
            row
            for row in messages
            if row["message"]["type"] == "notification.combat.attack"
        ]
        actual_attack_deliveries = sorted(
            (
                row["player_key"],
                row["message"]["data"]["_event_id"],
            )
            for row in attack_messages
        )
        self.assertEqual(
            actual_attack_deliveries,
            expected_attack_deliveries,
        )
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_detached_lethal_effect_orders_tick_before_duel_completion(self):
        self.opponent.health = 1
        self.opponent.save(update_fields=["health"])
        ActiveEffect.objects.create(
            world=self.run.spawned_world,
            source_player=self.player,
            target_player=self.opponent,
            scope=ActiveEffect.SCOPE_CHARACTER,
            effect="duel-burn",
            category="debuff",
            label="Duel Burn",
            remaining_rounds=1,
            duration_rounds=1,
            tick={
                "every_rounds": 1,
                "component": {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 10},
                    "text": {"label": "Duel Burn"},
                },
            },
            is_hostile=True,
            next_tick_ts=timezone.now() - timezone.timedelta(seconds=1),
        )

        returned_events = resolve_due_character_effects(
            persist_events=True,
        )

        self.assertEqual(returned_events, [])
        rows = list(GameEventOutbox.objects.order_by("sequence"))
        self.assertTrue(rows)
        self.assertEqual(len({row.batch_id for row in rows}), 1)
        effect_sequences = [
            row.sequence
            for row in rows
            if row.event_type == "notification.combat.effect"
        ]
        completion_sequences = [
            row.sequence
            for row in rows
            if row.event_type == "notification.duel.completed"
        ]
        self.assertTrue(effect_sequences)
        self.assertTrue(completion_sequences)
        self.assertLess(max(effect_sequences), min(completion_sequences))
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, DuelMatch.STATUS_COMPLETED)

    def test_external_hostile_effects_cannot_resolve_or_break_duel(self):
        third_player = self.create_player(
            "Outsider",
            user=self.create_user("outsider@example.com"),
        )
        trap = Mob.objects.create(
            world=self.run.spawned_world,
            room=self.arena_room,
            name="Trap",
            keywords="trap",
        )
        self.opponent.health = 1
        self.opponent.save(update_fields=["health"])
        due_at = timezone.now() - timezone.timedelta(seconds=1)
        effect_fields = {
            "world": self.run.spawned_world,
            "target_player": self.opponent,
            "scope": ActiveEffect.SCOPE_CHARACTER,
            "category": "debuff",
            "remaining_rounds": 1,
            "duration_rounds": 1,
            "tick": {
                "every_rounds": 1,
                "component": {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 100},
                },
            },
            "is_hostile": True,
            "next_tick_ts": due_at,
        }
        ActiveEffect.objects.create(
            **effect_fields,
            source_player=third_player,
            effect="outsider-burn",
            label="Outsider Burn",
        )
        ActiveEffect.objects.create(
            **effect_fields,
            source_mob=trap,
            effect="trap-burn",
            label="Trap Burn",
        )

        self.assertEqual(
            resolve_due_character_effects(due_at=timezone.now()),
            [],
        )

        self.opponent.refresh_from_db()
        self.match.refresh_from_db()
        self.assertEqual(self.opponent.health, 1)
        self.assertEqual(self.match.status, DuelMatch.STATUS_ACTIVE)
        self.assertFalse(
            ActiveEffect.objects.filter(
                target_player=self.opponent,
                is_hostile=True,
            ).exists()
        )

    def test_defeat_resolves_once_updates_counters_and_disables_combat(self):
        self.opponent.health = 1
        self.opponent.save(update_fields=["health"])

        KillAction().execute(self.player.id, "Rival")

        self.match.refresh_from_db()
        self.run.refresh_from_db()
        encounter = CombatEncounter.objects.get(duel_match=self.match)
        self.assertEqual(self.match.status, DuelMatch.STATUS_COMPLETED)
        self.assertEqual(self.match.winner_id, self.player.id)
        self.assertEqual(self.match.loser_id, self.opponent.id)
        self.assertEqual(self.run.status, InstanceRun.STATUS_COMPLETED)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(
            get_state_value(
                STATE_SCOPE_CHARACTER,
                self.player,
                DUELS_FOUGHT_STATE_KEY,
                0,
            ),
            1,
        )
        self.assertEqual(
            get_state_value(
                STATE_SCOPE_CHARACTER,
                self.player,
                DUELS_WON_STATE_KEY,
                0,
            ),
            1,
        )
        self.assertEqual(
            get_state_value(
                STATE_SCOPE_CHARACTER,
                self.opponent,
                DUELS_FOUGHT_STATE_KEY,
                0,
            ),
            1,
        )
        self.assertEqual(
            get_state_value(
                STATE_SCOPE_CHARACTER,
                self.opponent,
                DUELS_LOST_STATE_KEY,
                0,
            ),
            1,
        )

        resolve_combat_encounter_step(encounter.id, auto_advance=False)
        self.assertEqual(
            get_state_value(
                STATE_SCOPE_CHARACTER,
                self.player,
                DUELS_FOUGHT_STATE_KEY,
                0,
            ),
            1,
        )
        with self.assertRaises(ActionError) as error:
            KillAction().execute(self.player.id, "Rival")
        self.assertIn(
            error.exception.code,
            {"duel_complete", "duel_combat_disabled"},
        )

    def test_hostile_player_ability_uses_participant_owned_queue(self):
        ability = AbilityDefinition.objects.create(
            world=self.world,
            slug="dueling-strike",
            name="Dueling Strike",
            command_verbs=["dueling-strike"],
            consumes_primary_action_on_resolve=True,
            target={
                "type": "hostile",
                "default": "explicit",
                "allow_out_of_combat": True,
            },
            availability={"classes": [], "min_level": 1},
            requirements={},
            cost={},
            cast_time={},
            cooldown={"rounds": 2},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "text": {"label": "Dueling Strike"},
                }
            ],
        )
        self.player.known_abilities = [ability.slug]
        self.player.save(update_fields=["known_abilities"])

        dispatch_text_command(
            self.player.id,
            f"{ability.slug} Rival",
        )

        self.player.refresh_from_db()
        self.opponent.refresh_from_db()
        encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        participant = CombatParticipant.objects.get(
            encounter=encounter,
            player=self.player,
        )
        self.assertLess(self.opponent.health, 100)
        self.assertEqual(participant.pending_ability, {})
        self.assertEqual(
            self.player.ability_cooldowns.get(ability.slug),
            2,
        )

    def test_charge_moves_to_adjacent_opponent_and_opens_duel_combat(self):
        ability = self._grant_ability(
            slug="charge",
            name="Charge",
            target={
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": True,
                "range": "current_or_adjacent_room",
                "move_actor": True,
                "opener_priority": True,
            },
            cooldown={"rounds": 10},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 1.5},
                    "text": {"label": "Charge"},
                }
            ],
        )
        self.opponent.room = self.escape_room
        self.opponent.save(update_fields=["room"])
        location_sequence_before = self.player.location_sequence

        dispatch_text_command(
            self.player.id,
            f"{ability.slug} Rival east",
        )

        self.player.refresh_from_db()
        self.opponent.refresh_from_db()
        encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(self.player.room_id, self.escape_room.id)
        self.assertEqual(
            self.player.location_sequence,
            location_sequence_before + 1,
        )
        self.assertEqual(encounter.room_id, self.escape_room.id)
        self.assertEqual(encounter.round_number, 1)
        self.assertEqual(encounter.opening_priority[0]["source"], "charge")
        self.assertLess(self.opponent.health, 100)
        self.assertEqual(
            self.player.ability_cooldowns.get(ability.slug),
            10,
        )

    def test_room_opener_cannot_bypass_out_of_combat_requirement(self):
        ability = self._grant_ability(
            slug="guarded-charge",
            name="Guarded Charge",
            target={
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": False,
                "range": "current_or_adjacent_room",
                "move_actor": True,
                "opener_priority": True,
            },
            cooldown={"rounds": 10},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                }
            ],
        )
        self.opponent.room = self.escape_room
        self.opponent.save(update_fields=["room"])

        with self.assertRaises(ActionError) as raised:
            AbilityAction().execute(
                self.player.id,
                ability=ability,
                command=ability.slug,
                args=["Rival", "east"],
            )

        self.assertEqual(raised.exception.code, "combat_required")
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.arena_room.id)
        self.assertFalse(
            CombatEncounter.objects.filter(
                duel_match=self.match,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )

    def test_room_opener_rejects_broad_pvp_effect_selector(self):
        ability = self._grant_ability(
            slug="reckless-charge",
            name="Reckless Charge",
            target={
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": True,
                "range": "current_or_adjacent_room",
                "move_actor": True,
                "opener_priority": True,
            },
            cooldown={"rounds": 10},
            components=[
                {
                    "type": "effect",
                    "effect": "shockwave",
                    "category": "debuff",
                    "target": "room.hostiles",
                    "duration": {"rounds": 1},
                }
            ],
        )
        self.opponent.room = self.escape_room
        self.opponent.save(update_fields=["room"])

        with self.assertRaises(ActionError) as raised:
            AbilityAction().execute(
                self.player.id,
                ability=ability,
                command=ability.slug,
                args=["Rival", "east"],
            )

        self.assertEqual(
            raised.exception.code,
            "duel_ability_unsupported",
        )
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.arena_room.id)

    def test_shout_before_engagement_buffs_only_its_caster(self):
        ability = self._grant_ability(
            slug="shout",
            name="Shout",
            target={
                "type": "self",
                "default": "self",
                "allow_out_of_combat": True,
            },
            cooldown={"rounds": 30},
            components=[
                {
                    "type": "effect",
                    "effect": "shout",
                    "category": "buff",
                    "target": "room.allies",
                    "stack_key": "shout-damage-output",
                    "stacking": "refresh",
                    "duration": {"rounds": 4},
                    "primitives": [
                        {
                            "type": "combat_modifier",
                            "phase": "outgoing_damage",
                            "multiplier": 1.2,
                        }
                    ],
                    "text": {"label": "Shout"},
                }
            ],
        )

        dispatch_text_command(self.player.id, ability.slug)

        self.player.refresh_from_db()
        self.assertTrue(
            ActiveEffect.objects.filter(
                world=self.run.spawned_world,
                target_player=self.player,
                effect="shout",
                remaining_rounds__gt=0,
            ).exists()
        )
        self.assertFalse(
            ActiveEffect.objects.filter(
                world=self.run.spawned_world,
                target_player=self.opponent,
                effect="shout",
                remaining_rounds__gt=0,
            ).exists()
        )
        self.assertEqual(
            self.player.ability_cooldowns.get(ability.slug),
            30,
        )
        self.assertFalse(
            CombatEncounter.objects.filter(
                duel_match=self.match,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )

    def test_self_ability_rejects_spatially_stale_opponent_context(self):
        ability = self._grant_ability(
            slug="battle-cry",
            name="Battle Cry",
            target={
                "type": "self",
                "default": "self",
                "allow_out_of_combat": True,
            },
            cooldown={"rounds": 3},
            components=[],
        )
        self.world.config.combat_resolution_interval = 1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        with patch("spawns.actions.combat._schedule_encounter_resolution"):
            KillAction().execute(self.player.id, "Rival")
        encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.opponent.room = self.escape_room
        self.opponent.save(update_fields=["room"])

        with self.assertRaises(ActionError) as raised:
            AbilityAction().execute(
                self.player.id,
                ability=ability,
                command=ability.slug,
                args=[],
            )

        self.assertEqual(raised.exception.code, "duel_inactive")
        participant = CombatParticipant.objects.get(
            encounter=encounter,
            player=self.player,
        )
        self.assertEqual(participant.pending_ability, {})

    def test_completion_clears_nondamaging_opponent_debuff(self):
        effect = build_character_effect(
            component={
                "type": "effect",
                "effect": "root",
                "category": "debuff",
                "scope": "character",
                "duration": {"rounds": 4},
                "primitives": [
                    {
                        "type": "action_rule",
                        "phase": "before_action",
                        "rule": "prevent",
                        "actions": ["flee"],
                        "reason": "rooted",
                    }
                ],
            },
            source=self.player,
            target=self.opponent,
        )
        refresh_or_add_character_effect(
            self.opponent,
            effect,
            source=self.player,
        )
        active_effect = ActiveEffect.objects.get(
            world=self.run.spawned_world,
            target_player=self.opponent,
            effect="root",
        )
        self.assertTrue(active_effect.is_hostile)
        self.opponent.health = 1
        self.opponent.save(update_fields=["health"])

        KillAction().execute(self.player.id, "Rival")

        self.assertFalse(
            ActiveEffect.objects.filter(pk=active_effect.pk).exists()
        )

    def test_both_duel_participants_are_blocked_from_resting_in_combat(self):
        self.world.config.combat_resolution_interval = 1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        with patch("spawns.actions.combat._schedule_encounter_resolution"):
            KillAction().execute(self.player.id, "Rival")

        for actor in (self.player, self.opponent):
            with self.assertRaises(ActionError) as raised:
                RestAction().execute(actor.id)
            self.assertEqual(raised.exception.code, "in_combat")

    def test_unlearn_is_rejected_during_duel_without_lock_inversion(self):
        ability = self._grant_ability(
            slug="battle-focus",
            name="Battle Focus",
            target={
                "type": "self",
                "default": "self",
                "allow_out_of_combat": True,
            },
            cooldown={"rounds": 2},
            components=[],
        )
        self.world.config.combat_resolution_interval = 1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        with patch("spawns.actions.combat._schedule_encounter_resolution"):
            KillAction().execute(self.player.id, "Rival")

        with self.assertRaises(ActionError) as raised:
            UnlearnAbilityAction().execute(
                self.player.id,
                ability.slug,
            )

        self.assertEqual(raised.exception.code, "combat_in_progress")
        self.player.refresh_from_db()
        self.assertIn(ability.slug, self.player.known_abilities)

    def test_active_match_survives_template_pvp_config_mutation(self):
        self.arena.config.pvp_mode = adv_consts.PVP_MODE_DISABLED
        self.arena.config.save(update_fields=["pvp_mode"])
        self.opponent.health = 1
        self.opponent.save(update_fields=["health"])

        KillAction().execute(self.player.id, "Rival")

        self.match.refresh_from_db()
        self.assertEqual(self.match.status, DuelMatch.STATUS_COMPLETED)
        self.assertEqual(self.match.winner_id, self.player.id)

        Mob.objects.create(
            world=self.run.spawned_world,
            room=self.arena_room,
            name="Arena Rat",
            keywords="rat",
        )
        with self.assertRaises(ActionError) as raised:
            KillAction().execute(self.player.id, "rat")
        self.assertEqual(
            raised.exception.code,
            "duel_combat_disabled",
        )

    def test_positive_cadence_schedules_and_auto_advances_shared_encounter(self):
        self.world.config.combat_resolution_interval = 1
        self.world.config.save(update_fields=["combat_resolution_interval"])

        with patch(
            "spawns.actions.combat._schedule_encounter_resolution"
        ) as schedule:
            KillAction().execute(self.player.id, "Rival")

        encounter = CombatEncounter.objects.get(
            duel_match=self.match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(encounter.round_number, 0)
        schedule.assert_called_once_with(encounter.id, 1.0)

        encounter.next_resolution_ts = timezone.now() - timezone.timedelta(
            seconds=1
        )
        encounter.save(update_fields=["next_resolution_ts"])
        with patch(
            "spawns.actions.combat._schedule_encounter_resolution"
        ) as reschedule:
            result = resolve_combat_encounter_step(
                encounter.id,
                auto_advance=True,
            )

        encounter.refresh_from_db()
        self.assertTrue(result.encounter_active)
        self.assertEqual(encounter.round_number, 1)
        reschedule.assert_called_once_with(encounter.id, 1.0)
