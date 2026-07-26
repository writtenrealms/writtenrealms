from unittest.mock import patch

from django.utils import timezone

from config import constants as adv_consts
from core.scoped_state import STATE_SCOPE_CHARACTER, get_state_snapshot
from spawns.actions.base import ActionError
from spawns.actions.combat import KillAction
from spawns.actions.effects import (
    build_character_effect,
    refresh_or_add_character_effect,
)
from spawns.duels import (
    DUELS_FOUGHT_STATE_KEY,
    DUELS_LOST_STATE_KEY,
    DUELS_WON_STATE_KEY,
    accept_duel,
    challenge_duel,
    duel_combat_block_reason,
    duel_status_text,
    resolve_duel_defeat,
    surrender_duel,
)
from spawns.events import flush_game_event_outbox
from spawns.models import (
    ActiveEffect,
    CombatEncounter,
    DuelMatch,
    DuelParticipant,
    Mob,
)
from tests.base import WorldTestCase
from tests.utils import capture_game_messages, dispatch_text_command
from worlds.models import InstanceRun, World, WorldConfig


class TestDuelLifecycle(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=["is_multiplayer"])
        self.world.config.pvp_mode = adv_consts.PVP_MODE_DISABLED
        self.world.config.combat_resolution_interval = 2
        self.world.config.announce_duel_results = False
        self.world.config.save(update_fields=[
            "pvp_mode",
            "combat_resolution_interval",
            "announce_duel_results",
        ])

        self.arena_config = WorldConfig.objects.create(
            pvp_mode=adv_consts.PVP_MODE_MATCH,
            death_mode=adv_consts.DEATH_MODE_LOSE_NONE,
        )
        self.arena = World.objects.new_world(
            name="Dueling Arena",
            author=self.user,
            config=self.arena_config,
            is_multiplayer=True,
            instance_of=self.world,
        )
        self.arena_entry = self.arena.config.starting_room
        self.room.transfer_to = self.arena_entry
        self.room.save(update_fields=["transfer_to"])

        self.opponent = self.create_player("Alex")
        for player in (self.player, self.opponent):
            player.in_game = True
            player.health = 30
            player.health_max = 30
            player.stamina = 50
            player.stamina_max = 50
            player.save(update_fields=[
                "in_game",
                "health",
                "stamina",
            ])

    def _start_duel(self):
        challenge = challenge_duel(self.player.id, self.opponent.id)
        started = accept_duel(
            self.opponent.id,
            challenger_id=self.player.id,
        )
        match = DuelMatch.objects.select_related("run").get(pk=challenge.match_id)
        self.player.refresh_from_db()
        self.opponent.refresh_from_db()
        return match, started

    def test_challenge_records_team_aware_contestants(self):
        result = challenge_duel(self.player.id, self.opponent.id)

        match = DuelMatch.objects.get(pk=result.match_id)
        participants = list(
            match.participants.order_by("team").values_list(
                "player_id",
                "role",
                "team",
            )
        )

        self.assertEqual(match.status, DuelMatch.STATUS_PENDING)
        self.assertEqual(match.base_world, self.world)
        self.assertEqual(match.template_world, self.arena)
        self.assertEqual(match.entrance_room, self.room)
        self.assertEqual(
            participants,
            [
                (
                    self.player.id,
                    DuelParticipant.ROLE_CONTESTANT,
                    1,
                ),
                (
                    self.opponent.id,
                    DuelParticipant.ROLE_CONTESTANT,
                    2,
                ),
            ],
        )

    def test_accept_creates_fresh_private_run_and_moves_both_players(self):
        match, result = self._start_duel()

        self.assertEqual(match.status, DuelMatch.STATUS_ACTIVE)
        self.assertIsNotNone(match.run_id)
        self.assertEqual(match.run.status, InstanceRun.STATUS_ACTIVE)
        self.assertEqual(self.player.world_id, match.run.spawned_world_id)
        self.assertEqual(self.opponent.world_id, match.run.spawned_world_id)
        self.assertEqual(self.player.room_id, self.arena_entry.id)
        self.assertEqual(self.opponent.room_id, self.arena_entry.id)
        self.assertEqual(
            set(result.state_sync_player_ids),
            {self.player.id, self.opponent.id},
        )
        self.assertEqual(
            set(match.run.participants.values_list("player_id", flat=True)),
            {self.player.id, self.opponent.id},
        )

    def test_text_commands_challenge_accept_and_block_active_leave(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "duel Alex")

        challenge = DuelMatch.objects.get()
        self.assertEqual(challenge.status, DuelMatch.STATUS_PENDING)
        self.assertTrue(any(
            row["message"]["type"] == "notification.duel.challenged"
            and row["player_key"] == self.opponent.key
            for row in messages
        ))

        dispatch_text_command(self.opponent.id, "duel accept Joe")
        challenge.refresh_from_db()
        self.player.refresh_from_db()
        self.opponent.refresh_from_db()
        self.assertEqual(challenge.status, DuelMatch.STATUS_ACTIVE)
        self.assertEqual(self.player.world_id, challenge.run.spawned_world_id)
        self.assertEqual(self.opponent.world_id, challenge.run.spawned_world_id)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "leave")

        leave_error = next(
            row["message"]
            for row in messages
            if row["player_key"] == self.player.key
            and row["message"]["type"] == "cmd.leave.error"
        )
        self.assertIn("duel surrender", leave_error["text"])
        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, challenge.run.spawned_world_id)

    def test_only_opposing_contestant_combat_is_allowed_in_active_arena(self):
        with self.assertRaises(ActionError):
            KillAction().execute(self.player.id, "Alex")

        match, _result = self._start_duel()
        Mob.objects.create(
            world=match.run.spawned_world,
            room=self.arena_entry,
            name="Arena Rat",
            keywords="rat",
        )

        with self.assertRaises(ActionError) as raised:
            KillAction().execute(self.player.id, "rat")

        self.assertEqual(raised.exception.code, "duel_combat_disabled")
        self.assertIn("opposing contestant", raised.exception.message)

    def test_base_world_mob_target_is_not_shadowed_by_same_named_player(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Alex",
            keywords="alex",
            health=20,
            health_max=20,
            fights_back=False,
        )

        with patch(
            "spawns.actions.combat._schedule_encounter_resolution",
        ):
            KillAction().execute(self.player.id, "Alex")

        encounter = CombatEncounter.objects.get(
            player=self.player,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(encounter.mob_id, mob.id)
        self.opponent.refresh_from_db()
        self.assertEqual(self.opponent.health, 30)

    def test_accept_rejects_players_with_active_combat(self):
        challenge = challenge_duel(self.player.id, self.opponent.id)
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Gate Rat",
            keywords="rat",
        )
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            resolution_interval=-1,
        )

        with self.assertRaises(ActionError) as raised:
            accept_duel(self.opponent.id, challenger_id=self.player.id)

        self.assertEqual(raised.exception.code, "duel_combat_active")
        match = DuelMatch.objects.get(pk=challenge.match_id)
        self.assertEqual(match.status, DuelMatch.STATUS_PENDING)
        self.assertIsNone(match.run_id)

    def test_accept_rejects_players_with_live_hostile_character_effect(self):
        challenge = challenge_duel(self.player.id, self.opponent.id)
        third_player = self.create_player(
            "Hazard",
            user=self.create_user("hazard@example.com"),
        )
        ActiveEffect.objects.create(
            world=self.spawn_world,
            source_player=third_player,
            target_player=self.opponent,
            scope=ActiveEffect.SCOPE_CHARACTER,
            effect="poison",
            category="debuff",
            label="Poison",
            remaining_rounds=2,
            duration_rounds=2,
            tick={"every_rounds": 1},
            is_hostile=True,
            next_tick_ts=timezone.now() + timezone.timedelta(seconds=30),
        )

        with self.assertRaises(ActionError) as raised:
            accept_duel(self.opponent.id, challenger_id=self.player.id)

        self.assertEqual(
            raised.exception.code,
            "duel_hostile_effect_active",
        )
        match = DuelMatch.objects.get(pk=challenge.match_id)
        self.assertEqual(match.status, DuelMatch.STATUS_PENDING)
        self.assertIsNone(match.run_id)

    def test_negative_resource_tick_is_classified_as_hostile_on_accept(self):
        challenge = challenge_duel(self.player.id, self.opponent.id)
        third_player = self.create_player(
            "Hexer",
            user=self.create_user("hexer@example.com"),
        )
        effect = build_character_effect(
            component={
                "type": "effect",
                "effect": "life-drain",
                "category": "debuff",
                "scope": "character",
                "duration": {"rounds": 2},
                "tick": {
                    "every_rounds": 1,
                    "primitives": [
                        {
                            "type": "resource_change",
                            "resource": "health",
                            "amount": -5,
                        }
                    ],
                },
            },
            source=third_player,
            target=self.opponent,
        )
        refresh_or_add_character_effect(
            self.opponent,
            effect,
            source=third_player,
        )
        active_effect = ActiveEffect.objects.get(
            target_player=self.opponent,
            effect="life-drain",
        )
        self.assertTrue(active_effect.is_hostile)

        with self.assertRaises(ActionError) as raised:
            accept_duel(self.opponent.id, challenger_id=self.player.id)

        self.assertEqual(
            raised.exception.code,
            "duel_hostile_effect_active",
        )
        match = DuelMatch.objects.get(pk=challenge.match_id)
        self.assertEqual(match.status, DuelMatch.STATUS_PENDING)
        self.assertIsNone(match.run_id)

    def test_nondamaging_debuff_is_classified_as_hostile_on_accept(self):
        challenge = challenge_duel(self.player.id, self.opponent.id)
        third_player = self.create_player(
            "Jailer",
            user=self.create_user("jailer@example.com"),
        )
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
            source=third_player,
            target=self.opponent,
        )
        refresh_or_add_character_effect(
            self.opponent,
            effect,
            source=third_player,
        )
        active_effect = ActiveEffect.objects.get(
            target_player=self.opponent,
            effect="root",
        )
        self.assertTrue(active_effect.is_hostile)

        with self.assertRaises(ActionError) as raised:
            accept_duel(self.opponent.id, challenger_id=self.player.id)

        self.assertEqual(
            raised.exception.code,
            "duel_hostile_effect_active",
        )
        match = DuelMatch.objects.get(pk=challenge.match_id)
        self.assertEqual(match.status, DuelMatch.STATUS_PENDING)
        self.assertIsNone(match.run_id)

    def test_accept_rejects_hostile_effect_sourced_by_contestant(self):
        challenge = challenge_duel(self.player.id, self.opponent.id)
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Poisoned Rat",
            keywords="rat",
        )
        effect = build_character_effect(
            component={
                "type": "effect",
                "effect": "bleed",
                "category": "debuff",
                "scope": "character",
                "duration": {"rounds": 2},
                "tick": {
                    "every_rounds": 1,
                    "component": {
                        "type": "damage",
                        "profile": "basic_physical",
                    },
                },
            },
            source=self.player,
            target=mob,
        )
        refresh_or_add_character_effect(
            mob,
            effect,
            source=self.player,
        )

        with self.assertRaises(ActionError) as raised:
            accept_duel(self.opponent.id, challenger_id=self.player.id)

        self.assertEqual(
            raised.exception.code,
            "duel_hostile_effect_active",
        )
        match = DuelMatch.objects.get(pk=challenge.match_id)
        self.assertEqual(match.status, DuelMatch.STATUS_PENDING)
        self.assertIsNone(match.run_id)

    def test_accept_rejects_mob_sourced_hostile_character_effect(self):
        challenge = challenge_duel(self.player.id, self.opponent.id)
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Venom Trap",
            keywords="trap",
        )
        ActiveEffect.objects.create(
            world=self.spawn_world,
            source_mob=mob,
            target_player=self.opponent,
            scope=ActiveEffect.SCOPE_CHARACTER,
            effect="venom",
            category="debuff",
            label="Venom",
            remaining_rounds=2,
            duration_rounds=2,
            tick={"every_rounds": 1},
            is_hostile=True,
            next_tick_ts=timezone.now() + timezone.timedelta(seconds=30),
        )

        with self.assertRaises(ActionError) as raised:
            accept_duel(self.opponent.id, challenger_id=self.player.id)

        self.assertEqual(
            raised.exception.code,
            "duel_hostile_effect_active",
        )
        match = DuelMatch.objects.get(pk=challenge.match_id)
        self.assertEqual(match.status, DuelMatch.STATUS_PENDING)
        self.assertIsNone(match.run_id)

    def test_defeat_is_idempotent_and_updates_three_character_state_counters(self):
        match, _result = self._start_duel()
        self.opponent.health = 0
        self.opponent.save(update_fields=["health"])

        resolve_duel_defeat(match, self.player, self.opponent)
        resolve_duel_defeat(match, self.player, self.opponent)

        match.refresh_from_db()
        match.run.refresh_from_db()
        self.player.refresh_from_db()
        self.opponent.refresh_from_db()
        winner_state = get_state_snapshot(STATE_SCOPE_CHARACTER, self.player)
        loser_state = get_state_snapshot(STATE_SCOPE_CHARACTER, self.opponent)

        self.assertEqual(match.status, DuelMatch.STATUS_COMPLETED)
        self.assertEqual(match.winner_id, self.player.id)
        self.assertEqual(match.loser_id, self.opponent.id)
        self.assertEqual(match.run.status, InstanceRun.STATUS_COMPLETED)
        self.assertEqual(winner_state[DUELS_FOUGHT_STATE_KEY], 1)
        self.assertEqual(winner_state[DUELS_WON_STATE_KEY], 1)
        self.assertEqual(winner_state[DUELS_LOST_STATE_KEY], 0)
        self.assertEqual(loser_state[DUELS_FOUGHT_STATE_KEY], 1)
        self.assertEqual(loser_state[DUELS_LOST_STATE_KEY], 1)
        self.assertEqual(loser_state[DUELS_WON_STATE_KEY], 0)
        self.assertGreater(self.opponent.health, 0)
        self.assertIn("duel is over", duel_combat_block_reason(self.player).lower())
        self.assertIn(
            "Record: 1 fought, 1 won, 0 lost.",
            duel_status_text(self.player),
        )
        self.assertIn("You won this duel.", duel_status_text(self.player))
        self.assertIn(
            "Record: 1 fought, 0 won, 1 lost.",
            duel_status_text(self.opponent),
        )

        participant_results = dict(
            match.participants.values_list("player_id", "result")
        )
        self.assertEqual(
            participant_results,
            {
                self.player.id: DuelParticipant.RESULT_WON,
                self.opponent.id: DuelParticipant.RESULT_LOST,
            },
        )

    def test_result_announcement_is_opt_in_on_base_world(self):
        watcher = self.create_player("Watcher")
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])
        match, _result = self._start_duel()

        with capture_game_messages() as messages:
            resolve_duel_defeat(match, self.player, self.opponent)
            flush_game_event_outbox()

        result_messages = [
            row for row in messages
            if row["message"]["type"] == "notification.duel.completed"
        ]
        announcement_messages = [
            row for row in messages
            if row["message"]["type"] == "notification.duel.announcement"
        ]
        self.assertEqual(len(result_messages), 2)
        self.assertEqual(announcement_messages, [])
        for result_message in result_messages:
            actor = result_message["message"]["data"]["actor"]
            target = result_message["message"]["data"]["target"]
            self.assertEqual(actor["key"], result_message["player_key"])
            self.assertGreater(actor["health"], 0)
            self.assertNotEqual(target["key"], actor["key"])
            self.assertGreater(target["health"], 0)
            self.assertIn("inventory", actor)
            self.assertNotIn("inventory", target)

        World.leave_instance(player=self.player)
        World.leave_instance(player=self.opponent)
        self.player.refresh_from_db()
        self.opponent.refresh_from_db()
        second_challenge = challenge_duel(self.player.id, self.opponent.id)
        accept_duel(self.opponent.id, challenger_id=self.player.id)
        second_match = DuelMatch.objects.get(pk=second_challenge.match_id)
        self.world.config.announce_duel_results = True
        self.world.config.save(update_fields=["announce_duel_results"])

        with capture_game_messages() as messages:
            resolve_duel_defeat(second_match, self.opponent, self.player)
            flush_game_event_outbox()

        watcher_announcements = [
            row["message"]
            for row in messages
            if row["player_key"] == watcher.key
            and row["message"]["type"] == "notification.duel.announcement"
        ]
        self.assertEqual(len(watcher_announcements), 1)
        self.assertEqual(
            watcher_announcements[0]["text"],
            "Alex has defeated Joe in a duel.",
        )
        self.assertNotIn("records", watcher_announcements[0]["data"])

    def test_completed_duel_requires_leave_and_new_match_for_rematch(self):
        first_match, _result = self._start_duel()
        first_spawned_world_id = first_match.run.spawned_world_id
        resolve_duel_defeat(first_match, self.player, self.opponent)

        World.leave_instance(player=self.player)
        World.leave_instance(player=self.opponent)
        self.player.refresh_from_db()
        self.opponent.refresh_from_db()
        challenge = challenge_duel(self.player.id, self.opponent.id)
        accept_duel(self.opponent.id, challenger_id=self.player.id)
        second_match = DuelMatch.objects.select_related("run").get(
            pk=challenge.match_id,
        )

        self.assertNotEqual(
            second_match.run.spawned_world_id,
            first_spawned_world_id,
        )
        self.assertEqual(second_match.status, DuelMatch.STATUS_ACTIVE)

    def test_surrender_race_does_not_tell_the_actual_winner_they_lost(self):
        match, _result = self._start_duel()

        def opponent_finishes_first(*_args, **_kwargs):
            DuelMatch.objects.filter(pk=match.id).update(
                status=DuelMatch.STATUS_COMPLETED,
                winner=self.player,
                loser=self.opponent,
                completed_at=timezone.now(),
            )

        with patch(
            "spawns.duels.resolve_duel_defeat",
            side_effect=opponent_finishes_first,
        ), self.assertRaises(ActionError) as raised:
            surrender_duel(self.player.id)

        self.assertEqual(raised.exception.code, "duel_complete")
        self.assertIn("you won", raised.exception.message.lower())

    def test_offline_instance_cleanup_abandons_duel_without_a_result(self):
        match, _result = self._start_duel()
        run = match.run
        spawned_world = run.spawned_world
        for player in (self.player, self.opponent):
            player.in_game = False
            player.save(update_fields=["in_game"])

        spawned_world.lifecycle = adv_consts.WORLD_LIFECYCLE_STOPPED
        spawned_world.save(update_fields=["lifecycle"])
        spawned_world.cleanup()

        match.refresh_from_db()
        run.refresh_from_db()
        self.player.refresh_from_db()
        self.opponent.refresh_from_db()
        self.assertEqual(match.status, DuelMatch.STATUS_CANCELLED)
        self.assertIsNone(match.winner_id)
        self.assertIsNone(match.loser_id)
        self.assertEqual(match.outcome["resolution"], "abandoned")
        self.assertEqual(run.status, InstanceRun.STATUS_ABANDONED)
        self.assertEqual(self.player.world_id, self.spawn_world.id)
        self.assertEqual(self.opponent.world_id, self.spawn_world.id)
        self.assertGreater(self.player.health, 0)
        self.assertGreater(self.opponent.health, 0)
        for player in (self.player, self.opponent):
            state = get_state_snapshot(STATE_SCOPE_CHARACTER, player)
            self.assertEqual(state.get(DUELS_FOUGHT_STATE_KEY, 0), 0)
            self.assertEqual(state.get(DUELS_WON_STATE_KEY, 0), 0)
            self.assertEqual(state.get(DUELS_LOST_STATE_KEY, 0), 0)
