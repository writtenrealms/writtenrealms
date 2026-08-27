from django.db import connection
from django.test.utils import CaptureQueriesContext

from builders.models import AbilityDefinition, MobDefinition, SpawnEntry, SpawnPlan
from core.condition_dsl import ConditionContext, resolve_path
from core.scoped_state import (
    CHARACTER_STATE_MAX_ENCODED_BYTES,
    STATE_SCOPE_CHARACTER,
    STATE_SCOPE_ROOM,
    STATE_SCOPE_WORLD,
    STATE_SCOPE_ZONE,
    clear_state_value,
    get_state_snapshot,
    initialize_character_state,
    replace_initial_state_snapshot,
    reset_runtime_state,
    set_state_value,
)
from spawns.models import CharacterState, Mob, MobState
from spawns.loading import run_spawn_plans_for_world
from spawns.actions.abilities import execute_state_component
from tests.base import WorldTestCase
from worlds.models import (
    RoomState,
    WorldState,
    ZoneDoorResetSchedule,
    ZoneState,
)


class TestScopedRuntimeState(WorldTestCase):
    def _author_defaults(self):
        replace_initial_state_snapshot(
            STATE_SCOPE_WORLD,
            self.world,
            {"weather": "clear"},
        )
        replace_initial_state_snapshot(
            STATE_SCOPE_ZONE,
            self.zone,
            {"alert": 1},
        )
        replace_initial_state_snapshot(
            STATE_SCOPE_ROOM,
            self.room,
            {"gate_open": False},
        )

    def test_authored_defaults_seed_each_runtime_without_live_leakage(self):
        self._author_defaults()
        first = self.world.create_spawn_world()
        second = self.world.create_spawn_world()

        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_WORLD, first),
            {"weather": "clear"},
        )
        self.assertEqual(
            get_state_snapshot(
                STATE_SCOPE_ZONE,
                self.zone,
                runtime_world=first,
            ),
            {"alert": 1},
        )
        self.assertEqual(
            get_state_snapshot(
                STATE_SCOPE_ROOM,
                self.room,
                runtime_world=second,
            ),
            {"gate_open": False},
        )

        set_state_value(
            STATE_SCOPE_WORLD,
            first,
            "weather",
            "storm",
        )
        set_state_value(
            STATE_SCOPE_ZONE,
            self.zone,
            "alert",
            4,
            runtime_world=first,
        )
        set_state_value(
            STATE_SCOPE_ROOM,
            self.room,
            "gate_open",
            True,
            runtime_world=first,
        )

        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_WORLD, second)["weather"],
            "clear",
        )
        self.assertEqual(
            get_state_snapshot(
                STATE_SCOPE_ZONE,
                self.zone,
                runtime_world=second,
            )["alert"],
            1,
        )
        self.assertFalse(
            get_state_snapshot(
                STATE_SCOPE_ROOM,
                self.room,
                runtime_world=second,
            )["gate_open"]
        )

        replace_initial_state_snapshot(
            STATE_SCOPE_WORLD,
            self.world,
            {"weather": "snow"},
        )
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_WORLD, second)["weather"],
            "clear",
        )
        third = self.world.create_spawn_world()
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_WORLD, third)["weather"],
            "snow",
        )

    def test_reset_reseeds_only_the_selected_runtime(self):
        self._author_defaults()
        first = self.world.create_spawn_world()
        second = self.world.create_spawn_world()
        set_state_value(
            STATE_SCOPE_ROOM,
            self.room,
            "gate_open",
            True,
            runtime_world=first,
        )
        set_state_value(
            STATE_SCOPE_ROOM,
            self.room,
            "gate_open",
            True,
            runtime_world=second,
        )

        reset_runtime_state(first)

        self.assertFalse(
            get_state_snapshot(
                STATE_SCOPE_ROOM,
                self.room,
                runtime_world=first,
            )["gate_open"]
        )
        self.assertTrue(
            get_state_snapshot(
                STATE_SCOPE_ROOM,
                self.room,
                runtime_world=second,
            )["gate_open"]
        )

    def test_character_scope_dispatches_players_and_mobs_independently(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Captive Commander",
        )
        initialize_character_state(mob, {"captive": True})
        set_state_value(
            STATE_SCOPE_CHARACTER,
            self.player,
            "captive",
            False,
        )

        self.assertTrue(
            get_state_snapshot(STATE_SCOPE_CHARACTER, mob)["captive"]
        )
        self.assertFalse(
            get_state_snapshot(STATE_SCOPE_CHARACTER, self.player)["captive"]
        )
        self.assertEqual(MobState.objects.filter(mob=mob).count(), 1)
        self.assertEqual(
            CharacterState.objects.filter(player=self.player).count(),
            1,
        )

        mob_id = mob.id
        mob.delete()
        self.assertFalse(MobState.objects.filter(mob_id=mob_id).exists())
        self.assertTrue(
            CharacterState.objects.filter(player=self.player).exists()
        )

    def test_player_character_state_has_a_bounded_json_size(self):
        with self.assertRaisesRegex(ValueError, "byte limit"):
            set_state_value(
                STATE_SCOPE_CHARACTER,
                self.player,
                "large",
                "x" * CHARACTER_STATE_MAX_ENCODED_BYTES,
            )

    def test_mob_actor_state_wins_over_viewer_player_state(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Jailer",
        )
        initialize_character_state(mob, {"captive": True})
        set_state_value(
            STATE_SCOPE_CHARACTER,
            self.player,
            "captive",
            False,
        )

        value = resolve_path(
            "state.character.captive",
            ConditionContext(
                actor=mob,
                player=self.player,
                room=self.room,
                world=self.spawn_world,
            ),
        )

        self.assertTrue(value)

    def test_ability_state_component_can_mutate_mob_character_state(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Spellbound Guard",
        )
        ability = AbilityDefinition.objects.create(
            world=self.world,
            slug="raise-alarm",
            name="Raise Alarm",
        )

        event = execute_state_component(
            component={
                "type": "state",
                "scope": "character",
                "op": "increment",
                "key": "alarm_count",
                "amount": 2,
            },
            player=mob,
            ability=ability,
            room=self.room,
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.recipients, [mob.key])
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, mob)["alarm_count"],
            2,
        )

    def test_condition_context_loads_each_state_scope_once(self):
        set_state_value(
            STATE_SCOPE_WORLD,
            self.spawn_world,
            "weather",
            "rain",
        )
        set_state_value(
            STATE_SCOPE_WORLD,
            self.spawn_world,
            "season",
            "winter",
        )
        context = ConditionContext(
            actor=self.player,
            player=self.player,
            room=self.room,
            world=self.spawn_world,
        )

        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(
                resolve_path("state.world.weather", context),
                "rain",
            )
            self.assertEqual(
                resolve_path("state.world.season", context),
                "winter",
            )

        self.assertEqual(len(queries), 1)

    def test_clear_absent_state_does_not_create_sparse_rows(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Stateless Guard",
        )

        self.assertFalse(
            clear_state_value(STATE_SCOPE_CHARACTER, mob, "missing")
        )
        self.assertFalse(MobState.objects.filter(mob=mob).exists())

    def test_clearing_sparse_zone_state_preserves_door_reset_schedule(self):
        run_spawn_plans_for_world(world=self.spawn_world, initial=True)
        schedule = ZoneDoorResetSchedule.objects.get(
            world=self.spawn_world,
            zone=self.zone,
        )
        deadline = schedule.next_reset_ts
        policy_version = schedule.policy_version
        set_state_value(
            STATE_SCOPE_ZONE,
            self.zone,
            "temporary",
            True,
            runtime_world=self.spawn_world,
        )
        self.assertTrue(
            ZoneState.objects.filter(
                world=self.spawn_world,
                zone=self.zone,
            ).exists()
        )

        self.assertTrue(
            clear_state_value(
                STATE_SCOPE_ZONE,
                self.zone,
                "temporary",
                runtime_world=self.spawn_world,
            )
        )

        self.assertFalse(
            ZoneState.objects.filter(
                world=self.spawn_world,
                zone=self.zone,
            ).exists()
        )
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_reset_ts, deadline)
        self.assertEqual(schedule.policy_version, policy_version)

    def test_deleting_runtime_world_cascades_all_runtime_scope_rows(self):
        self._author_defaults()
        runtime = self.world.create_spawn_world()
        runtime_id = runtime.id

        self.assertTrue(WorldState.objects.filter(world=runtime).exists())
        self.assertTrue(ZoneState.objects.filter(world=runtime).exists())
        self.assertTrue(RoomState.objects.filter(world=runtime).exists())

        runtime.delete()

        self.assertFalse(WorldState.objects.filter(world_id=runtime_id).exists())
        self.assertFalse(ZoneState.objects.filter(world_id=runtime_id).exists())
        self.assertFalse(RoomState.objects.filter(world_id=runtime_id).exists())

    def test_mob_definition_initial_state_applies_only_to_new_mobs(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="captive-commander",
            name="Captive Commander",
            initial_state={"captive": True},
        )

        first = definition.spawn(self.room, self.spawn_world)
        self.assertTrue(
            get_state_snapshot(STATE_SCOPE_CHARACTER, first)["captive"]
        )

        set_state_value(
            STATE_SCOPE_CHARACTER,
            first,
            "captive",
            False,
        )
        definition.initial_state = {"captive": True, "rank": "commander"}
        definition.save(update_fields=["initial_state"])
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, first),
            {"captive": False},
        )

        second = definition.spawn(self.room, self.spawn_world)
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, second),
            {"captive": True, "rank": "commander"},
        )

    def test_spawn_entry_initial_state_overrides_definition_and_reseeds_replacement(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="greek-commander",
            name="Greek Commander",
            initial_state={"captive": True, "faction": "greek"},
        )
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="camp-spawns",
            name="Camp Spawns",
            respawn_policy={"mode": "fixed", "seconds": 0},
        )
        entry = SpawnEntry.objects.create(
            plan=plan,
            slug="greek-commander",
            source=f"mobdefinition.{definition.slug}",
            target_room=self.room,
            count=1,
            initial_state={"captive": False, "guarded": True},
        )

        run_spawn_plans_for_world(world=self.spawn_world, initial=True)
        first = Mob.objects.get(
            world=self.spawn_world,
            definition=definition,
        )
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, first),
            {
                "captive": False,
                "faction": "greek",
                "guarded": True,
            },
        )

        set_state_value(
            STATE_SCOPE_CHARACTER,
            first,
            "captive",
            True,
        )
        entry.initial_state = {"captive": False, "guarded": False}
        entry.save(update_fields=["initial_state"])
        run_spawn_plans_for_world(world=self.spawn_world, repopulate=True)
        self.assertTrue(
            get_state_snapshot(STATE_SCOPE_CHARACTER, first)["captive"]
        )

        Mob.objects.filter(pk=first.pk).delete()
        run_spawn_plans_for_world(world=self.spawn_world, repopulate=True)
        replacement = Mob.objects.get(
            world=self.spawn_world,
            definition=definition,
        )
        self.assertNotEqual(replacement.pk, first.pk)
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, replacement),
            {
                "captive": False,
                "faction": "greek",
                "guarded": False,
            },
        )
