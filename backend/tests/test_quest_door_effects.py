from copy import deepcopy

import yaml
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from builders.instance_templates import create_instance_template
from builders.models import MobDefinition
from quests.models import QuestInstance, QuestTemplate
from quests.services.effects import apply_quest_effects
from quests.services.engine import QuestRuntimeError, accept_template, choose_for_instance
from spawns.models import DoorState, Mob, Player
from tests.base import WorldTestCase
from tests.combat_fixtures import dispatch_and_drain_combat
from tests.utils import capture_game_messages
from worlds.models import Door, Doorway


class TestQuestDoorEffects(WorldTestCase):
    message = "The watchman turns his key and swings the barred door open."

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        self.template = create_instance_template(
            base_world=self.world, author=self.user, name="Orientation",
        )
        self.room = self.template.config.starting_room
        self.court = self.room.create_at("east")
        self.doorway = Doorway.objects.create(
            world=self.template, default_state="locked",
        )
        for origin, destination, direction in (
            (self.room, self.court, "east"),
            (self.court, self.room, "west"),
        ):
            Door.objects.create(
                doorway=self.doorway, from_room=origin, to_room=destination,
                direction=direction, name="barred cell door",
            )
        self.runtime = self.template.create_spawn_world()
        self.player.world = self.runtime
        self.player.room = self.room
        self.player.in_game = True
        self.player.health = 100
        self.player.stamina = 100
        self.player.save()
        self.guard_definition = MobDefinition.objects.create(
            world=self.world, slug="watchman", name="a watchman",
            keywords="watchman", attackable=False,
        )
        self.guard = self.guard_definition.spawn(self.room, self.runtime)
        self.effect = {
            "type": "mob_command",
            "mob_definition": "mobdefinition.watchman",
            "command": f"/open east -- {self.message}",
        }
        manifest = {
            "kind": "quest",
            "metadata": {"slug": "a-debt-to-athens", "name": "A Debt to Athens"},
            "spec": {
                "status": "active",
                "discovery": {"sources": [{
                    "type": "npc_dialogue",
                    "mob_definition": "mobdefinition.watchman",
                }]},
                "steps": [{
                    "id": "practice", "kind": "objective",
                    "effects": [self.effect],
                    "objectives": [{
                        "id": "report",
                        "tracker": {"event": "cmd.talk.success"},
                    }],
                    "transitions": [{
                        "when": {"objective_complete": "report"}, "goto": "done",
                    }],
                }, {"id": "done", "kind": "resolution"}],
            },
        }
        response = self.client.post(
            reverse("builder-world-manifest-apply", args=[self.template.pk]),
            {"manifest": yaml.safe_dump(manifest)}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.quest = QuestTemplate.objects.get(world=self.template, slug="a-debt-to-athens")
        with capture_game_messages():
            dispatch_and_drain_combat(self.player.pk, "talk watchman")

    def _state(self, world=None):
        return DoorState.objects.get_or_create(
            world=world or self.runtime, doorway=self.doorway,
            defaults={"state": "locked"},
        )[0].state

    def _accept_http(self):
        return self.client.post(
            reverse("game-quest-opportunity-accept", args=[self.quest.slug]),
            {}, format="json", HTTP_X_PLAYER_ID=str(self.player.pk),
        )

    def _door_messages(self, messages):
        return [entry for entry in messages
                if entry["message"]["type"] == "door.state_changed"]

    def _assert_start_before_door(self, messages):
        self.assertEqual([
            entry["message"]["type"] for entry in messages
            if entry["player_key"] == self.player.key
            and entry["message"]["type"] in {"quest.instance.started", "door.state_changed"}
        ], ["quest.instance.started", "door.state_changed"])

    def _save_graph(self, graph):
        self.quest.graph = graph
        self.quest.save(update_fields=["graph"])

    def test_talk_and_normal_commands_leave_door_locked_then_accept_opens_it(self):
        self.assertEqual(self._state(), "locked")
        with capture_game_messages():
            dispatch_and_drain_combat(self.player.pk, "open east")
            dispatch_and_drain_combat(self.player.pk, "east")
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.pk)
        self.assertEqual(self._state(), "locked")

        with capture_game_messages() as messages:
            dispatch_and_drain_combat(self.player.pk, "quest accept a-debt-to-athens")
        self._assert_start_before_door(messages)
        self.assertEqual(self._state(), "open")
        door_messages = self._door_messages(messages)
        self.assertEqual(len(door_messages), 1)
        self.assertEqual(door_messages[0]["message"]["text"], self.message)
        self.assertTrue(QuestInstance.objects.filter(player=self.player, status="active").exists())
        with capture_game_messages():
            dispatch_and_drain_combat(self.player.pk, "east")
            self.player.refresh_from_db()
            self.assertEqual(self.player.room_id, self.court.pk)
            dispatch_and_drain_combat(self.player.pk, "west")
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.pk)

    def test_http_accept_opens_both_faces_only_in_the_accepting_players_run(self):
        other_runtime = self.template.create_spawn_world()
        other = self.create_player("Other", world=other_runtime, room=self.room)
        other.in_game = True
        other.save(update_fields=["in_game"])
        self.guard_definition.spawn(self.room, other_runtime)
        observer = self.create_player("Observer", world=self.runtime, room=self.court)
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        with capture_game_messages() as messages:
            response = self._accept_http()
        self.assertEqual(response.status_code, 201, response.data)
        self._assert_start_before_door(messages)
        self.assertEqual(self._state(), "open")
        self.assertEqual(self._state(other_runtime), "locked")
        door_messages = self._door_messages(messages)
        self.assertEqual({entry["player_key"] for entry in door_messages},
                         {self.player.key, observer.key})
        self.assertTrue(all(entry["message"]["text"] == self.message for entry in door_messages))
        with capture_game_messages() as repeated:
            response = self._accept_http()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._door_messages(repeated), [])

    def test_talk_prompt_hides_after_command_or_http_acceptance_and_report_still_works(self):
        response = self.client.post(
            reverse("builder-world-manifest-apply", args=[self.template.pk]),
            {"manifest": yaml.safe_dump({
                "kind": "trigger",
                "metadata": {"name": "Talk to watchman"},
                "spec": {
                    "scope": "room", "kind": "command",
                    "target": f"room@{self.room.relative_id}",
                    "match": "talk watchman", "script": "talk watchman",
                    "display_action_in_room": True, "gate_delay": 0,
                    "conditions": {"not": {"quest_accepted": self.quest.slug}},
                },
            })}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        other_runtime = self.template.create_spawn_world()
        other = self.create_player("Other", world=other_runtime, room=self.room)
        other.in_game = True
        other.save(update_fields=["in_game"])
        self.guard_definition.spawn(self.room, other_runtime)

        def actions(player):
            with capture_game_messages() as messages:
                dispatch_and_drain_combat(player.pk, "look")
            return next(entry["message"]["data"]["target"]["actions"] for entry in messages
                        if entry["message"]["type"] == "cmd.look.success")

        for player, via_http in ((self.player, False), (other, True)):
            with self.subTest(via_http=via_http):
                self.assertIn("talk watchman", actions(player))
                with capture_game_messages() as messages:
                    dispatch_and_drain_combat(player.pk, "TALK WATCHMAN")
                self.assertEqual(sum(entry["message"]["type"] == "cmd.talk.success"
                                     for entry in messages), 1)
                self.assertIn("talk watchman", actions(player))
                with capture_game_messages():
                    if via_http:
                        response = self.client.post(
                            reverse("game-quest-opportunity-accept", args=[self.quest.slug]),
                            {}, format="json", HTTP_X_PLAYER_ID=str(player.pk),
                        )
                        self.assertEqual(response.status_code, 201, response.data)
                    else:
                        dispatch_and_drain_combat(player.pk, f"quest accept {self.quest.slug}")
                self.assertNotIn("talk watchman", actions(player))
                with capture_game_messages():
                    dispatch_and_drain_combat(player.pk, "talk watchman")
                self.assertEqual(QuestInstance.objects.get(player=player, template=self.quest).resolution,
                                 "complete")
                self.assertNotIn("talk watchman", actions(player))

    def test_auto_start_announces_quest_before_door_output(self):
        self.quest.discovery_policy = {"sources": [{"type": "auto_start"}]}
        self.quest.save(update_fields=["discovery_policy"])
        with capture_game_messages() as messages:
            dispatch_and_drain_combat(self.player.pk, "look")
        self._assert_start_before_door(messages)
        self.assertEqual(self._state(), "open")

    def test_later_failed_effect_rolls_back_acceptance_door_and_output(self):
        self._state()
        graph = deepcopy(self.quest.graph)
        graph["steps"][0]["effects"].append({**self.effect, "command": "/open north"})
        self._save_graph(graph)
        with capture_game_messages() as messages:
            response = self._accept_http()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "door_not_found")
        self.assertEqual(self._state(), "locked")
        self.assertFalse(QuestInstance.objects.filter(player=self.player).exists())
        self.assertEqual(self._door_messages(messages), [])
        self.assertNotIn("quest.instance.started", [entry["message"]["type"] for entry in messages])

    def test_missing_local_guard_cannot_use_another_runs_guard(self):
        self.guard.delete()
        other_runtime = self.template.create_spawn_world()
        self.guard_definition.spawn(self.room, other_runtime)
        with capture_game_messages() as messages:
            with self.assertRaises(QuestRuntimeError) as raised:
                accept_template(self.player, self.quest)
        self.assertEqual(raised.exception.code, "quest_mob_not_found")
        self.assertEqual(self._state(), "locked")
        self.assertFalse(QuestInstance.objects.filter(player=self.player).exists())
        self.assertEqual(self._door_messages(messages), [])

    def test_door_command_rejects_command_chains(self):
        graph = deepcopy(self.quest.graph)
        graph["steps"][0]["effects"][0]["command"] = "/open east && /open west"
        self._save_graph(graph)
        with capture_game_messages():
            response = self._accept_http()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "command_chain_not_allowed")
        self.assertEqual(self._state(), "locked")
        self.assertFalse(QuestInstance.objects.filter(player=self.player).exists())

    def test_transition_effect_door_output_is_returned(self):
        graph = deepcopy(self.quest.graph)
        graph["steps"][0]["effects"] = []
        graph["steps"][0]["transitions"][0]["effects"] = [self.effect]
        self._save_graph(graph)
        accept_template(self.player, self.quest)
        with capture_game_messages() as messages:
            dispatch_and_drain_combat(self.player.pk, "talk watchman")
        self.assertEqual(self._state(), "open")
        self.assertEqual(len(self._door_messages(messages)), 1)

    def test_reward_effect_door_output_is_returned(self):
        self._save_graph({"steps": [{"id": "done", "kind": "resolution"}]})
        self.quest.reward_policy = {"complete": [self.effect]}
        self.quest.save(update_fields=["reward_policy"])
        result = accept_template(self.player, self.quest)
        self.assertEqual(self._state(), "open")
        self.assertEqual([event.type for event in result.events],
                         ["cmd./open.success", "door.state_changed", "quest.instance.resolved"])
        self.assertEqual([event.text for event in result.events if event.type == "door.state_changed"],
                         [self.message])

    def test_choice_effect_output_and_failed_transition_rollback(self):
        self._save_graph({"steps": [{
            "id": "offer", "kind": "storylet",
            "choices": [{"id": "go", "text": "Go", "effects": [self.effect]}],
        }, {"id": "done", "kind": "resolution"}]})
        result = accept_template(self.player, self.quest)
        with capture_game_messages() as messages:
            dispatch_and_drain_combat(self.player.pk, "quest choose a-debt-to-athens go")
        self.assertEqual(self._state(), "locked")
        self.assertEqual(self._door_messages(messages), [])
        graph = deepcopy(self.quest.graph)
        graph["steps"][0]["choices"][0]["goto"] = "done"
        self._save_graph(graph)
        result = choose_for_instance(self.player, str(result.quest_instance.pk), "go")
        self.assertEqual(self._state(), "open")
        self.assertEqual([event.text for event in result.events if event.type == "door.state_changed"],
                         [self.message])

    def test_door_effect_query_count_does_not_grow_with_unrelated_mobs(self):
        self._state()
        instance = QuestInstance.objects.create(
            player=self.player, world=self.runtime, template=self.quest,
            status="active", current_step_id="practice",
        )

        def measure():
            player = Player.objects.get(pk=self.player.pk)
            template = QuestTemplate.objects.get(pk=self.quest.pk)
            with CaptureQueriesContext(connection) as queries:
                apply_quest_effects(instance, [self.effect], player=player, template=template)
            return len(queries)

        baseline = measure()
        DoorState.objects.filter(world=self.runtime, doorway=self.doorway).update(state="locked")
        Mob.objects.bulk_create([
            Mob(world=self.runtime, room=self.court, name=f"Bystander {index}")
            for index in range(100)
        ])
        self.assertEqual(measure(), baseline)
