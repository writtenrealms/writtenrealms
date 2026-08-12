import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import serializers as drf_serializers
from rest_framework.reverse import reverse

from builders import manifests as builder_manifests
from builders.models import (
    AbilityDefinition,
    BuilderAssignment,
    MobDefinition,
    TrainerProfile,
    TrainerProfileAbility,
    Trigger,
    WorldBuilder,
)
from config import constants as adv_consts
from tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    dispatch_text_command,
)
from spawns.actions.abilities import (
    LearnAbilityAction,
    resolve_ability_for_selector,
)
from spawns.actions.base import ActionError
from spawns.models import Player
from spawns.state_payloads import serialize_room
from spawns.trainers import (
    discover_training_providers,
    learning_statuses_for_providers,
)
from tests.base import WorldTestCase
from worlds.models import World, WorldConfig


class RoomTrainerTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.ability = self._ability("power-strike", "Power Strike", "strike")
        self.profile = self._profile(
            "arms-training",
            "Arms Training",
            [self.ability],
        )

    def _ability(self, slug, name, verb):
        return AbilityDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            command_verbs=[verb],
            target={
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": False,
            },
            availability={"classes": [], "min_level": 1},
            requirements={},
            cost={},
            cooldown={"rounds": 0},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )

    def _profile(self, slug, name, abilities, *, learning=None):
        fields = {
            "world": self.world,
            "slug": slug,
            "name": name,
        }
        if learning is not None:
            fields["learning"] = learning
        profile = TrainerProfile.objects.create(
            **fields,
        )
        TrainerProfileAbility.objects.bulk_create([
            TrainerProfileAbility(
                profile=profile,
                ability=ability,
                order=index,
            )
            for index, ability in enumerate(abilities)
        ])
        return profile

    @staticmethod
    def _messages(messages, message_type):
        return [
            envelope["message"]
            for envelope in messages
            if envelope["message"].get("type") == message_type
        ]

    def _dispatch(self, text):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, text)
        return messages

    def _trainer_mob(self, profile, *, slug="arms-master", availability="present"):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=f"the {slug}",
            keywords=slug.replace("-", " "),
            base_properties={"health_max": 10},
            trainer_profile=profile,
            trainer_availability=availability,
        )
        return definition, definition.spawn(self.room, self.spawn_world)


class RoomTrainerRuntimeTests(RoomTrainerTestCase):
    def test_mob_only_curriculum_is_hidden_but_stale_known_entry_can_be_unlearned(self):
        mob_only = self._ability("mob-curse", "Mob Curse", "mobcurse")
        mob_only.availability = {
            "actors": ["mob"],
            "classes": [],
            "min_level": 1,
        }
        mob_only.save(update_fields=["availability"])
        profile = self._profile(
            "creature-training",
            "Creature Training",
            [mob_only],
        )
        self.room.trainer_profile = profile
        self.room.save(update_fields=["trainer_profile"])

        learn_list = self._messages(
            self._dispatch("learn"),
            "cmd.ability.learn.list",
        )[0]
        self.assertNotIn(
            mob_only.slug,
            {row["slug"] for row in learn_list["data"]["abilities"]},
        )
        error = self._messages(
            self._dispatch("learn mobcurse"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(error["data"]["code"], "ability_missing")

        self.player.known_abilities = [mob_only.slug]
        self.player.ability_hotkeys = {"1": mob_only.slug}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys"])
        unlearn_list = self._messages(
            self._dispatch("unlearn"),
            "cmd.ability.unlearn.list",
        )[0]
        self.assertIn(
            mob_only.slug,
            {row["slug"] for row in unlearn_list["data"]["abilities"]},
        )
        self.assertIsNotNone(
            self._messages(
                self._dispatch("unlearn mobcurse"),
                "cmd.ability.unlearn.success",
            )[0]
        )
        self.player.refresh_from_db()
        self.assertEqual(self.player.known_abilities, [])
        self.assertEqual(self.player.ability_hotkeys, {})

    def test_mob_only_open_rows_do_not_crow_player_abilities_out_of_list(self):
        AbilityDefinition.objects.bulk_create([
            AbilityDefinition(
                world=self.world,
                slug=f"mob-only-{index:03d}",
                name=f"Mob Only {index}",
                command_verbs=[f"mobonly{index}"],
                availability={
                    "actors": ["mob"],
                    "classes": [],
                    "min_level": 1,
                },
                target={"type": "hostile", "default": "current_target"},
                components=[{"type": "damage", "profile": "basic_physical"}],
            )
            for index in range(101)
        ])
        late_player_ability = self._ability(
            "late-player-skill",
            "Late Player Skill",
            "lateplayerskill",
        )

        payload = self._messages(
            self._dispatch("learn"),
            "cmd.ability.learn.list",
        )[0]["data"]
        listed_slugs = {row["slug"] for row in payload["abilities"]}

        self.assertIn(late_player_ability.slug, listed_slugs)
        self.assertFalse(any(slug.startswith("mob-only-") for slug in listed_slugs))

    def test_malformed_stored_audience_fails_closed_for_learning(self):
        malformed = self._ability("broken-roar", "Broken Roar", "brokenroar")
        malformed.availability = {
            "actor": ["player"],
            "classes": [],
            "min_level": 1,
        }
        malformed.save(update_fields=["availability"])

        payload = self._messages(
            self._dispatch("learn"),
            "cmd.ability.learn.list",
        )[0]["data"]
        self.assertNotIn(
            malformed.slug,
            {row["slug"] for row in payload["abilities"]},
        )
        error = self._messages(
            self._dispatch("learn broken roar"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(error["data"]["code"], "ability_missing")

    def test_mob_only_slug_does_not_shadow_player_ability_verb(self):
        mob_only = self._ability("focus", "Mob Focus", "mobfocus")
        mob_only.availability = {
            "actors": ["mob"],
            "classes": [],
            "min_level": 1,
        }
        mob_only.save(update_fields=["availability"])
        player_focus = self._ability(
            "player-focus",
            "Player Focus",
            "focus",
        )

        learned = self._messages(
            self._dispatch("learn focus"),
            "cmd.ability.learn.success",
        )[0]

        self.assertEqual(learned["data"]["ability"]["slug"], player_focus.slug)

    def test_direct_alias_prefers_local_curriculum_over_remote_exact_slug(self):
        local_ability = self._ability("tide", "Riptide", "tide")
        local_ability.command_verbs = ["tide", "riptide"]
        local_ability.save(update_fields=["command_verbs"])
        local_profile = self._profile(
            "tidecaller-training",
            "Tidecaller Training",
            [local_ability],
        )
        self.room.trainer_profile = local_profile
        self.room.save(update_fields=["trainer_profile"])

        remote_ability = self._ability("riptide", "Riptide", "riptide")
        remote_ability.command_verbs = ["tide", "riptide"]
        remote_ability.save(update_fields=["command_verbs"])
        remote_profile = self._profile(
            "legacy-riptide-training",
            "Legacy Riptide Training",
            [remote_ability],
        )
        MobDefinition.objects.create(
            world=self.world,
            slug="remote-riptide-trainer",
            name="the remote riptide trainer",
            base_properties={"health_max": 10},
            trainer_profile=remote_profile,
        )

        self.assertEqual(
            resolve_ability_for_selector(self.player.world, "riptide").id,
            remote_ability.id,
        )
        learned = self._messages(
            self._dispatch("learn riptide"),
            "cmd.ability.learn.success",
        )[0]
        self.assertEqual(learned["data"]["ability"]["slug"], local_ability.slug)
        self.assertEqual(learned["data"]["trainer"]["type"], "room")

    def test_direct_room_provider_learns_lists_and_unlearns_without_mob(self):
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])

        learn_messages = self._dispatch("learn power strike")
        learned = self._messages(learn_messages, "cmd.ability.learn.success")[0]
        self.assertEqual(learned["data"]["trainer"]["type"], "room")
        self.assertEqual(learned["data"]["trainer"]["id"], self.room.id)

        unlearn_list = self._messages(
            self._dispatch("unlearn"),
            "cmd.ability.unlearn.list",
        )[0]
        self.assertEqual(unlearn_list["data"]["max_known"], 8)
        self.assertEqual(unlearn_list["data"]["limit"], 100)
        self.assertFalse(unlearn_list["data"]["truncated"])
        self.assertEqual(
            unlearn_list["data"]["abilities"][0]["unlearn_command"],
            "unlearn strike",
        )
        self.assertEqual(
            unlearn_list["data"]["abilities"][0]["trainer"]["type"],
            "room",
        )

        unlearned = self._messages(
            self._dispatch("unlearn power strike"),
            "cmd.ability.unlearn.success",
        )[0]
        self.assertEqual(unlearned["data"]["trainer"]["type"], "room")
        self.player.refresh_from_db()
        self.assertNotIn(self.ability.slug, self.player.known_abilities)

    def test_ungated_known_ability_is_listed_and_unlearned_anywhere(self):
        open_ability = self._ability("field-mend", "Field Mend", "mend")
        self.player.known_abilities = [open_ability.slug]
        self.player.save(update_fields=["known_abilities"])

        payload = self._messages(
            self._dispatch("unlearn"),
            "cmd.ability.unlearn.list",
        )[0]["data"]

        self.assertEqual(payload["abilities"][0]["slug"], "field-mend")
        self.assertIsNone(payload["abilities"][0]["trainer"])
        self.assertIsNotNone(
            self._messages(
                self._dispatch("unlearn field mend"),
                "cmd.ability.unlearn.success",
            )[0]
        )

    def test_duplicate_room_and_mob_providers_choose_room_first(self):
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])
        self._trainer_mob(self.profile)

        learned = self._messages(
            self._dispatch("learn strike"),
            "cmd.ability.learn.success",
        )[0]

        self.assertEqual(learned["data"]["trainer"]["type"], "room")
        self.assertEqual(learned["data"]["trainer"]["key"], self.room.key)

    def test_profile_limit_allows_any_two_then_swap_after_unlearning(self):
        second = self._ability("shield-wall", "Shield Wall", "wall")
        third = self._ability("spear-cast", "Spear Cast", "cast")
        profile = self._profile(
            "cross-training",
            "Cross Training",
            [self.ability, second, third],
            learning={
                "conditions": {
                    "in": [
                        "actor.archetype",
                        ["tidecaller", "mystic"],
                    ],
                },
                "max_known": 2,
            },
        )
        self.player.archetype = "tidecaller"
        self.player.save(update_fields=["archetype"])
        self.room.trainer_profile = profile
        self.room.save(update_fields=["trainer_profile"])

        initial = self._messages(
            self._dispatch("learn"),
            "cmd.ability.learn.list",
        )[0]["data"]
        self.assertEqual(
            initial["learning"],
            [{
                "profile_id": profile.id,
                "profile_key": profile.key,
                "profile_slug": profile.slug,
                "profile_name": profile.name,
                "status": "available",
                "eligible": True,
                "max_known": 2,
                "known": 0,
                "remaining": 2,
            }],
        )
        self.assertEqual(len(initial["abilities"]), 3)

        first_learn = self._messages(
            self._dispatch("learn strike"),
            "cmd.ability.learn.success",
        )[0]
        self.assertEqual(first_learn["data"]["learning"]["known"], 1)
        second_learn = self._messages(
            self._dispatch("learn wall"),
            "cmd.ability.learn.success",
        )[0]
        self.assertEqual(
            second_learn["data"]["learning"]["status"],
            "limit_reached",
        )

        limited = self._messages(
            self._dispatch("learn cast"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(limited["data"]["code"], "trainer_learning_limit")
        self.assertEqual(limited["data"]["learning"]["known"], 2)
        self.assertEqual(limited["data"]["learning"]["remaining"], 0)

        unlearned = self._messages(
            self._dispatch("unlearn strike"),
            "cmd.ability.unlearn.success",
        )[0]
        self.assertEqual(unlearned["data"]["learning"]["remaining"], 1)
        self.assertIsNotNone(
            self._messages(
                self._dispatch("learn cast"),
                "cmd.ability.learn.success",
            )[0]
        )
        self.player.refresh_from_db()
        self.assertEqual(
            set(self.player.known_abilities),
            {"shield-wall", "spear-cast"},
        )

    def test_profile_condition_denies_learning_but_not_unlearning(self):
        self.profile.learning = {
            "conditions": {"eq": ["actor.archetype", "tidecaller"]},
            "max_known": 2,
        }
        self.profile.save(update_fields=["learning"])
        self.player.archetype = "warlord"
        self.player.known_abilities = [self.ability.slug]
        self.player.save(update_fields=["archetype", "known_abilities"])
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])

        denied = self._messages(
            self._dispatch("learn strike"),
            "cmd.ability.learn.error",
        )
        # Already-known requests remain idempotent and do not re-run policy.
        self.assertEqual(denied, [])
        second = self._ability("guard-step", "Guard Step", "guardstep")
        TrainerProfileAbility.objects.create(
            profile=self.profile,
            ability=second,
            order=1,
        )
        denied = self._messages(
            self._dispatch("learn guardstep"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(denied["data"]["code"], "trainer_learning_denied")
        self.assertEqual(denied["data"]["learning"]["status"], "denied")

        unlearned = self._messages(
            self._dispatch("unlearn strike"),
            "cmd.ability.unlearn.success",
        )[0]
        self.assertEqual(unlearned["data"]["learning"]["status"], "denied")
        self.player.refresh_from_db()
        self.assertEqual(self.player.known_abilities, [])

    def test_explicit_false_and_malformed_direct_policies_fail_closed(self):
        second = self._ability("closed-guard", "Closed Guard", "closedguard")
        TrainerProfileAbility.objects.create(
            profile=self.profile,
            ability=second,
            order=1,
        )
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])

        self.profile.learning = {"conditions": False, "max_known": 2}
        self.profile.save(update_fields=["learning"])
        denied = self._messages(
            self._dispatch("learn closedguard"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(denied["data"]["code"], "trainer_learning_denied")

        self.profile.learning = {"conditions": {}, "max_known": 0}
        self.profile.save(update_fields=["learning"])
        invalid = self._messages(
            self._dispatch("learn closedguard"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(invalid["data"]["code"], "trainer_learning_denied")
        self.assertEqual(
            invalid["data"]["learning"]["reason"],
            "invalid_policy",
        )

    def test_runtime_condition_type_error_denies_direct_and_list_learning(self):
        self.profile.learning = {
            "conditions": {
                "gte": ["actor.level", "not-a-number"],
            },
            "max_known": 2,
        }
        self.profile.save(update_fields=["learning"])
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])

        listed = self._messages(
            self._dispatch("learn"),
            "cmd.ability.learn.list",
        )[0]["data"]
        self.assertEqual(listed["abilities"], [])
        self.assertEqual(listed["learning"][0]["status"], "denied")
        self.assertEqual(
            listed["learning"][0]["reason"],
            "invalid_policy",
        )

        denied = self._messages(
            self._dispatch("learn strike"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(denied["data"]["code"], "trainer_learning_denied")
        self.assertEqual(
            denied["data"]["learning"]["reason"],
            "invalid_policy",
        )

    def test_unknown_direct_policy_key_denies_direct_and_list_learning(self):
        self.profile.learning = {
            "condition": {
                "eq": ["actor.archetype", "not-this-class"],
            },
            "max_known": 2,
        }
        self.profile.save(update_fields=["learning"])
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])

        listed = self._messages(
            self._dispatch("learn"),
            "cmd.ability.learn.list",
        )[0]["data"]
        self.assertEqual(listed["abilities"], [])
        self.assertEqual(listed["learning"][0]["status"], "denied")
        self.assertEqual(
            listed["learning"][0]["reason"],
            "invalid_policy",
        )

        denied = self._messages(
            self._dispatch("learn strike"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(denied["data"]["code"], "trainer_learning_denied")
        self.assertEqual(
            denied["data"]["learning"]["reason"],
            "invalid_policy",
        )

    def test_learning_skips_denied_room_provider_for_eligible_mob_provider(self):
        denied_profile = self._profile(
            "warlord-only",
            "Warlord Training",
            [self.ability],
            learning={
                "conditions": {"eq": ["actor.archetype", "warlord"]},
                "max_known": 1,
            },
        )
        eligible_profile = self._profile(
            "tidecaller-training",
            "Tidecaller Training",
            [self.ability],
            learning={
                "conditions": {"eq": ["actor.archetype", "tidecaller"]},
                "max_known": 1,
            },
        )
        self.player.archetype = "tidecaller"
        self.player.save(update_fields=["archetype"])
        self.room.trainer_profile = denied_profile
        self.room.save(update_fields=["trainer_profile"])
        _definition, mob = self._trainer_mob(
            eligible_profile,
            slug="tidecaller-master",
        )

        listed = self._messages(
            self._dispatch("learn"),
            "cmd.ability.learn.list",
        )[0]["data"]
        self.assertEqual(listed["abilities"][0]["trainer"]["id"], mob.id)
        learned = self._messages(
            self._dispatch("learn strike"),
            "cmd.ability.learn.success",
        )[0]
        self.assertEqual(learned["data"]["trainer"]["type"], "mob")
        self.assertEqual(learned["data"]["trainer"]["id"], mob.id)

    def test_known_grants_and_inactive_entries_count_toward_shared_profile_cap(self):
        granted = self._ability("granted-guard", "Granted Guard", "gguard")
        inactive_grant = self._ability(
            "retired-guard",
            "Retired Guard",
            "rguard",
        )
        candidate = self._ability("new-guard", "New Guard", "nguard")
        inactive_grant.is_active = False
        inactive_grant.save(update_fields=["is_active"])
        profile = self._profile(
            "shared-guard-training",
            "Shared Guard Training",
            [granted, inactive_grant, candidate],
            learning={"max_known": 2, "conditions": {}},
        )
        self.player.known_abilities = [granted.slug, inactive_grant.slug]
        self.player.save(update_fields=["known_abilities"])
        self.room.trainer_profile = profile
        self.room.save(update_fields=["trainer_profile"])
        self._trainer_mob(profile, slug="second-shared-guard-trainer")

        limited = self._messages(
            self._dispatch("learn nguard"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(limited["data"]["code"], "trainer_learning_limit")
        self.assertEqual(limited["data"]["learning"]["known"], 2)

        unlearnable = self._messages(
            self._dispatch("unlearn"),
            "cmd.ability.unlearn.list",
        )[0]
        self.assertIn(
            inactive_grant.slug,
            [entry["slug"] for entry in unlearnable["data"]["abilities"]],
        )
        unlearned = self._messages(
            self._dispatch("unlearn rguard"),
            "cmd.ability.unlearn.success",
        )[0]
        self.assertEqual(unlearned["data"]["ability"]["slug"], inactive_grant.slug)
        learned = self._messages(
            self._dispatch("learn nguard"),
            "cmd.ability.learn.success",
        )[0]
        self.assertEqual(learned["data"]["ability"]["slug"], candidate.slug)

    def test_profile_limit_six_allows_full_class_curriculum(self):
        abilities = [self.ability]
        abilities.extend(
            self._ability(
                f"hoplite-skill-{index}",
                f"Hoplite Skill {index}",
                f"hoplite{index}",
            )
            for index in range(2, 7)
        )
        profile = self._profile(
            "hoplite-training",
            "Hoplite Training",
            abilities,
            learning={
                "conditions": {"eq": ["actor.archetype", "hoplite"]},
                "max_known": 6,
            },
        )
        self.player.archetype = "hoplite"
        self.player.save(update_fields=["archetype"])
        self.room.trainer_profile = profile
        self.room.save(update_fields=["trainer_profile"])

        for ability in abilities:
            learned = self._messages(
                self._dispatch(f"learn {ability.command_verbs[0]}"),
                "cmd.ability.learn.success",
            )
            self.assertEqual(len(learned), 1)
        self.player.refresh_from_db()
        self.assertEqual(len(self.player.known_abilities), 6)

    def test_exact_lookup_is_not_hidden_by_bounded_general_discovery(self):
        first_ability = self._ability("first-aid", "First Aid", "aid")
        first_profile = self._profile("first-training", "First Training", [first_ability])
        self._trainer_mob(first_profile, slug="first-trainer")
        self._trainer_mob(self.profile, slug="second-trainer")

        with patch("spawns.trainers.TRAINER_PROVIDER_LIMIT", 1):
            list_payload = self._messages(
                self._dispatch("learn"),
                "cmd.ability.learn.list",
            )[0]["data"]
            learned = self._messages(
                self._dispatch("learn strike"),
                "cmd.ability.learn.success",
            )[0]

        self.assertTrue(list_payload["truncated"])
        self.assertEqual(learned["data"]["ability"]["slug"], "power-strike")
        self.assertEqual(learned["data"]["trainer"]["type"], "mob")

    def test_parallel_runtime_mob_is_not_a_provider(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="runtime-trainer",
            name="the runtime trainer",
            trainer_profile=self.profile,
            base_properties={"health_max": 10},
        )
        parallel_world = self.world.create_spawn_world(instance_ref="parallel-trainer")
        definition.spawn(self.room, parallel_world)

        error = self._messages(
            self._dispatch("learn strike"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(error["data"]["code"], "ability_trainer_required")

        definition.spawn(self.room, self.spawn_world)
        self.assertIsNotNone(
            self._messages(
                self._dispatch("learn strike"),
                "cmd.ability.learn.success",
            )[0]
        )

    def test_base_attachment_gates_instance_until_provider_is_present(self):
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])
        instance_world = World.objects.new_world(
            name="Training Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
        )
        instance_room = instance_world.rooms.get(relative_id=1)
        instance_runtime = instance_world.create_spawn_world()
        self.player.world = instance_runtime
        self.player.room = instance_room
        self.player.save(update_fields=["world", "room"])

        error = self._messages(
            self._dispatch("learn strike"),
            "cmd.ability.learn.error",
        )[0]
        self.assertEqual(error["data"]["code"], "ability_trainer_required")

        instance_room.trainer_profile = self.profile
        instance_room.save(update_fields=["trainer_profile"])
        learned = self._messages(
            self._dispatch("learn strike"),
            "cmd.ability.learn.success",
        )[0]
        self.assertEqual(learned["data"]["trainer"]["type"], "room")
        self.assertEqual(learned["data"]["trainer"]["id"], instance_room.id)

    def test_bare_learn_query_count_does_not_scale_with_world_definitions(self):
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])
        with CaptureQueriesContext(connection) as baseline_queries:
            LearnAbilityAction().execute(self.player.id, None)

        for index in range(20):
            ability = self._ability(
                f"unused-{index}",
                f"Unused {index}",
                f"unused{index}",
            )
            profile = self._profile(
                f"unused-{index}",
                f"Unused {index}",
                [ability],
            )
            MobDefinition.objects.create(
                world=self.world,
                slug=f"unused-trainer-{index}",
                name=f"unused trainer {index}",
                trainer_profile=profile,
            )

        with CaptureQueriesContext(connection) as expanded_queries:
            LearnAbilityAction().execute(self.player.id, None)

        self.assertLessEqual(
            len(expanded_queries),
            len(baseline_queries) + 1,
        )
        self.assertLessEqual(len(expanded_queries), 12)

    def test_profile_count_query_does_not_expand_with_known_slug_payload(self):
        self.profile.learning = {"conditions": {}, "max_known": 2}
        self.profile.save(update_fields=["learning"])
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])
        self.player.known_abilities = [
            f"unknown-grant-{index}"
            for index in range(1000)
        ]
        self.player.save(update_fields=["known_abilities"])

        with CaptureQueriesContext(connection) as queries:
            LearnAbilityAction().execute(self.player.id, None)

        membership_queries = [
            query["sql"]
            for query in queries.captured_queries
            if "builders_trainerprofileability" in query["sql"].lower()
            and "ability__slug" not in query["sql"].lower()
        ]
        self.assertTrue(membership_queries)
        self.assertTrue(
            all("unknown-grant-" not in sql for sql in membership_queries)
        )

    def test_unrestricted_and_uncapped_profiles_skip_quota_membership_query(self):
        uncapped_ability = self._ability(
            "uncapped-guard",
            "Uncapped Guard",
            "ucguard",
        )
        uncapped_profile = self._profile(
            "uncapped-training",
            "Uncapped Training",
            [uncapped_ability],
            learning={"conditions": {}, "max_known": "uncapped"},
        )
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])
        self._trainer_mob(uncapped_profile, slug="uncapped-trainer")
        self.player.known_abilities = [
            self.ability.slug,
            uncapped_ability.slug,
        ]
        self.player.save(update_fields=["known_abilities"])
        providers, _truncated = discover_training_providers(self.player)

        with CaptureQueriesContext(connection) as queries:
            statuses = learning_statuses_for_providers(
                self.player,
                providers,
                known_slugs=self.player.known_abilities,
            )

        self.assertEqual(len(queries), 0)
        self.assertEqual(statuses[self.profile.id]["known"], 0)
        self.assertEqual(statuses[self.profile.id]["status"], "unrestricted")
        self.assertEqual(statuses[uncapped_profile.id]["known"], 0)
        self.assertEqual(statuses[uncapped_profile.id]["status"], "available")

    def test_room_state_only_promotes_direct_provider_and_mob_state_is_reactive(self):
        definition, mob = self._trainer_mob(
            self.profile,
            availability="alive_and_present",
        )
        payload = serialize_room(
            self.room,
            {self.room.id: self.room.key},
            {},
            viewer=self.player,
            runtime_world=self.spawn_world,
        )
        trainer_char = next(char for char in payload.chars if char.id == mob.id)
        self.assertIsNone(payload.training_provider)
        self.assertNotIn("learn", payload.actions)
        self.assertTrue(trainer_char.is_trainer)
        self.assertIn("learn", trainer_char.actions)

        mob.is_pending_deletion = True
        mob.save(update_fields=["is_pending_deletion"])
        payload = serialize_room(
            self.room,
            {self.room.id: self.room.key},
            {},
            viewer=self.player,
            runtime_world=self.spawn_world,
        )
        trainer_char = next(char for char in payload.chars if char.id == mob.id)
        self.assertFalse(trainer_char.is_trainer)
        self.assertNotIn("learn", trainer_char.actions)

        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])
        direct_payload = serialize_room(
            self.room,
            {self.room.id: self.room.key},
            {},
            viewer=self.player,
            runtime_world=self.spawn_world,
        )
        self.assertEqual(direct_payload.training_provider.type, "room")
        self.assertIn("learn", direct_payload.actions)
        self.assertIn("unlearn", direct_payload.actions)

    def test_room_training_actions_casefold_dedupe_trigger_labels(self):
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])
        target_type = ContentType.objects.get_for_model(self.room)
        for match in ("LEARN", "learn", "Unlearn", "UNLEARN"):
            Trigger.objects.create(
                world=self.world,
                scope=adv_consts.TRIGGER_SCOPE_ROOM,
                kind=adv_consts.TRIGGER_KIND_COMMAND,
                target_type=target_type,
                target_id=self.room.id,
                match=match,
                script="/echo -- Training is available.",
                display_action_in_room=True,
            )

        payload = serialize_room(
            self.room,
            {self.room.id: self.room.key},
            {},
            viewer=self.player,
            runtime_world=self.spawn_world,
        )

        self.assertEqual(sum(label.casefold() == "learn" for label in payload.actions), 1)
        self.assertEqual(sum(label.casefold() == "unlearn" for label in payload.actions), 1)


class RoomTrainerBuilderTests(RoomTrainerTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.apply_endpoint = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )
        self.room_config_endpoint = reverse(
            "builder-room-config",
            args=[self.world.pk, self.room.pk],
        )

    def _apply(self, manifest, expected_status=200):
        response = self.client.post(
            self.apply_endpoint,
            {"manifest": yaml.safe_dump(manifest, sort_keys=False)},
            format="json",
        )
        self.assertEqual(response.status_code, expected_status, response.data)
        return response

    def test_rank_two_assigned_builder_can_inspect_but_not_mutate_training(self):
        builder_user = self.create_user("assigned-room-trainer@example.com")
        builder = WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        BuilderAssignment.objects.create(
            builder=builder,
            assignment=self.room,
        )
        self.client.force_authenticate(builder_user)

        inspected = self.client.get(self.room_config_endpoint)
        denied = self.client.patch(
            self.room_config_endpoint,
            {"trainer_profile": self.profile.id},
            format="json",
        )

        self.assertEqual(inspected.status_code, 200, inspected.data)
        self.assertTrue(inspected.data["can_edit"])
        self.assertFalse(inspected.data["can_edit_training"])
        self.assertEqual(denied.status_code, 403, denied.data)
        self.room.refresh_from_db()
        self.assertIsNone(self.room.trainer_profile_id)

    def test_rank_two_assigned_builder_can_edit_room_but_not_trainer_manifest(self):
        builder_user = self.create_user("assigned-room-manifest-trainer@example.com")
        builder = WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        BuilderAssignment.objects.create(
            builder=builder,
            assignment=self.room,
        )
        self.client.force_authenticate(builder_user)
        base_manifest = {
            "kind": "room",
            "metadata": {
                "ref": f"room@{self.room.relative_id}",
                "name": self.room.name,
            },
        }

        ordinary_edit = self._apply({
            **base_manifest,
            "spec": {"description": "An assigned builder may edit this."},
        })
        denied = self.client.post(
            self.apply_endpoint,
            {
                "manifest": yaml.safe_dump(
                    {
                        **base_manifest,
                        "spec": {
                            "trainer": {
                                "profile": f"trainerprofile.{self.profile.slug}",
                            },
                        },
                    },
                    sort_keys=False,
                ),
            },
            format="json",
        )

        self.assertEqual(ordinary_edit.status_code, 200, ordinary_edit.data)
        self.assertEqual(denied.status_code, 403, denied.data)
        self.room.refresh_from_db()
        self.assertEqual(
            self.room.description,
            "An assigned builder may edit this.",
        )
        self.assertIsNone(self.room.trainer_profile_id)

    def test_profile_endpoints_room_config_and_export_order(self):
        list_response = self.client.get(
            reverse("builder-trainer-profile-list", args=[self.world.pk])
        )
        self.assertEqual(list_response.status_code, 200, list_response.data)
        self.assertEqual(list_response.data["results"][0]["ability_count"], 1)

        detail_response = self.client.get(
            reverse(
                "builder-trainer-profile-detail",
                args=[self.world.pk, self.profile.pk],
            )
        )
        self.assertEqual(detail_response.status_code, 200, detail_response.data)
        self.assertEqual(detail_response.data["abilities"][0]["slug"], "power-strike")
        self.assertEqual(detail_response.data["manifest"]["kind"], "trainerprofile")

        attached = self.client.patch(
            self.room_config_endpoint,
            {"trainer_profile": f"trainerprofile.{self.profile.slug}"},
            format="json",
        )
        self.assertEqual(attached.status_code, 200, attached.data)
        self.assertEqual(attached.data["trainer_profile"]["id"], self.profile.id)

        exported = self.client.get(
            reverse("builder-world-export", args=[self.world.pk])
        )
        self.assertEqual(exported.status_code, 200, exported.data)
        kinds = [document["kind"] for document in exported.data["documents"]]
        self.assertLess(kinds.index("ability"), kinds.index("trainerprofile"))
        self.assertLess(kinds.index("trainerprofile"), kinds.index("room"))
        room_document = next(
            document
            for document in exported.data["documents"]
            if document["kind"] == "room"
            and document["metadata"]["ref"] == f"room@{self.room.relative_id}"
        )
        self.assertEqual(
            room_document["spec"]["trainer"]["profile"],
            "trainerprofile.arms-training",
        )

        cleared = self.client.patch(
            self.room_config_endpoint,
            {"trainer_profile": None},
            format="json",
        )
        self.assertEqual(cleared.status_code, 200, cleared.data)
        self.assertIsNone(cleared.data["trainer_profile"])

    def test_learning_policy_manifest_round_trip_partial_preserve_and_clear(self):
        configured = self._apply({
            "kind": "trainerprofile",
            "metadata": {"slug": self.profile.slug},
            "spec": {
                "learning": {
                    "conditions": {
                        "in": [
                            "actor.archetype",
                            ["tidecaller", "mystic"],
                        ],
                    },
                    "max_known": 2,
                },
            },
        })
        expected = {
            "conditions": {
                "in": [
                    "actor.archetype",
                    ["tidecaller", "mystic"],
                ],
            },
            "max_known": 2,
        }
        self.assertEqual(configured.data["trainer_profile"]["learning"], expected)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.learning, expected)
        self.assertEqual(
            builder_manifests.trainer_profile_to_manifest(self.profile)["spec"][
                "learning"
            ],
            expected,
        )

        self._apply({
            "kind": "trainerprofile",
            "metadata": {"slug": self.profile.slug},
            "spec": {"notes": "Policy preserved."},
        })
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.learning, expected)

        cleared = self._apply({
            "kind": "trainerprofile",
            "metadata": {"slug": self.profile.slug},
            "spec": {"learning": {}},
        })
        self.assertEqual(cleared.data["trainer_profile"]["learning"], {})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.learning, {})

    def test_learning_policy_accepts_uncapped_and_rejects_invalid_or_query_backed(self):
        parsed = builder_manifests.parse_trainer_profile_manifest(
            world=self.world,
            manifest={
                "kind": "trainerprofile",
                "metadata": {"slug": self.profile.slug},
                "spec": {
                    "learning": {
                        "conditions": {
                            "eq": ["actor.archetype", "hoplite"],
                        },
                        "max_known": "UNCAPPED",
                    },
                },
            },
        )
        self.assertEqual(parsed.fields["learning"]["max_known"], "uncapped")

        for invalid_learning in (
            {"conditions": {}},
            {"max_known": 0},
            {"max_known": True},
            {
                "conditions": {"mob_present": "mobdefinition.teacher"},
                "max_known": 2,
            },
        ):
            with self.subTest(learning=invalid_learning):
                with self.assertRaises(drf_serializers.ValidationError):
                    builder_manifests.parse_trainer_profile_manifest(
                        world=self.world,
                        manifest={
                            "kind": "trainerprofile",
                            "metadata": {"slug": self.profile.slug},
                            "spec": {"learning": invalid_learning},
                        },
                    )

    def test_manifest_creates_profile_and_canonical_mob_attachment(self):
        created = self._apply(
            {
                "kind": "trainerprofile",
                "metadata": {"slug": "advanced-arms", "name": "Advanced Arms"},
                "spec": {
                    "notes": "Veteran instruction.",
                    "abilities": ["ability.power-strike"],
                },
            },
            expected_status=201,
        )
        self.assertEqual(created.data["trainer_profile"]["ability_count"], 1)

        mob_response = self._apply(
            {
                "kind": "mobdefinition",
                "metadata": {"slug": "veteran", "name": "a veteran"},
                "spec": {
                    "type": "humanoid",
                    "trainer": {
                        "profile": "trainerprofile.advanced-arms",
                        "availability": "alive_and_present",
                    },
                },
            },
            expected_status=201,
        )
        self.assertEqual(
            mob_response.data["mob_definition"]["trainer"]["profile"],
            "trainerprofile.advanced-arms",
        )

    def test_instance_room_attaches_inherited_profile_and_lists_base_profiles(self):
        instance_world = World.objects.new_world(
            name="Builder Training Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
        )
        instance_room = instance_world.rooms.get(relative_id=1)
        endpoint = reverse(
            "builder-room-config",
            args=[instance_world.pk, instance_room.pk],
        )

        attached = self.client.patch(
            endpoint,
            {"trainer_profile": self.profile.id},
            format="json",
        )
        listed = self.client.get(
            reverse("builder-trainer-profile-list", args=[instance_world.pk])
        )

        self.assertEqual(attached.status_code, 200, attached.data)
        self.assertEqual(attached.data["trainer_profile_world"]["id"], self.world.id)
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["results"][0]["id"], self.profile.id)

    def test_mob_availability_only_patch_preserves_profile_and_null_clears(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="availability-trainer",
            name="an availability trainer",
            trainer_profile=self.profile,
            trainer_availability="present",
        )
        self._apply({
            "kind": "mobdefinition",
            "metadata": {"slug": definition.slug, "name": definition.name},
            "spec": {"trainer": {"availability": "alive_and_present"}},
        })
        definition.refresh_from_db()
        self.assertEqual(definition.trainer_profile_id, self.profile.id)
        self.assertEqual(definition.trainer_availability, "alive_and_present")

        self._apply({
            "kind": "mobdefinition",
            "metadata": {"slug": definition.slug, "name": definition.name},
            "spec": {"trainer": {"profile": None}},
        })
        definition.refresh_from_db()
        self.assertIsNone(definition.trainer_profile_id)

    def test_legacy_inline_import_is_collision_safe_idempotent_and_canonical(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="legacy-veteran",
            name="a legacy veteran",
        )
        other_ability = self._ability("shield-wall", "Shield Wall", "wall")
        authored_collision = self._profile(
            f"legacy-mob-{definition.id}",
            "Authored Collision",
            [other_ability],
        )
        legacy_manifest = {
            "kind": "mobdefinition",
            "metadata": {
                "slug": definition.slug,
                "name": definition.name,
            },
            "spec": {
                "trainer": {
                    "abilities": ["power-strike"],
                    "availability": "alive_and_present",
                },
            },
        }

        self._apply(legacy_manifest)
        definition.refresh_from_db()
        generated_profile_id = definition.trainer_profile_id
        self.assertNotEqual(generated_profile_id, authored_collision.id)
        self.assertEqual(definition.trainer, {})
        self.assertTrue(definition.trainer_profile.slug.startswith(
            f"legacy-mob-{definition.id}-"
        ))

        self._apply(legacy_manifest)
        definition.refresh_from_db()
        self.assertEqual(definition.trainer_profile_id, generated_profile_id)
        self.assertEqual(
            list(authored_collision.abilities.values_list("slug", flat=True)),
            ["shield-wall"],
        )
        self.assertEqual(
            TrainerProfile.objects.filter(
                world=self.world,
                slug__startswith=f"legacy-mob-{definition.id}",
            ).count(),
            2,
        )

    def test_legacy_inline_import_never_reuses_authored_patterned_profile(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="authored-pattern-trainer",
            name="an authored pattern trainer",
        )
        authored = TrainerProfile.objects.create(
            world=self.world,
            slug=f"legacy-mob-{definition.id}",
            name="Intentionally Authored",
        )
        other_ability = self._ability("authored-guard", "Authored Guard", "guard")
        TrainerProfileAbility.objects.create(
            profile=authored,
            ability=other_ability,
            order=0,
        )
        definition.trainer_profile = authored
        definition.save(update_fields=["trainer_profile"])

        self._apply({
            "kind": "mobdefinition",
            "metadata": {"slug": definition.slug, "name": definition.name},
            "spec": {"trainer": {"abilities": [self.ability.slug]}},
        })

        definition.refresh_from_db()
        authored.refresh_from_db()
        self.assertNotEqual(definition.trainer_profile_id, authored.id)
        self.assertEqual(definition.trainer_profile.legacy_source_mob_id, definition.id)
        self.assertIsNone(authored.legacy_source_mob_id)
        self.assertEqual(
            list(authored.abilities.values_list("slug", flat=True)),
            ["authored-guard"],
        )

    def test_legacy_inline_import_rejects_more_than_one_hundred_abilities(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="oversized-legacy-trainer",
            name="an oversized legacy trainer",
        )
        response = self._apply(
            {
                "kind": "mobdefinition",
                "metadata": {"slug": definition.slug, "name": definition.name},
                "spec": {
                    "trainer": {
                        "abilities": [self.ability.slug] * 101,
                    },
                },
            },
            expected_status=400,
        )

        self.assertIn("more than 100", str(response.data))
        definition.refresh_from_db()
        self.assertIsNone(definition.trainer_profile_id)

    def test_ability_delete_is_rejected_while_profile_references_it(self):
        response = self._apply(
            {
                "kind": "ability",
                "operation": "delete",
                "metadata": {"slug": self.ability.slug},
            },
            expected_status=400,
        )
        self.assertIn("trainer profile", str(response.data).lower())
        self.assertTrue(
            AbilityDefinition.objects.filter(pk=self.ability.pk).exists()
        )

    def test_profile_delete_detaches_hosts_and_does_not_resurrect_legacy_json(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="attached-trainer",
            name="an attached trainer",
            trainer_profile=self.profile,
            trainer={"abilities": [self.ability.slug], "availability": "present"},
        )
        self.room.trainer_profile = self.profile
        self.room.save(update_fields=["trainer_profile"])

        deleted = self._apply(
            {
                "kind": "trainerprofile",
                "operation": "delete",
                "metadata": {"slug": self.profile.slug},
            }
        )
        self.assertEqual(deleted.data["operation"], "deleted")
        definition.refresh_from_db()
        self.room.refresh_from_db()
        self.assertIsNone(definition.trainer_profile_id)
        self.assertEqual(definition.trainer, {})
        self.assertIsNone(self.room.trainer_profile_id)

        self._apply(
            {
                "kind": "mobdefinition",
                "metadata": {"slug": definition.slug, "name": definition.name},
                "spec": {"description": "Still retired."},
            }
        )
        definition.refresh_from_db()
        self.assertIsNone(definition.trainer_profile_id)

    def test_reverse_migration_reconstructs_ordered_inline_curriculum(self):
        second = self._ability("shield-bash", "Shield Bash", "bash")
        TrainerProfileAbility.objects.create(
            profile=self.profile,
            ability=second,
            order=2,
        )
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="rollback-trainer",
            name="a rollback trainer",
            trainer_profile=self.profile,
            trainer_availability="alive_and_present",
            trainer={},
        )
        migration = importlib.import_module(
            "builders.migrations.0257_trainer_profiles"
        )

        migration.restore_inline_mob_trainers(
            django_apps,
            SimpleNamespace(connection=SimpleNamespace(alias="default")),
        )

        definition.refresh_from_db()
        self.assertEqual(
            definition.trainer,
            {
                "abilities": ["power-strike", "shield-bash"],
                "availability": "alive_and_present",
            },
        )

    def test_forward_migration_rejects_unresolved_inline_curriculum(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="invalid-rollback-trainer",
            name="an invalid rollback trainer",
            trainer={
                "abilities": ["power-strike", "missing-technique"],
                "availability": "present",
            },
        )
        migration = importlib.import_module(
            "builders.migrations.0257_trainer_profiles"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            rf"world {self.world.id}.*mob {definition.id}.*missing-technique",
        ):
            migration.migrate_inline_mob_trainers(
                django_apps,
                SimpleNamespace(connection=SimpleNamespace(alias="default")),
            )

        definition.refresh_from_db()
        self.assertIsNone(definition.trainer_profile_id)
        self.assertEqual(
            definition.trainer["abilities"],
            ["power-strike", "missing-technique"],
        )
        self.assertFalse(
            TrainerProfile.objects.filter(
                world=self.world,
                slug__startswith=f"legacy-mob-{definition.id}",
            ).exists()
        )

    def test_forward_migration_rejects_oversized_inline_curriculum(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="oversized-migration-trainer",
            name="an oversized migration trainer",
            trainer={
                "abilities": [self.ability.slug] * 101,
                "availability": "present",
            },
        )
        migration = importlib.import_module(
            "builders.migrations.0257_trainer_profiles"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            rf"world {self.world.id}, mob {definition.id}.*101.*maximum.*100",
        ):
            migration.migrate_inline_mob_trainers(
                django_apps,
                SimpleNamespace(connection=SimpleNamespace(alias="default")),
            )

        definition.refresh_from_db()
        self.assertIsNone(definition.trainer_profile_id)
        self.assertEqual(len(definition.trainer["abilities"]), 101)

    def test_trainer_profile_ability_resolution_is_query_bounded(self):
        abilities = [self.ability]
        abilities.extend(
            self._ability(f"bulk-{index}", f"Bulk {index}", f"bulk{index}")
            for index in range(20)
        )
        manifest = {
            "kind": "trainerprofile",
            "metadata": {"slug": "bulk-training", "name": "Bulk Training"},
            "spec": {
                "abilities": [f"ability.{ability.slug}" for ability in abilities],
            },
        }

        with CaptureQueriesContext(connection) as queries:
            parsed = builder_manifests.parse_trainer_profile_manifest(
                world=self.world,
                manifest=manifest,
            )

        ability_queries = [
            query["sql"]
            for query in queries.captured_queries
            if "builders_abilitydefinition" in query["sql"].lower()
        ]
        self.assertEqual(len(ability_queries), 1)
        self.assertEqual(
            [ability.id for ability in parsed.abilities],
            [ability.id for ability in abilities],
        )

    def test_trainer_profile_bulk_resolution_reports_failing_index(self):
        with self.assertRaisesRegex(
            drf_serializers.ValidationError,
            r"spec\.abilities\[1\].*unknown ability",
        ):
            builder_manifests.parse_trainer_profile_manifest(
                world=self.world,
                manifest={
                    "kind": "trainerprofile",
                    "metadata": {"slug": "invalid-bulk", "name": "Invalid Bulk"},
                    "spec": {
                        "abilities": [self.ability.slug, "missing-technique"],
                    },
                },
            )

    def test_partial_profile_apply_preserves_concurrent_fields_and_memberships(self):
        second = self._ability("concurrent-guard", "Concurrent Guard", "cguard")
        parsed_abilities = builder_manifests.parse_trainer_profile_manifest(
            world=self.world,
            manifest={
                "kind": "trainerprofile",
                "metadata": {"id": self.profile.id},
                "spec": {"abilities": [self.ability.slug, second.slug]},
            },
        )
        TrainerProfile.objects.filter(pk=self.profile.pk).update(
            name="Concurrently Renamed",
            notes="Concurrent notes",
            learning={"conditions": {}, "max_known": 4},
        )
        builder_manifests.apply_trainer_profile_manifest(parsed_abilities)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.name, "Concurrently Renamed")
        self.assertEqual(self.profile.notes, "Concurrent notes")
        self.assertEqual(self.profile.learning["max_known"], 4)

        parsed_notes = builder_manifests.parse_trainer_profile_manifest(
            world=self.world,
            manifest={
                "kind": "trainerprofile",
                "metadata": {"id": self.profile.id},
                "spec": {"notes": "New notes"},
            },
        )
        TrainerProfileAbility.objects.filter(
            profile=self.profile,
            ability=second,
        ).update(order=0)
        TrainerProfileAbility.objects.filter(
            profile=self.profile,
            ability=self.ability,
        ).update(order=1)
        TrainerProfile.objects.filter(pk=self.profile.pk).update(
            learning={"conditions": {}, "max_known": 3},
        )
        builder_manifests.apply_trainer_profile_manifest(parsed_notes)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.notes, "New notes")
        self.assertEqual(self.profile.learning["max_known"], 3)
        self.assertEqual(
            list(self.profile.ability_entries.values_list("ability__slug", flat=True)),
            [second.slug, self.ability.slug],
        )

    def test_room_delete_is_world_scoped_and_training_is_rank_three_only(self):
        foreign_world = World.objects.new_world(
            name="Foreign Room World",
            author=self.create_user("foreign-room-owner@example.com"),
            config=WorldConfig.objects.create(),
        )
        foreign_room = foreign_world.rooms.get(relative_id=1)
        foreign_response = self.client.delete(
            reverse(
                "builder-room-detail",
                args=[self.world.pk, foreign_room.pk],
            )
        )
        self.assertEqual(foreign_response.status_code, 404, foreign_response.data)
        self.assertTrue(type(self.room).objects.filter(pk=foreign_room.pk).exists())

        unassigned = self.create_imported_room(
            relative_id=90,
            x=90,
            name="Unassigned Room",
        )
        ordinary = self.create_imported_room(
            relative_id=91,
            x=91,
            name="Assigned Ordinary Room",
        )
        training = self.create_imported_room(
            relative_id=92,
            x=92,
            name="Assigned Training Room",
        )
        training.trainer_profile = self.profile
        training.save(update_fields=["trainer_profile"])
        builder_user = self.create_user("room-delete-trainer@example.com")
        builder = WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        BuilderAssignment.objects.bulk_create([
            BuilderAssignment(builder=builder, assignment=ordinary),
            BuilderAssignment(builder=builder, assignment=training),
        ])
        self.client.force_authenticate(builder_user)

        unassigned_response = self.client.delete(
            reverse("builder-room-detail", args=[self.world.pk, unassigned.pk])
        )
        ordinary_response = self.client.delete(
            reverse("builder-room-detail", args=[self.world.pk, ordinary.pk])
        )
        training_response = self.client.delete(
            reverse("builder-room-detail", args=[self.world.pk, training.pk])
        )

        self.assertEqual(unassigned_response.status_code, 403, unassigned_response.data)
        self.assertEqual(ordinary_response.status_code, 204, ordinary_response.data)
        self.assertEqual(training_response.status_code, 403, training_response.data)
        self.assertTrue(type(self.room).objects.filter(pk=unassigned.pk).exists())
        self.assertTrue(type(self.room).objects.filter(pk=training.pk).exists())


class ConcurrentTrainerLearningLimitTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user(
            "concurrent-trainer-learning@example.com",
            "p",
        )
        self.world = World.objects.new_world(
            name="Concurrent Trainer World",
            author=user,
            config=WorldConfig.objects.create(),
        )
        apply_basic_stat_system(self.world)
        spawn_world = self.world.create_spawn_world()
        room = self.world.zones.first().rooms.first()
        self.player = Player.objects.create(
            name="Concurrent Student",
            user=user,
            world=spawn_world,
            room=room,
        )
        self.abilities = [
            AbilityDefinition.objects.create(
                world=self.world,
                slug=f"concurrent-skill-{index}",
                name=f"Concurrent Skill {index}",
                command_verbs=[f"concurrent{index}"],
                target={
                    "type": "hostile",
                    "default": "current_target",
                    "allow_out_of_combat": False,
                },
                availability={"classes": [], "min_level": 1},
                requirements={},
                cost={},
                cooldown={"rounds": 0},
                components=[{"type": "damage", "profile": "basic_physical"}],
            )
            for index in range(2)
        ]
        profile = TrainerProfile.objects.create(
            world=self.world,
            slug="concurrent-training",
            name="Concurrent Training",
            learning={"conditions": {}, "max_known": 1},
        )
        TrainerProfileAbility.objects.bulk_create([
            TrainerProfileAbility(
                profile=profile,
                ability=ability,
                order=index,
            )
            for index, ability in enumerate(self.abilities)
        ])
        room.trainer_profile = profile
        room.save(update_fields=["trainer_profile"])

    def test_player_lock_prevents_two_simultaneous_learns_exceeding_profile_cap(self):
        barrier = Barrier(2)

        def learn_once(ability_slug):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                LearnAbilityAction().execute(self.player.id, ability_slug)
                return "success"
            except ActionError as exc:
                return exc.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(
                learn_once,
                [ability.slug for ability in self.abilities],
            ))

        self.assertEqual(
            sorted(outcomes),
            ["success", "trainer_learning_limit"],
        )
        self.player.refresh_from_db()
        self.assertEqual(len(self.player.known_abilities), 1)
