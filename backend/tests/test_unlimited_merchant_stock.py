from datetime import timedelta
from unittest.mock import patch

import yaml
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.reverse import reverse

from builders.manifests import merchant_profile_to_manifest
from builders.models import ItemBundle, ItemDefinition, MerchantProfile, MerchantStockSlot
from spawns.actions.base import ActionError
from spawns.merchants import buy_item, list_merchant_stock, restock_merchant
from spawns.models import Item, MerchantRuntime, MerchantStockEntry
from spawns.wallet import balance_map, mutate_balances
from tests.test_merchants import MerchantTestCase


class TestUnlimitedMerchantStock(MerchantTestCase):
    def setUp(self):
        super().setUp()
        self.helm = self._item_definition("bronze-helm", "a bronze helm", cost=10)
        self.profile = MerchantProfile.objects.create(
            world=self.world, slug="armorer", name="Armorer",
            settlement_currency=self.currency, restock_interval_seconds=60,
        )
        self.slot = MerchantStockSlot.objects.create(
            profile=self.profile, key="helms", item_definition=self.helm, unlimited=True,
        )
        self.room.merchant_profile = self.profile
        self.room.save(update_fields=["merchant_profile"])
        mutate_balances(self.player, {self.currency: 200}, reason="test", emit_event=False)

    def listing(self):
        return list_merchant_stock(self.player, self.room.key)

    def buy(self, selector):
        return buy_item(self.player, self.room.key, selector)

    def test_repeated_purchases_keep_one_listing_and_create_distinct_items(self):
        listing = self.listing()
        self.assertEqual(len(listing["stock"]), 1)
        entry = listing["stock"][0]
        self.assertTrue(entry["unlimited"])
        self.assertEqual(entry["item"]["name"], self.helm.name)
        self.assertEqual(entry["item"]["key"], entry["key"])
        self.assertEqual(Item.objects.filter(definition=self.helm).count(), 0)
        runtime = MerchantRuntime.objects.get(profile=self.profile)
        self.assertIsNone(runtime.next_restock_ts)

        results = [self.buy(selector) for selector in ["1", entry["key"], "helm"] * 4]
        self.assertEqual(len({result["item"]["key"] for result in results}), 12)
        self.assertEqual(self.player.inventory.filter(definition=self.helm).count(), 12)
        self.assertEqual(balance_map(self.player)["obol"], 80)
        self.assertEqual(runtime.stock_entries.count(), 1)
        self.assertIsNone(runtime.stock_entries.get().item_id)
        self.assertEqual(self.listing()["stock"][0]["key"], entry["key"])

    def test_mixed_stock_sells_out_and_restock_preserves_unlimited_listing(self):
        sword = self._item_definition("sword", "a sword")
        MerchantStockSlot.objects.create(
            profile=self.profile, key="swords", item_definition=sword, count=1,
        )
        listing = self.listing()
        unlimited_key = listing["stock"][0]["key"]
        self.assertFalse(listing["stock"][1]["unlimited"])
        self.buy("2")
        with self.assertRaises(ActionError):
            self.buy("2")
        self.buy("1")
        runtime = MerchantRuntime.objects.get(profile=self.profile)
        self.assertIsNotNone(runtime.next_restock_ts)
        with transaction.atomic():
            restock_merchant(runtime, now=timezone.now() + timedelta(minutes=2))
        self.assertEqual(len(self.listing()["stock"]), 2)
        self.assertEqual(self.listing()["stock"][0]["key"], unlimited_key)

    def test_failed_purchase_does_not_charge_or_create_items(self):
        key = self.listing()["stock"][0]["key"]
        with patch.object(ItemDefinition, "spawn", side_effect=RuntimeError("creation failed")):
            with self.assertRaises(RuntimeError):
                self.buy(key)
        self.assertEqual(balance_map(self.player)["obol"], 200)
        self.assertFalse(Item.objects.filter(definition=self.helm).exists())
        mutate_balances(self.player, {self.currency: -200}, reason="test", emit_event=False)
        with self.assertRaises(ActionError):
            self.buy(key)
        self.assertFalse(Item.objects.filter(definition=self.helm).exists())

    def test_catalog_price_tracks_definition_changes_without_restock(self):
        key = self.listing()["stock"][0]["key"]
        self.helm.cost = 15
        self.helm.save()
        self.assertEqual(self.listing()["stock"][0]["price"]["amount"], 15)
        result = self.buy(key)
        self.assertEqual(result["price"]["amount"], 15)
        self.assertEqual(balance_map(self.player)["obol"], 185)

    def test_all_unlimited_shop_keeps_timer_for_budget_or_buyback(self):
        for changes in (
            {"funds_mode": "finite", "purchase_budget": 20, "buyback_enabled": False},
            {"funds_mode": "unlimited", "buyback_enabled": True},
        ):
            MerchantProfile.objects.filter(pk=self.profile.pk).update(**changes)
            self.listing()
            runtime = MerchantRuntime.objects.get(profile=self.profile)
            with transaction.atomic():
                restock_merchant(runtime)
            runtime.refresh_from_db()
            self.assertIsNotNone(runtime.next_restock_ts)

    def test_mob_provider_supports_the_same_unlimited_catalog(self):
        mob = self._merchant_mob(self.profile)
        listing = list_merchant_stock(self.player, mob.key)
        key = listing["stock"][0]["key"]
        buy_item(self.player, mob.key, key)
        buy_item(self.player, mob.key, key)
        self.assertEqual(mob.merchant_runtime.stock_entries.count(), 1)

    def test_listing_queries_do_not_grow_per_unlimited_slot(self):
        self.listing()
        with CaptureQueriesContext(connection) as small:
            self.listing()
        MerchantStockSlot.objects.bulk_create([
            MerchantStockSlot(profile=self.profile, key=f"helm-{i}", item_definition=self.helm, unlimited=True)
            for i in range(40)
        ])
        runtime = MerchantRuntime.objects.get(profile=self.profile)
        with transaction.atomic():
            restock_merchant(runtime)
        with CaptureQueriesContext(connection) as large:
            listing = self.listing()
        self.assertEqual(len(listing["stock"]), 41)
        self.assertLessEqual(len(large), len(small) + 1)
        self.assertFalse(Item.objects.filter(definition=self.helm).exists())

    def apply_profile(self, manifest):
        return self.client.post(
            reverse("builder-world-manifest-apply", args=[self.world.pk]),
            {"manifest": yaml.safe_dump(manifest)}, format="json",
        )

    def test_manifest_round_trip_and_switching_modes_invalidate_old_selections(self):
        old_key = self.listing()["stock"][0]["key"]
        manifest = merchant_profile_to_manifest(self.profile)
        self.assertEqual(manifest["spec"]["stock"], [{
            "key": "helms", "item_definition": "bronze-helm", "unlimited": True,
        }])
        response = self.apply_profile(manifest)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["merchant_profile"]["stock"][0]["unlimited"])
        self.assertEqual(len(self.listing()["stock"]), 1)
        self.assertEqual(MerchantStockEntry.objects.filter(runtime__profile=self.profile).count(), 1)
        with self.assertRaises(ActionError):
            self.buy(old_key)
        slot = manifest["spec"]["stock"][0]
        slot.pop("unlimited")
        slot["count"] = 2
        self.assertEqual(self.apply_profile(manifest).status_code, 200)
        self.assertEqual(len(self.listing()["stock"]), 2)
        purchased = self.buy("1")["item"]["key"]
        old_items = list(MerchantStockEntry.objects.filter(
            runtime__profile=self.profile, status="available",
        ).values_list("item_id", flat=True))
        slot.pop("count")
        slot["unlimited"] = True
        self.assertEqual(self.apply_profile(manifest).status_code, 200)
        self.assertEqual(len(self.listing()["stock"]), 1)
        self.assertEqual(Item.objects.filter(pk__in=old_items, is_pending_deletion=True).count(), 1)
        self.assertEqual(self.player.inventory.get(definition=self.helm).key, purchased)
        self.assertFalse(self.player.inventory.get(definition=self.helm).is_pending_deletion)

    def test_world_export_preserves_unlimited_stock(self):
        response = self.client.get(reverse("builder-world-export", args=[self.world.pk]))
        self.assertEqual(response.status_code, 200, response.data)
        profile = next(document for document in yaml.safe_load_all(response.data["yaml"])
                       if document.get("kind") == "merchantprofile")
        self.assertEqual(profile["spec"]["stock"], [{
            "key": "helms", "item_definition": "itemdefinition.bronze-helm", "unlimited": True,
        }])

    def test_manifest_rejects_ambiguous_or_random_unlimited_stock(self):
        bundle = ItemBundle.objects.create(world=self.world, slug="random", name="Random")
        bundle.entries.create(item_definition=self.helm, weight=1)
        for extra in ({"count": 10}, {"refresh": "fill_missing"}, {"item_bundle": "random"}):
            manifest = merchant_profile_to_manifest(self.profile)
            slot = manifest["spec"]["stock"][0]
            if "item_bundle" in extra:
                slot.pop("item_definition")
            slot.update(extra)
            response = self.apply_profile(manifest)
            self.assertEqual(response.status_code, 400, response.data)
        self.helm.randomization = {"attributes": [{"key": "strength", "min": 1, "max": 2}]}
        self.helm.save()
        response = self.apply_profile(merchant_profile_to_manifest(self.profile))
        self.assertEqual(response.status_code, 400, response.data)

    def test_item_manifest_cannot_randomize_an_unlimited_catalog_item(self):
        response = self.apply_profile({
            "kind": "itemdefinition", "metadata": {"slug": self.helm.slug},
            "spec": {"randomization": {"attributes": [{"key": "strength", "min": 1, "max": 2}]}},
        })
        self.assertEqual(response.status_code, 400, response.data)
        self.helm.refresh_from_db()
        self.assertEqual(self.helm.randomization, {})
