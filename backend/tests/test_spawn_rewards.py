import copy

import yaml
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from builders.currencies import currency_usage_map, delete_currency
from builders.models import (
    Currency,
    ItemBundle,
    ItemDefinition,
    MobCurrencyReward,
    MobDefinition,
    SpawnPlan,
    SpawnPlanRun,
)
from builders.world_export import apply_spawn_plan_manifest
from core.economy import MAX_CURRENCY_AMOUNT
from spawns.loading import run_spawn_plans_for_world
from spawns.models import Mob
from spawns.wallet import balance_map
from tests.base import WorldTestCase
from tests.combat_fixtures import dispatch_and_drain_combat
from tests.utils import apply_basic_stat_system, capture_game_messages
from worlds.models import World


class TestSpawnRewards(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.gold = Currency.objects.create(world=self.world, code="gold", name="Gold")
        self.silver = Currency.objects.create(world=self.world, code="silver", name="Silver")
        self.definition = MobDefinition.objects.create(
            world=self.world,
            slug="guard",
            name="a guard",
            keywords="guard",
            base_properties={"health_max": 1},
        )
        MobCurrencyReward.objects.create(
            mob_definition=self.definition, currency=self.gold, amount=5,
        )
        MobCurrencyReward.objects.create(
            mob_definition=self.definition, currency=self.silver, amount=2,
        )

    def entry(self, slug="guard", **overrides):
        return {
            "slug": slug,
            "source": "mobdefinition.guard",
            "target": f"room@{self.room.relative_id}",
            "count": 1,
            **overrides,
        }

    def manifest(self, entries, *, world=None):
        return {
            "kind": "spawnplan",
            "metadata": {"slug": "guards"},
            "spec": {
                "zone": f"zone@{(world or self.world).zones.get().relative_id}",
                "respawn": {"mode": "fixed", "seconds": 0},
                "entries": entries,
            },
        }

    def apply(self, entries, *, world=None):
        world = world or self.world
        return self.client.post(
            reverse("builder-world-manifest-apply", args=[world.pk]),
            {"manifest": yaml.safe_dump(self.manifest(entries, world=world))},
            format="json",
        )

    def test_rewards_round_trip_and_omission_restores_inheritance(self):
        for rewards, expected in (
            (
                {"currencies": {"gold": 1, "silver": "3"}},
                {"currencies": {"gold": 1, "silver": 3}},
            ),
            ({"currencies": {}}, {"currencies": {}}),
            ({"currencies": {"gold": 0}}, {"currencies": {}}),
            (
                {"currencies": {"silver": MAX_CURRENCY_AMOUNT}},
                {"currencies": {"silver": MAX_CURRENCY_AMOUNT}},
            ),
            ({}, {}),
            (None, {}),
        ):
            with self.subTest(rewards=rewards):
                entry = self.entry()
                if rewards is not None:
                    entry["rewards"] = rewards
                response = self.apply([entry])
                self.assertIn(response.status_code, (200, 201), response.data)
                plan = SpawnPlan.objects.get(world=self.world, slug="guards")
                self.assertEqual(plan.entries.get().rewards, expected)
                exported = yaml.safe_load(response.data["spawn_plan"]["yaml"])
                exported_entry = exported["spec"]["entries"][0]
                self.assertEqual(exported_entry.get("rewards", {}), expected)
                if not expected:
                    self.assertNotIn("rewards", exported_entry)
                apply_spawn_plan_manifest(world=self.world, manifest=exported)
                self.assertEqual(plan.entries.get().rewards, expected)

    def test_rewards_reject_invalid_shapes_codes_and_amounts(self):
        for rewards in (
            None, [], "gold", {"experience": 3}, {1: 2},
            {"currencies": None}, {"currencies": []},
            {"currencies": {"unknown": 1}},
            {"currencies": {True: 1}},
            *({"currencies": {"gold": amount}} for amount in (
                -1, 1.5, True, None, "1.5", MAX_CURRENCY_AMOUNT + 1,
            )),
        ):
            with self.subTest(rewards=rewards):
                response = self.apply([self.entry(rewards=rewards)])
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("rewards", str(response.data))
                self.assertFalse(SpawnPlan.objects.filter(world=self.world).exists())

    def test_rewards_reject_item_bundle_and_mixed_sources(self):
        ItemDefinition.objects.create(world=self.world, slug="token", name="Token")
        ItemBundle.objects.create(world=self.world, slug="tokens", name="Tokens")
        for source in ("itemdefinition.token", "itembundle.tokens"):
            for pooled in (False, True):
                with self.subTest(source=source, pooled=pooled):
                    entry = self.entry(source=source, rewards={"currencies": {}})
                    if pooled:
                        del entry["source"]
                        entry["source_pool"] = ["mobdefinition.guard", source]
                    response = self.apply([entry])
                    self.assertEqual(response.status_code, 400, response.data)
                    self.assertIn("only supported", str(response.data))

    def test_rewards_apply_to_any_mob_in_source_pool(self):
        MobDefinition.objects.create(world=self.world, slug="archer", name="an archer")
        entry = self.entry(rewards={"currencies": {"silver": 4}}, count=8)
        del entry["source"]
        entry["source_pool"] = ["mobdefinition.guard", "mobdefinition.archer"]
        response = self.apply([entry])
        self.assertEqual(response.status_code, 201, response.data)
        run_spawn_plans_for_world(world=self.spawn_world, initial=True)
        mobs = list(Mob.objects.filter(world=self.spawn_world))
        self.assertEqual(len(mobs), 8)
        self.assertTrue(all(mob.currency_reward_snapshot == {"silver": 4} for mob in mobs))

    def test_world_export_preserves_explicit_empty_rewards(self):
        self.apply([self.entry(rewards={"currencies": {}})])
        response = self.client.get(reverse("builder-world-export", args=[self.world.pk]))
        self.assertEqual(response.status_code, 200, response.data)
        documents = list(yaml.safe_load_all(response.data["yaml"]))
        plan = next(doc for doc in documents if doc and doc["kind"] == "spawnplan")
        self.assertEqual(plan["spec"]["entries"][0]["rewards"], {"currencies": {}})

    def test_instance_uses_base_currency_catalog_and_freezes_on_reward_edit(self):
        template = World.objects.new_world(
            name="Guard Instance", author=self.user, instance_of=self.world,
        )
        room = template.zones.get().rooms.get()
        entry = self.entry(
            target=f"room@{room.relative_id}", rewards={"currencies": {"silver": 1}},
        )
        response = self.apply([entry], world=template)
        self.assertEqual(response.status_code, 201, response.data)
        runtime = template.create_spawn_world(instance_ref="rewards", leader=self.player)
        run_spawn_plans_for_world(world=runtime, initial=True)
        mob = Mob.objects.get(world=runtime)
        self.assertEqual(mob.currency_reward_snapshot, {"silver": 1})
        run = SpawnPlanRun.objects.get(spawn_world=runtime)
        original_hash = run.spec_hash
        entry["rewards"] = {"currencies": {}}
        self.assertEqual(self.apply([entry], world=template).status_code, 200)
        mob.delete()
        output = run_spawn_plans_for_world(world=runtime)
        run.refresh_from_db()
        self.assertEqual(run.spec_hash, original_hash)
        self.assertTrue(output["spawn_plans"][0]["skipped"])
        self.assertFalse(Mob.objects.filter(world=runtime).exists())

    def test_spawn_and_respawn_inherit_replace_or_disable_rewards(self):
        entries = [
            self.entry("inherited"),
            self.entry("custom", rewards={"currencies": {"silver": 7}}),
            self.entry("empty", rewards={"currencies": {}}),
        ]
        self.assertEqual(self.apply(entries).status_code, 201)
        expected = {
            "inherited": {"gold": 5, "silver": 2},
            "custom": {"silver": 7},
            "empty": {},
        }
        for initial in (True, False):
            with self.subTest(initial=initial):
                run_spawn_plans_for_world(world=self.spawn_world, initial=initial)
                mobs = list(Mob.objects.filter(world=self.spawn_world))
                self.assertEqual(len(mobs), 3)
                for mob in mobs:
                    slug = mob.roll_metadata["spawn_plan"]["entry_slug"]
                    self.assertEqual(mob.currency_reward_snapshot, expected[slug])
                    self.assertEqual(
                        bool(mob.roll_metadata.get("currency_rewards_overridden")),
                        slug != "inherited",
                    )
                    mob.delete()
        self.assertEqual(
            dict(self.definition.currency_rewards.values_list("currency__code", "amount")),
            {"gold": 5, "silver": 2},
        )

    def test_live_reward_edits_apply_on_respawn_and_preserve_other_rolls(self):
        self.apply([self.entry()])
        run_spawn_plans_for_world(world=self.spawn_world, initial=True)
        mob = Mob.objects.get(world=self.spawn_world)
        placement_id = mob.spawn_placement_id
        original_traits = copy.deepcopy(mob.trait_instances)
        run = SpawnPlanRun.objects.get(spawn_world=self.spawn_world)
        for rewards, expected in (
            ({"currencies": {"silver": 3}}, {"silver": 3}),
            ({"currencies": {}}, {}),
            ({}, {"gold": 5, "silver": 2}),
        ):
            with self.subTest(rewards=rewards):
                before_rewards = copy.deepcopy(mob.currency_reward_snapshot)
                old_hash = run.spec_hash
                self.assertEqual(self.apply([self.entry(rewards=rewards)]).status_code, 200)
                run_spawn_plans_for_world(world=self.spawn_world)
                run.refresh_from_db()
                self.assertNotEqual(run.spec_hash, old_hash)
                mob.refresh_from_db()
                self.assertEqual(mob.currency_reward_snapshot, before_rewards)
                self.assertEqual(mob.spawn_placement_id, placement_id)
                mob.delete()
                run_spawn_plans_for_world(world=self.spawn_world)
                mob = Mob.objects.get(world=self.spawn_world)
                self.assertEqual(mob.currency_reward_snapshot, expected)
                self.assertEqual(mob.spawn_placement_id, placement_id)
                self.assertEqual(mob.trait_instances, original_traits)

    def test_definition_edits_preserve_overrides_even_after_entry_removal(self):
        self.apply([
            self.entry("inherited"),
            self.entry("empty", rewards={"currencies": {}}),
            self.entry("custom", rewards={"currencies": {"silver": 8}}),
        ])
        run_spawn_plans_for_world(world=self.spawn_world, initial=True)
        self.apply([self.entry("inherited")])
        response = self.client.post(
            reverse("builder-world-manifest-apply", args=[self.world.pk]),
            {"manifest": yaml.safe_dump({
                "kind": "mobdefinition",
                "metadata": {"slug": self.definition.slug},
                "spec": {"rewards": {"currencies": {"gold": 9}}},
            })},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        actual = {
            mob.roll_metadata["spawn_plan"]["entry_slug"]: mob.currency_reward_snapshot
            for mob in Mob.objects.filter(world=self.spawn_world)
        }
        self.assertEqual(actual, {"inherited": {"gold": 9}, "empty": {}, "custom": {"silver": 8}})

    def test_spawn_overrides_avoid_definition_reward_queries(self):
        self.apply([self.entry(rewards={"currencies": {"silver": 3}}, count=8)])
        with CaptureQueriesContext(connection) as queries:
            run_spawn_plans_for_world(world=self.spawn_world, initial=True)
        reward_queries = [
            query["sql"] for query in queries
            if 'FROM "builders_mobcurrencyreward"' in query["sql"]
        ]
        self.assertEqual(reward_queries, [])
        self.assertEqual(Mob.objects.filter(world=self.spawn_world).count(), 8)

    def test_plan_validates_currency_catalog_with_one_query_for_many_entries(self):
        entries = [
            self.entry(f"guard-{index}", rewards={"currencies": {"silver": 3, "gold": 1}})
            for index in range(20)
        ]
        with CaptureQueriesContext(connection) as queries:
            apply_spawn_plan_manifest(world=self.world, manifest=self.manifest(entries))
        catalog_queries = [
            query["sql"] for query in queries
            if 'FROM "builders_currency"' in query["sql"]
        ]
        self.assertEqual(len(catalog_queries), 1, catalog_queries)

    def test_currency_cannot_be_deleted_while_referenced_by_spawn_rewards(self):
        self.definition.currency_rewards.all().delete()
        self.apply([self.entry(rewards={"currencies": {"silver": 3}})])
        usages = currency_usage_map(world=self.world)
        self.assertIn({"type": "spawn entry", "count": 1}, usages[self.silver.pk])
        with self.assertRaises(ValidationError):
            delete_currency(self.silver)
        self.assertTrue(Currency.objects.filter(pk=self.silver.pk).exists())

    def test_kills_pay_only_spawn_rewards_and_can_disable_loot(self):
        apply_basic_stat_system(self.world)
        self.player.in_game = True
        self.player.health = 10000
        self.player.save(update_fields=["in_game", "health"])
        ItemDefinition.objects.create(world=self.world, slug="token", name="Token")
        self.definition.loot = {"entries": [{
            "slug": "token", "source": "itemdefinition.token", "probability": 100, "quantity": 1,
        }]}
        self.definition.save(update_fields=["loot"])
        for rewards in ({"silver": 3}, {}):
            with self.subTest(rewards=rewards):
                self.apply([self.entry(
                    rewards={"currencies": rewards},
                    loot={"inherit_definition": False, "entries": []},
                )])
                run_spawn_plans_for_world(world=self.spawn_world, initial=True)
                mob = Mob.objects.get(world=self.spawn_world)
                self.assertFalse(mob.loot.get("entries"))
                before = balance_map(self.player)
                with capture_game_messages():
                    dispatch_and_drain_combat(self.player.id, "kill guard")
                self.assertFalse(Mob.objects.filter(pk=mob.pk).exists())
                after = balance_map(self.player)
                self.assertEqual(after.get("gold", 0), before.get("gold", 0))
                self.assertEqual(
                    after.get("silver", 0),
                    before.get("silver", 0) + rewards.get("silver", 0),
                )
