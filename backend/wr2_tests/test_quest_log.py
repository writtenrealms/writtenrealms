from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from quests.models import QuestInstance, QuestTemplate
from quests.services.engine import can_start_template
from quests.services.quest_log import build_quest_log
from tests.base import WorldTestCase


NOW = datetime(2026, 7, 20, 21, 0, tzinfo=datetime_timezone.utc)


class QuestLogTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        self.client.force_authenticate(self.user)
        self.headers = {"HTTP_X_PLAYER_ID": str(self.player.id)}

    def create_quest(
        self,
        slug,
        *,
        mode="never",
        cooldown_seconds=0,
        status="active",
    ):
        return QuestTemplate.objects.create(
            world=self.world,
            slug=slug,
            name=slug.replace("-", " ").title(),
            quest_type="quest",
            scope="player",
            status=status,
            repeatability_mode=mode,
            repeatability_cooldown_seconds=cooldown_seconds,
            max_active=1,
            discovery_policy={},
            slot_schema={},
            graph={
                "steps": [
                    {
                        "id": "offer",
                        "kind": "storylet",
                        "recap": f"Consider {slug}.",
                        "text": {"body": f"The story of {slug}."},
                    },
                    {
                        "id": "resolved",
                        "kind": "resolution",
                        "recap": f"Finished {slug}.",
                    },
                ],
            },
            reward_policy={},
        )

    def create_instance(
        self,
        template,
        *,
        status="resolved",
        resolution="complete",
        resolved_at=None,
    ):
        return QuestInstance.objects.create(
            world=self.player.world,
            template=template,
            player=self.player,
            status=status,
            resolution=resolution if status == "resolved" else None,
            current_step_id="resolved" if status == "resolved" else "offer",
            slot_bindings={},
            local_state={},
            visible_objective_ids=[],
            resolved_at=(resolved_at or NOW) if status == "resolved" else None,
        )


class TestQuestLogApi(QuestLogTestCase):
    def test_groups_current_state_and_uses_latest_successful_completion(self):
        active_template = self.create_quest("active-repeatable", mode="always")
        self.create_instance(active_template, status="active")
        self.create_instance(
            active_template,
            resolved_at=NOW - timedelta(days=1),
        )

        cooldown_template = self.create_quest(
            "barley-seeds",
            mode="cooldown",
            cooldown_seconds=1200,
        )
        self.create_instance(
            cooldown_template,
            resolved_at=NOW - timedelta(minutes=10),
        )
        latest_success = self.create_instance(
            cooldown_template,
            resolved_at=NOW - timedelta(minutes=5),
        )
        self.create_instance(
            cooldown_template,
            resolution="abandoned",
            resolved_at=NOW - timedelta(minutes=1),
        )

        final_template = self.create_quest("final-quest")
        final_instance = self.create_instance(final_template, resolved_at=NOW - timedelta(hours=1))

        abandoned_template = self.create_quest("abandoned-only", mode="always")
        self.create_instance(
            abandoned_template,
            resolution="abandoned",
            resolved_at=NOW - timedelta(seconds=10),
        )

        with patch("quests.services.quest_log.timezone.now", return_value=NOW):
            response = self.client.get(reverse("game-quest-log"), **self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["server_time"], NOW.isoformat())
        self.assertEqual(
            [entry["template"]["slug"] for entry in response.data["active"]],
            ["active-repeatable"],
        )
        self.assertEqual(
            [entry["template"]["slug"] for entry in response.data["repeatable"]],
            ["barley-seeds"],
        )
        self.assertEqual(
            [entry["template"]["slug"] for entry in response.data["resolved"]],
            ["final-quest"],
        )

        repeatable = response.data["repeatable"][0]
        self.assertEqual(repeatable["id"], latest_success.id)
        self.assertEqual(
            repeatable["repeatability"],
            {
                "mode": "cooldown",
                "cooldown_seconds": 1200,
                "state": "waiting",
                "ready_at": (NOW + timedelta(minutes=15)).isoformat(),
                "remaining_seconds": 900,
                "template_status": "active",
            },
        )
        self.assertEqual(response.data["resolved"][0]["id"], final_instance.id)
        self.assertEqual(
            response.data["active"][0]["repeatability"]["state"],
            "unavailable",
        )


class TestQuestLogProjection(QuestLogTestCase):
    def test_all_non_abandoned_resolutions_match_runtime_repeatability(self):
        template = self.create_quest(
            "compromised-quest",
            mode="cooldown",
            cooldown_seconds=1200,
        )
        self.create_instance(
            template,
            resolution="compromised",
            resolved_at=NOW - timedelta(minutes=5),
        )

        with patch("quests.services.engine.timezone.now", return_value=NOW):
            self.assertFalse(can_start_template(self.player, template))

        payload = build_quest_log(self.player, now=NOW)
        self.assertEqual(
            [entry["template"]["slug"] for entry in payload["repeatable"]],
            ["compromised-quest"],
        )
        self.assertEqual(
            payload["repeatable"][0]["repeatability"]["remaining_seconds"],
            900,
        )

    def test_live_template_edits_reclassify_history_without_migration(self):
        template = self.create_quest("mutable-quest")
        self.create_instance(template, resolved_at=NOW - timedelta(seconds=60))

        initial = build_quest_log(self.player, now=NOW)
        self.assertEqual([entry["template"]["slug"] for entry in initial["resolved"]], ["mutable-quest"])

        template.repeatability_mode = "cooldown"
        template.repeatability_cooldown_seconds = 120
        template.save(update_fields=["repeatability_mode", "repeatability_cooldown_seconds"])
        waiting = build_quest_log(self.player, now=NOW)
        self.assertEqual(waiting["resolved"], [])
        self.assertEqual(waiting["repeatable"][0]["repeatability"]["state"], "waiting")
        self.assertEqual(waiting["repeatable"][0]["repeatability"]["remaining_seconds"], 60)

        template.repeatability_cooldown_seconds = 30
        template.save(update_fields=["repeatability_cooldown_seconds"])
        ready = build_quest_log(self.player, now=NOW)
        self.assertEqual(ready["repeatable"][0]["repeatability"]["state"], "ready")
        self.assertEqual(ready["repeatable"][0]["repeatability"]["remaining_seconds"], 0)

        template.status = "archived"
        template.save(update_fields=["status"])
        archived = build_quest_log(self.player, now=NOW)
        self.assertEqual(archived["repeatable"][0]["repeatability"]["state"], "unavailable")
        self.assertEqual(archived["repeatable"][0]["repeatability"]["template_status"], "archived")

        template.repeatability_mode = "never"
        template.save(update_fields=["repeatability_mode"])
        non_repeatable = build_quest_log(self.player, now=NOW)
        self.assertEqual(non_repeatable["repeatable"], [])
        self.assertEqual(
            [entry["template"]["slug"] for entry in non_repeatable["resolved"]],
            ["mutable-quest"],
        )

        template.status = "active"
        template.repeatability_mode = "always"
        template.save(update_fields=["status", "repeatability_mode"])
        always = build_quest_log(self.player, now=NOW)
        self.assertEqual(always["resolved"], [])
        self.assertEqual(always["repeatable"][0]["repeatability"]["state"], "ready")

    def test_buckets_are_bounded_without_per_entry_queries(self):
        for index in range(3):
            active = self.create_quest(f"active-{index}", mode="always")
            self.create_instance(active, status="active")

            repeatable = self.create_quest(f"repeatable-{index}", mode="always")
            self.create_instance(repeatable, resolved_at=NOW - timedelta(minutes=index))

            resolved = self.create_quest(f"resolved-{index}")
            self.create_instance(resolved, resolved_at=NOW - timedelta(minutes=index))

        with (
            patch("quests.services.quest_log.QUEST_LOG_ACTIVE_LIMIT", 2),
            patch("quests.services.quest_log.QUEST_LOG_REPEATABLE_LIMIT", 2),
            patch("quests.services.quest_log.QUEST_LOG_RESOLVED_LIMIT", 2),
            CaptureQueriesContext(connection) as queries,
        ):
            payload = build_quest_log(self.player, now=NOW)

        self.assertEqual(len(payload["active"]), 2)
        self.assertEqual(len(payload["repeatable"]), 2)
        self.assertEqual(len(payload["resolved"]), 2)
        self.assertEqual(
            payload["limits"],
            {
                "active": {"limit": 2, "truncated": True},
                "repeatable": {"limit": 2, "truncated": True},
                "resolved": {"limit": 2, "truncated": True},
            },
        )
        # Each populated bucket uses one bounded instance query plus bounded
        # objective/latest-journal prefetches, independent of card count.
        self.assertLessEqual(len(queries), 9)

    def test_templated_text_reuses_one_state_context_for_all_cards(self):
        for index in range(3):
            template = self.create_quest(f"templated-{index}", mode="always")
            template.graph["steps"][1]["recap"] = "{{ actor }} completed a task."
            template.save(update_fields=["graph"])
            self.create_instance(template, resolved_at=NOW - timedelta(minutes=index))

        with CaptureQueriesContext(connection) as queries:
            payload = build_quest_log(self.player, now=NOW)

        self.assertEqual(len(payload["repeatable"]), 3)
        self.assertTrue(
            all(
                entry["current_step"]["recap"] == "Joe completed a task."
                for entry in payload["repeatable"]
            )
        )
        # State rows are read once for the whole projection rather than once
        # per rendered quest string/card.
        self.assertLessEqual(len(queries), 15)
