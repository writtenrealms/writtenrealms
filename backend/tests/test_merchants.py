from rest_framework.reverse import reverse

from builders.currencies import create_currency
from builders.models import (
    Currency,
    ItemBundle,
    ItemDefinition,
    MerchantProfile,
    MerchantStockSlot,
    MobDefinition,
)
from config import constants as adv_consts
from spawns.actions.merchants import RestockMerchantAction
from spawns.handlers import dispatch_command
from spawns.models import MerchantBuybackEntry, MerchantRuntime, MerchantStockEntry
from spawns.wallet import balance_map, mutate_balances
from tests.base import WorldTestCase
from tests.utils import apply_basic_stat_system


class MerchantTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        self.currency = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )

    def _message_by_type(self, messages, message_type):
        for message in messages:
            if message.get("type") == message_type:
                return message
        return None

    def _dispatch_text(self, text):
        messages = []
        dispatch_command(
            command_type="text",
            player_id=self.player.id,
            payload={"text": text},
            published_messages=messages,
        )
        return messages

    def _item_definition(self, slug, name, *, cost=10, keywords=""):
        return ItemDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            keywords=keywords or slug.replace("-", " "),
            item_type=adv_consts.ITEM_TYPE_INERT,
            cost=cost,
            currency=self.currency,
        )

    def _merchant_mob(self, profile, *, attackable=False, slug="garron"):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug=slug,
            name="Garron",
            room_description="Garron watches the counter.",
            keywords=f"{slug} garron merchant",
            attackable=attackable,
            merchant_profile=profile,
            merchant_availability="alive_and_present",
            base_properties={
                "health_max": 20,
                "attack_power": 1,
                "fights_back": False,
            },
        )
        return definition.spawn(self.room, self.spawn_world)


class TestMerchantManifests(MerchantTestCase):
    def test_apply_merchant_profile_and_attach_to_mob_definition(self):
        sword = self._item_definition("iron-sword", "an iron sword", keywords="iron sword")
        bundle_item = self._item_definition("lucky-charm", "a lucky charm", keywords="lucky charm")
        bundle = ItemBundle.objects.create(
            world=self.world,
            slug="curios",
            name="Curios",
        )
        bundle.entries.create(item_definition=bundle_item, weight=1)

        apply_ep = reverse("builder-world-manifest-apply", args=[self.world.pk])
        merchant_manifest = """
kind: merchantprofile
metadata:
  slug: garron-smithy
  name: Garron's Smithy
spec:
  settlement_currency: obol
  pricing:
    sell_markup: 1.2
    buy_multiplier: 0.5
  restock:
    interval_seconds: 3600
  funds:
    mode: finite
    purchase_budget: 500
  buyback:
    enabled: true
    max_items: 3
  stock:
    - key: swords
      item_definition: itemdefinition.iron-sword
      count: 2
    - key: curios
      item_bundle: itembundle.curios
      count: 1
"""
        resp = self.client.post(apply_ep, {"manifest": merchant_manifest}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        profile = MerchantProfile.objects.get(world=self.world, slug="garron-smithy")
        self.assertEqual(profile.stock_slots.count(), 2)
        self.assertEqual(profile.stock_slots.get(key="swords").item_definition, sword)
        self.assertEqual(profile.stock_slots.get(key="curios").item_bundle, bundle)

        mob_manifest = """
kind: mobdefinition
metadata:
  slug: garron-blacksmith
  name: Garron
spec:
  room_description: Garron the Blacksmith works beside a smoking forge.
  keywords: garron blacksmith smith merchant
  combat:
    attackable: false
    health: 120
    attack_power: 12
  merchant:
    profile: merchantprofile.garron-smithy
    availability: alive_and_present
"""
        resp = self.client.post(apply_ep, {"manifest": mob_manifest}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        definition = MobDefinition.objects.get(world=self.world, slug="garron-blacksmith")
        self.assertFalse(definition.attackable)
        self.assertEqual(definition.merchant_profile, profile)
        self.assertEqual(definition.merchant_availability, "alive_and_present")
        self.assertEqual(definition.base_properties["health_max"], 120)

    def test_merchant_manifest_rejects_stock_priced_in_another_currency(self):
        drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="drachma-sword",
            name="a drachma sword",
            cost=10,
            currency=drachma,
        )
        manifest = """
kind: merchantprofile
metadata:
  slug: obol-smith
  name: Obol Smith
spec:
  settlement_currency: obol
  stock:
    - key: swords
      item_definition: itemdefinition.drachma-sword
"""

        response = self.client.post(
            reverse("builder-world-manifest-apply", args=[self.world.pk]),
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("different currency", str(response.data))
        self.assertFalse(
            MerchantProfile.objects.filter(
                world=self.world,
                slug="obol-smith",
            ).exists()
        )

    def test_merchant_manifest_rejects_fractional_purchase_budget(self):
        manifest = """
kind: merchantprofile
metadata:
  slug: fractional-budget
  name: Fractional Budget
spec:
  settlement_currency: obol
  funds:
    mode: finite
    purchase_budget: 1.9
"""

        response = self.client.post(
            reverse("builder-world-manifest-apply", args=[self.world.pk]),
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("must be an integer", str(response.data))


class TestMerchantProfileBuilderEndpoints(MerchantTestCase):
    def setUp(self):
        super().setUp()
        self.list_ep = reverse("builder-merchant-profile-list", args=[self.world.pk])

    def test_list_merchant_profiles_for_builder_ui(self):
        sword = self._item_definition("iron-sword", "an iron sword", keywords="iron sword")
        profile = MerchantProfile.objects.create(
            world=self.world,
            slug="garron-smithy",
            name="Garron's Smithy",
            funds_mode=MerchantProfile.FUNDS_MODE_FINITE,
            settlement_currency=self.currency,
            purchase_budget=500,
            buyback_enabled=True,
            buyback_max_items=3,
            restock_interval_seconds=3600,
        )
        MerchantStockSlot.objects.create(
            profile=profile,
            key="swords",
            item_definition=sword,
            count=2,
        )

        resp = self.client.get(self.list_ep, {"sort_by": "slug"})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["slug"], "garron-smithy")
        self.assertEqual(resp.data["results"][0]["stock_count"], 1)
        self.assertEqual(resp.data["results"][0]["funds_mode"], MerchantProfile.FUNDS_MODE_FINITE)
        self.assertTrue(resp.data["results"][0]["buyback_enabled"])

    def test_retrieve_merchant_profile_includes_yaml(self):
        sword = self._item_definition("iron-sword", "an iron sword", keywords="iron sword")
        profile = MerchantProfile.objects.create(
            world=self.world,
            slug="garron-smithy",
            name="Garron's Smithy",
            settlement_currency=self.currency,
        )
        MerchantStockSlot.objects.create(
            profile=profile,
            key="swords",
            item_definition=sword,
            count=2,
        )

        resp = self.client.get(
            reverse("builder-merchant-profile-detail", args=[self.world.pk, profile.pk])
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["slug"], "garron-smithy")
        self.assertEqual(resp.data["stock_count"], 1)
        self.assertIn("kind: merchantprofile", resp.data["yaml"])
        self.assertEqual(resp.data["manifest"]["kind"], "merchantprofile")


class TestMerchantRuntime(MerchantTestCase):
    def test_profile_currency_change_rolls_over_runtime_stock_generation(self):
        old_item = self._item_definition("old-token", "an old token")
        profile = MerchantProfile.objects.create(
            world=self.world,
            slug="changing-cart",
            name="Changing Cart",
            settlement_currency=self.currency,
        )
        MerchantStockSlot.objects.create(
            profile=profile,
            key="old-stock",
            item_definition=old_item,
        )
        mob = self._merchant_mob(profile, slug="changer")
        runtime = mob.merchant_runtime
        old_entry = runtime.stock_entries.get(
            status=MerchantStockEntry.STATUS_AVAILABLE,
        )
        drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="new-token",
            name="a new token",
            cost=8,
            currency=drachma,
        )
        manifest = """
kind: merchantprofile
metadata:
  slug: changing-cart
spec:
  settlement_currency: drachma
  stock:
    - key: new-stock
      item_definition: itemdefinition.new-token
"""

        response = self.client.post(
            reverse("builder-world-manifest-apply", args=[self.world.pk]),
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        runtime.refresh_from_db()
        self.assertIsNone(runtime.last_restocked_ts)

        RestockMerchantAction().execute(runtime.id)

        runtime.refresh_from_db()
        old_entry.refresh_from_db()
        self.assertEqual(runtime.settlement_currency, drachma)
        self.assertEqual(old_entry.status, MerchantStockEntry.STATUS_RETIRED)
        available = runtime.stock_entries.filter(
            status=MerchantStockEntry.STATUS_AVAILABLE,
        )
        self.assertEqual(available.count(), 1)
        self.assertEqual(available.get().currency, drachma)

    def test_non_attackable_fixed_stock_merchant_can_sell_but_not_be_killed(self):
        sword = self._item_definition("iron-sword", "an iron sword", keywords="iron sword")
        profile = MerchantProfile.objects.create(
            world=self.world,
            slug="garron-smithy",
            name="Garron's Smithy",
            settlement_currency=self.currency,
            sell_markup=1.5,
        )
        MerchantStockSlot.objects.create(
            profile=profile,
            key="swords",
            item_definition=sword,
            count=2,
        )
        mob = self._merchant_mob(profile, attackable=False)
        runtime = mob.merchant_runtime

        self.assertEqual(runtime.stock_entries.filter(status=MerchantStockEntry.STATUS_AVAILABLE).count(), 2)

        messages = self._dispatch_text("kill garron")
        error = self._message_by_type(messages, "cmd.kill.error")
        self.assertIsNotNone(error)
        self.assertEqual(error["data"]["code"], "not_attackable")

        mutate_balances(
            self.player,
            {self.currency: 100},
            reason="merchant test setup",
            emit_event=False,
        )
        messages = self._dispatch_text("buy sword from garron")
        buy_message = self._message_by_type(messages, "cmd.buy.success")
        self.assertIsNotNone(buy_message)
        self.assertEqual(
            buy_message["data"]["price"],
            {
                "amount": 15,
                "currency": "obol",
                "display": "15 Obols",
            },
        )
        self.assertEqual(self.player.inventory.filter(definition=sword).count(), 1)
        runtime.refresh_from_db()
        self.assertEqual(
            runtime.stock_entries.filter(status=MerchantStockEntry.STATUS_AVAILABLE).count(),
            1,
        )

    def test_bundle_stock_rerolls_on_restock(self):
        charm = self._item_definition("lucky-charm", "a lucky charm", keywords="lucky charm")
        bundle = ItemBundle.objects.create(
            world=self.world,
            slug="curios",
            name="Curios",
        )
        bundle.entries.create(item_definition=charm, weight=1)
        profile = MerchantProfile.objects.create(
            world=self.world,
            slug="curio-cart",
            name="Curio Cart",
            settlement_currency=self.currency,
        )
        MerchantStockSlot.objects.create(
            profile=profile,
            key="curios",
            item_bundle=bundle,
            count=2,
            refresh=MerchantStockSlot.REFRESH_REROLL_ON_RESTOCK,
        )
        mob = self._merchant_mob(profile, slug="curio")
        runtime = mob.merchant_runtime
        first_entries = list(runtime.stock_entries.filter(status=MerchantStockEntry.STATUS_AVAILABLE))
        self.assertEqual(len(first_entries), 2)
        self.assertTrue(all(entry.bundle_roll_id for entry in first_entries))

        RestockMerchantAction().execute(runtime.id)
        runtime.refresh_from_db()
        self.assertEqual(
            runtime.stock_entries.filter(status=MerchantStockEntry.STATUS_RETIRED).count(),
            2,
        )
        self.assertEqual(
            runtime.stock_entries.filter(status=MerchantStockEntry.STATUS_AVAILABLE).count(),
            2,
        )

    def test_finite_funds_and_buyback_cap_reset_on_restock(self):
        trinket = self._item_definition("tin-trinket", "a tin trinket", keywords="tin trinket")
        profile = MerchantProfile.objects.create(
            world=self.world,
            slug="pawn-cart",
            name="Pawn Cart",
            settlement_currency=self.currency,
            funds_mode=MerchantProfile.FUNDS_MODE_FINITE,
            purchase_budget=100,
            buy_multiplier=1.0,
            buyback_enabled=True,
            buyback_max_items=2,
        )
        mob = self._merchant_mob(profile, slug="pawnbroker")
        runtime = mob.merchant_runtime

        for _index in range(3):
            trinket.spawn(self.player, self.spawn_world)

        for _index in range(3):
            messages = self._dispatch_text("sell trinket to pawnbroker")
            self.assertIsNotNone(self._message_by_type(messages, "cmd.sell.success"))

        runtime.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(balance_map(self.player), {"obol": 30})
        self.assertEqual(runtime.remaining_purchase_budget, 70)
        self.assertEqual(
            runtime.buyback_entries.filter(status=MerchantBuybackEntry.STATUS_ACTIVE).count(),
            2,
        )
        self.assertEqual(
            runtime.buyback_entries.filter(status=MerchantBuybackEntry.STATUS_EXPIRED).count(),
            1,
        )

        RestockMerchantAction().execute(runtime.id)
        runtime.refresh_from_db()
        self.assertEqual(runtime.remaining_purchase_budget, 100)
        self.assertEqual(
            runtime.buyback_entries.filter(status=MerchantBuybackEntry.STATUS_ACTIVE).count(),
            0,
        )
