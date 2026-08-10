from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import (
    IntegrityError,
    close_old_connections,
    connection,
    transaction,
)
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.reverse import reverse

from builders.currencies import create_currency
from builders.models import (
    ItemDefinition,
    MerchantProfile,
    MerchantStockSlot,
    MobDefinition,
    Trigger,
)
from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.actions.information import LookAction
from spawns.handlers import dispatch_command
from spawns.merchants import (
    buy_item,
    buyback_item,
    create_or_update_room_merchant_runtime,
    invalidate_room_merchant_runtimes,
    list_buyback,
    list_merchant_offers,
    list_merchant_stock,
    sell_item,
)
from spawns.models import MerchantRuntime, MerchantStockEntry, Player
from spawns.state_payloads import serialize_room
from spawns.wallet import mutate_balances
from tests.base import WorldTestCase
from tests.utils import apply_basic_stat_system
from worlds.models import Room, World, WorldConfig


User = get_user_model()


class RoomMerchantTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.currency = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        self.item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="brass-token",
            name="a brass token",
            keywords="brass token",
            item_type=adv_consts.ITEM_TYPE_INERT,
            cost=10,
            currency=self.currency,
        )
        self.profile = MerchantProfile.objects.create(
            world=self.world,
            slug="room-counter",
            name="The Room Counter",
            settlement_currency=self.currency,
            buy_multiplier=1.0,
            buyback_enabled=True,
            buyback_max_items=3,
        )
        MerchantStockSlot.objects.create(
            profile=self.profile,
            key="tokens",
            item_definition=self.item_definition,
            count=2,
        )
        self.room.merchant_profile = self.profile
        self.room.save(update_fields=["merchant_profile"])

    def _dispatch_text(self, text):
        messages = []
        dispatch_command(
            command_type="text",
            player_id=self.player.id,
            payload={"text": text},
            published_messages=messages,
        )
        return messages

    @staticmethod
    def _message(messages, message_type):
        return next(
            (message for message in messages if message.get("type") == message_type),
            None,
        )

    def _merchant_mob(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="counter-clerk",
            name="the counter clerk",
            room_description="A clerk waits beside the counter.",
            keywords="counter clerk merchant",
            attackable=False,
            merchant_profile=self.profile,
            base_properties={
                "health_max": 20,
                "attack_power": 1,
                "fights_back": False,
            },
        )
        return definition.spawn(self.room, self.spawn_world)


class TestRoomMerchantRuntime(RoomMerchantTestCase):
    def test_explicit_room_provider_key_skips_mob_discovery(self):
        self._merchant_mob()

        with patch("spawns.merchants._merchant_mob_queryset") as mob_queryset:
            payload = list_merchant_stock(self.player, self.room.key)

        mob_queryset.assert_not_called()
        self.assertEqual(payload["merchant"]["key"], self.room.key)

    def test_implicit_provider_discovery_uses_a_bounded_mob_query(self):
        self._merchant_mob()

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(ActionError) as caught:
                list_merchant_stock(self.player, None)

        self.assertEqual(caught.exception.code, "missing_target")
        mob_queries = [
            query["sql"].lower()
            for query in queries.captured_queries
            if 'from "spawns_mob"' in query["sql"].lower()
        ]
        self.assertEqual(len(mob_queries), 1)
        self.assertIn("limit 101", mob_queries[0])

    def test_structured_shop_compatibility_publishes_canonical_list(self):
        messages = []

        dispatch_command(
            command_type="shop",
            player_id=self.player.id,
            payload={"args": []},
            published_messages=messages,
        )

        self.assertIsNotNone(self._message(messages, "cmd.list.success"))
        self.assertIsNone(self._message(messages, "cmd.shop.success"))

    def test_bare_buy_and_sell_publish_canonical_alias_errors(self):
        self.room.merchant_profile = None
        self.room.save(update_fields=["merchant_profile"])

        list_error = self._message(
            self._dispatch_text("buy"),
            "cmd.list.error",
        )
        offer_error = self._message(
            self._dispatch_text("sell"),
            "cmd.offer.error",
        )

        self.assertEqual(list_error["data"]["code"], "target_not_found")
        self.assertEqual(offer_error["data"]["code"], "target_not_found")

    def test_list_shop_and_bare_buy_publish_canonical_numbered_list(self):
        mutate_balances(
            self.player,
            {self.currency: 37},
            reason="merchant list test setup",
            emit_event=False,
        )

        for command in ("list", "shop", "buy"):
            message = self._message(
                self._dispatch_text(command),
                "cmd.list.success",
            )
            self.assertIsNotNone(message, command)
            self.assertEqual(
                [entry["number"] for entry in message["data"]["stock"]],
                [1, 2],
            )
            self.assertEqual(
                message["data"]["hint"],
                "buy # to purchase an item",
            )
            self.assertEqual(message["data"]["balance"]["amount"], 37)
            self.assertEqual(
                message["text"].splitlines()[-2:],
                ["You have 37 Obols.", "buy # to purchase an item"],
            )

        self.assertFalse(
            MerchantStockEntry.objects.filter(
                status=MerchantStockEntry.STATUS_SOLD,
            ).exists()
        )

    def test_buy_number_uses_the_numbered_list_order(self):
        mutate_balances(
            self.player,
            {self.currency: 100},
            reason="merchant ordinal buy setup",
            emit_event=False,
        )
        listed = self._message(
            self._dispatch_text("list"),
            "cmd.list.success",
        )["data"]["stock"]

        message = self._message(
            self._dispatch_text("buy 2"),
            "cmd.buy.success",
        )

        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["item"]["key"], listed[1]["item"]["key"])
        self.assertEqual(
            MerchantStockEntry.objects.get(pk=listed[0]["id"]).status,
            MerchantStockEntry.STATUS_AVAILABLE,
        )
        self.assertEqual(
            MerchantStockEntry.objects.get(pk=listed[1]["id"]).status,
            MerchantStockEntry.STATUS_SOLD,
        )

    def test_buy_number_fails_closed_if_the_snapshotted_entry_was_sold(self):
        other_player = self.create_player("Other")
        for player in (self.player, other_player):
            mutate_balances(
                player,
                {self.currency: 100},
                reason="merchant stable ordinal buy setup",
                emit_event=False,
            )
        listed = list_merchant_stock(
            self.player,
            self.room.key,
        )["stock"]
        buy_item(
            other_player,
            self.room.key,
            listed[0]["key"],
        )

        with self.assertRaises(ActionError) as caught:
            buy_item(self.player, self.room.key, "1")

        self.assertEqual(caught.exception.code, "stock_not_found")
        self.assertEqual(
            MerchantStockEntry.objects.get(pk=listed[1]["id"]).status,
            MerchantStockEntry.STATUS_AVAILABLE,
        )

    def test_list_is_capped_and_rejects_out_of_range_or_huge_ordinals(self):
        slot = self.profile.stock_slots.get()
        slot.count = 101
        slot.save(update_fields=["count", "modified_ts"])
        list_merchant_stock(self.player, None)

        with CaptureQueriesContext(connection) as queries:
            message = self._message(
                self._dispatch_text("list"),
                "cmd.list.success",
            )

        self.assertEqual(len(message["data"]["stock"]), 100)
        self.assertTrue(message["data"]["truncated"])
        self.assertEqual(message["data"]["limit"], 100)
        self.assertFalse(
            any(
                "builders_trigger" in query["sql"].lower()
                for query in queries.captured_queries
            )
        )
        for selector in (
            "101",
            "9" * 5000,
            "merchant_stock_entry.9999999999999999999",
        ):
            error = self._message(
                self._dispatch_text(f"buy {selector}"),
                "cmd.buy.error",
            )
            self.assertIsNotNone(error, selector[:20])
            self.assertIn("not for sale", error["text"])

    def test_offer_and_bare_sell_share_filtered_numbering_with_sell_number(self):
        salvage_only = ItemDefinition.objects.create(
            world=self.world,
            slug="captured-token",
            name="a captured token",
            keywords="captured token",
            item_type=adv_consts.ITEM_TYPE_INERT,
            salvage_only=True,
            cost=5,
            currency=self.currency,
        )
        other_currency = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
            plural_name="Drachmas",
        )
        wrong_currency = ItemDefinition.objects.create(
            world=self.world,
            slug="foreign-token",
            name="a foreign token",
            keywords="foreign token",
            item_type=adv_consts.ITEM_TYPE_INERT,
            cost=7,
            currency=other_currency,
        )
        silver = ItemDefinition.objects.create(
            world=self.world,
            slug="silver-token",
            name="a silver token",
            keywords="silver token",
            item_type=adv_consts.ITEM_TYPE_INERT,
            cost=20,
            currency=self.currency,
        )
        unpriced = ItemDefinition.objects.create(
            world=self.world,
            slug="unpriced-token",
            name="an unpriced token",
            keywords="unpriced token",
            item_type=adv_consts.ITEM_TYPE_INERT,
        )
        quest_item = ItemDefinition.objects.create(
            world=self.world,
            slug="quest-token",
            name="a quest token",
            keywords="quest token",
            item_type=adv_consts.ITEM_TYPE_QUEST,
            cost=1,
            currency=self.currency,
        )
        container_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="token-pouch",
            name="a token pouch",
            keywords="token pouch",
            item_type=adv_consts.ITEM_TYPE_CONTAINER,
            cost=5,
            currency=self.currency,
        )
        salvage_only.spawn(self.player, self.spawn_world)
        wrong_currency.spawn(self.player, self.spawn_world)
        unpriced.spawn(self.player, self.spawn_world)
        quest = quest_item.spawn(self.player, self.spawn_world)
        container = container_definition.spawn(self.player, self.spawn_world)
        self.item_definition.spawn(container, self.spawn_world)
        first_eligible = self.item_definition.spawn(self.player, self.spawn_world)
        second_eligible = silver.spawn(self.player, self.spawn_world)

        for command in ("offer", "sell"):
            message = self._message(
                self._dispatch_text(command),
                "cmd.offer.success",
            )
            self.assertIsNotNone(message, command)
            self.assertEqual(
                [entry["number"] for entry in message["data"]["offers"]],
                [1, 2],
            )
            self.assertEqual(
                [entry["item"]["key"] for entry in message["data"]["offers"]],
                [first_eligible.key, second_eligible.key],
            )
            self.assertEqual(message["data"]["hint"], "sell # to sell an item")

        quest_error = self._message(
            self._dispatch_text(f"sell {quest.key}"),
            "cmd.sell.error",
        )
        container_error = self._message(
            self._dispatch_text(f"sell {container.key}"),
            "cmd.sell.error",
        )
        self.assertEqual(quest_error["data"]["code"], "item_not_sellable")
        self.assertEqual(container_error["data"]["code"], "container_not_empty")

        message = self._message(
            self._dispatch_text("sell 2"),
            "cmd.sell.success",
        )
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["item"]["key"], second_eligible.key)
        self.assertTrue(self.player.inventory.filter(pk=first_eligible.pk).exists())
        self.assertFalse(self.player.inventory.filter(pk=second_eligible.pk).exists())

    def test_sell_number_fails_closed_if_snapshotted_inventory_shifted(self):
        first = self.item_definition.spawn(self.player, self.spawn_world)
        second = self.item_definition.spawn(self.player, self.spawn_world)
        list_merchant_offers(self.player, self.room.key)
        sell_item(self.player, self.room.key, first.key)

        with self.assertRaises(ActionError) as caught:
            sell_item(self.player, self.room.key, "1")

        self.assertEqual(caught.exception.code, "item_not_found")
        self.assertTrue(self.player.inventory.filter(pk=second.pk).exists())

    def test_offer_is_query_bounded_and_filters_finite_budget(self):
        self.profile.funds_mode = MerchantProfile.FUNDS_MODE_FINITE
        self.profile.purchase_budget = 5
        self.profile.buy_multiplier = 1.0
        self.profile.save(
            update_fields=[
                "funds_mode",
                "purchase_budget",
                "buy_multiplier",
                "modified_ts",
            ]
        )
        cheap_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="cheap-token",
            name="a cheap token",
            keywords="cheap token",
            item_type=adv_consts.ITEM_TYPE_INERT,
            cost=4,
            currency=self.currency,
        )
        cheap_item = cheap_definition.spawn(self.player, self.spawn_world)
        self.item_definition.spawn(self.player, self.spawn_world)
        list_merchant_stock(self.player, None)

        with CaptureQueriesContext(connection) as initial_queries:
            payload = list_merchant_offers(self.player, None)

        self.assertEqual(
            [entry["item"]["key"] for entry in payload["offers"]],
            [cheap_item.key],
        )
        for _index in range(100):
            cheap_definition.spawn(self.player, self.spawn_world)
        with CaptureQueriesContext(connection) as populated_queries:
            populated = list_merchant_offers(self.player, None)

        self.assertEqual(len(populated["offers"]), 100)
        self.assertTrue(populated["truncated"])
        self.assertEqual(populated["limit"], 100)
        self.assertLessEqual(
            len(populated_queries),
            len(initial_queries) + 1,
        )
        self.assertFalse(
            any(
                "builders_trigger" in query["sql"].lower()
                for query in populated_queries.captured_queries
            )
        )
        for selector in ("101", "9" * 5000):
            error = self._message(
                self._dispatch_text(f"sell {selector}"),
                "cmd.sell.error",
            )
            self.assertIsNotNone(error, selector[:20])
            self.assertEqual(error["data"]["code"], "item_not_found")

    def test_offer_tiny_positive_multiplier_does_not_overflow_price_filter(self):
        self.profile.buy_multiplier = 1e-12
        self.profile.save(update_fields=["buy_multiplier", "modified_ts"])
        item = self.item_definition.spawn(self.player, self.spawn_world)

        payload = list_merchant_offers(self.player, None)

        self.assertEqual(
            [entry["item"]["key"] for entry in payload["offers"]],
            [item.key],
        )

    def test_room_merchant_actions_are_list_and_offer_and_dedupe_triggers(self):
        target_type = ContentType.objects.get_for_model(Room)
        for match in ("LIST", "list", "Offer", "offer"):
            Trigger.objects.create(
                world=self.world,
                scope=adv_consts.TRIGGER_SCOPE_ROOM,
                kind=adv_consts.TRIGGER_KIND_COMMAND,
                target_type=target_type,
                target_id=self.room.id,
                match=match,
                script="/echo -- The counter is open.",
                display_action_in_room=True,
            )

        result = LookAction().execute(self.player.id)
        actions = result.events[0].data["target"]["actions"]

        self.assertEqual(sum(label.casefold() == "list" for label in actions), 1)
        self.assertEqual(sum(label.casefold() == "offer" for label in actions), 1)
        self.assertFalse(any(label.casefold() == "shop" for label in actions))

    def test_look_advertises_room_merchant_without_creating_runtime(self):
        result = LookAction().execute(self.player.id)

        self.assertFalse(MerchantRuntime.objects.exists())
        self.assertEqual(
            result.events[0].data["target"]["merchant_provider"],
            {
                "type": "room",
                "id": self.room.id,
                "key": self.room.key,
                "name": self.profile.name,
            },
        )

        payload = list_merchant_stock(self.player, None)

        runtime = MerchantRuntime.objects.get(
            world=self.spawn_world,
            room=self.room,
        )
        self.assertIsNone(runtime.mob_id)
        self.assertEqual(
            runtime.stock_entries.filter(
                status=MerchantStockEntry.STATUS_AVAILABLE,
            ).count(),
            2,
        )
        self.assertEqual(
            payload["merchant"],
            {
                "type": "room",
                "id": self.room.id,
                "key": self.room.key,
                "name": self.profile.name,
            },
        )

    def test_room_serialization_does_not_lazy_load_merchant_profile(self):
        uncached_room = Room.objects.get(pk=self.room.pk)
        self.assertNotIn("merchant_profile", uncached_room._state.fields_cache)

        payload = serialize_room(
            uncached_room,
            {uncached_room.id: uncached_room.key},
            {},
            viewer=self.player,
            runtime_world=self.spawn_world,
        )

        self.assertNotIn("merchant_profile", uncached_room._state.fields_cache)
        self.assertEqual(payload.merchant_provider.type, "room")
        self.assertEqual(payload.merchant_provider.name, uncached_room.name)

    def test_runtime_requires_exactly_one_host_and_unique_room_per_world(self):
        merchant_fields = {
            "world": self.spawn_world,
            "profile": self.profile,
            "settlement_currency": self.currency,
        }

        with self.assertRaises(IntegrityError), transaction.atomic():
            MerchantRuntime.objects.create(**merchant_fields)

        mob = self.create_mob("a clerk")
        with self.assertRaises(IntegrityError), transaction.atomic():
            MerchantRuntime.objects.create(
                **merchant_fields,
                mob=mob,
                room=self.room,
            )

        create_or_update_room_merchant_runtime(self.room, self.spawn_world)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MerchantRuntime.objects.create(
                **merchant_fields,
                room=self.room,
            )

    def test_room_merchant_supports_buy_sell_and_buyback(self):
        mutate_balances(
            self.player,
            {self.currency: 100},
            reason="room merchant test setup",
            emit_event=False,
        )

        bought = buy_item(self.player, self.room.key, "token")
        sold = sell_item(self.player, self.room.key, "token")
        buyback = list_buyback(self.player, self.room.key)
        bought_back = buyback_item(self.player, self.room.key, "token")

        expected_provider = {
            "type": "room",
            "id": self.room.id,
            "key": self.room.key,
            "name": self.profile.name,
        }
        self.assertEqual(bought["merchant"], expected_provider)
        self.assertEqual(sold["merchant"], expected_provider)
        self.assertEqual(buyback["merchant"], expected_provider)
        self.assertEqual(bought_back["merchant"], expected_provider)
        self.assertEqual(len(buyback["buyback"]), 1)
        self.assertEqual(
            self.player.inventory.filter(definition=self.item_definition).count(),
            1,
        )

    def test_room_and_mob_providers_are_targeted_without_ordinary_mob_ambiguity(self):
        self.create_mob("a customer")

        room_payload = list_merchant_stock(self.player, None)
        self.assertEqual(room_payload["merchant"]["type"], "room")

        mob = self._merchant_mob()
        with self.assertRaises(ActionError) as error:
            list_merchant_stock(self.player, None)
        self.assertEqual(error.exception.code, "missing_target")

        with self.assertRaises(ActionError) as error:
            list_merchant_stock(self.player, "counter")
        self.assertEqual(error.exception.code, "ambiguous_merchant_provider")
        self.assertEqual(
            {provider["type"] for provider in error.exception.data["providers"]},
            {"mob", "room"},
        )

        room_payload = list_merchant_stock(self.player, self.room.key)
        mob_payload = list_merchant_stock(self.player, mob.key)
        self.assertEqual(room_payload["merchant"]["type"], "room")
        self.assertEqual(room_payload["merchant"]["id"], self.room.id)
        self.assertEqual(mob_payload["merchant"]["type"], "mob")
        self.assertEqual(mob_payload["merchant"]["id"], mob.id)

    def test_attachment_change_reconciles_stock_and_clear_deactivates_runtime(self):
        list_merchant_stock(self.player, None)
        runtime = MerchantRuntime.objects.get(
            world=self.spawn_world,
            room=self.room,
        )
        old_stock = runtime.stock_entries.filter(
            status=MerchantStockEntry.STATUS_AVAILABLE,
        ).first()

        replacement_item = ItemDefinition.objects.create(
            world=self.world,
            slug="silver-token",
            name="a silver token",
            keywords="silver token",
            item_type=adv_consts.ITEM_TYPE_INERT,
            cost=12,
            currency=self.currency,
        )
        replacement_profile = MerchantProfile.objects.create(
            world=self.world,
            slug="replacement-counter",
            name="The Replacement Counter",
            settlement_currency=self.currency,
        )
        MerchantStockSlot.objects.create(
            profile=replacement_profile,
            key="silver-tokens",
            item_definition=replacement_item,
        )
        self.room.merchant_profile = replacement_profile
        self.room.save(update_fields=["merchant_profile"])
        invalidate_room_merchant_runtimes(self.room)

        runtime.refresh_from_db()
        self.assertEqual(runtime.profile, replacement_profile)
        self.assertTrue(runtime.is_active)
        self.assertIsNone(runtime.last_restocked_ts)
        payload = list_merchant_stock(self.player, None)
        runtime.refresh_from_db()
        old_stock.refresh_from_db()
        self.assertEqual(old_stock.status, MerchantStockEntry.STATUS_RETIRED)
        self.assertEqual(len(payload["stock"]), 1)
        self.assertEqual(
            runtime.stock_entries.get(
                status=MerchantStockEntry.STATUS_AVAILABLE,
            ).item.definition,
            replacement_item,
        )

        self.room.merchant_profile = None
        self.room.save(update_fields=["merchant_profile"])
        invalidate_room_merchant_runtimes(self.room)
        runtime.refresh_from_db()
        self.assertFalse(runtime.is_active)
        with self.assertRaises(ActionError) as error:
            list_merchant_stock(self.player, None)
        self.assertEqual(error.exception.code, "target_not_found")

    def test_runtime_reloads_stale_attachment_without_locking_shared_room(self):
        replacement_profile = MerchantProfile.objects.create(
            world=self.world,
            slug="locked-counter",
            name="The Locked Counter",
            settlement_currency=self.currency,
        )
        MerchantStockSlot.objects.create(
            profile=replacement_profile,
            key="locked-tokens",
            item_definition=self.item_definition,
        )
        stale_room = Room.objects.select_related("merchant_profile").get(
            pk=self.room.pk,
        )
        Room.objects.filter(pk=self.room.pk).update(
            merchant_profile=replacement_profile,
        )

        runtime = create_or_update_room_merchant_runtime(
            stale_room,
            self.spawn_world,
        )

        self.assertEqual(runtime.profile, replacement_profile)
        stale_room = Room.objects.select_related("merchant_profile").get(
            pk=self.room.pk,
        )
        Room.objects.filter(pk=self.room.pk).update(merchant_profile=None)
        self.assertIsNone(
            create_or_update_room_merchant_runtime(
                stale_room,
                self.spawn_world,
            )
        )
        runtime.refresh_from_db()
        self.assertFalse(runtime.is_active)

    def test_null_snapshot_cannot_clobber_a_new_attachment(self):
        runtime = create_or_update_room_merchant_runtime(
            self.room,
            self.spawn_world,
        )
        self.room.merchant_profile = None
        self.room.save(update_fields=["merchant_profile"])
        stale_room = Room.objects.select_related("merchant_profile").get(
            pk=self.room.pk,
        )
        Room.objects.filter(pk=self.room.pk).update(
            merchant_profile=self.profile,
        )
        attached_room = Room.objects.select_related(
            "merchant_profile",
            "merchant_profile__settlement_currency",
        ).get(pk=self.room.pk)

        with patch(
            "spawns.merchants._room_with_merchant_profile",
            side_effect=[stale_room, attached_room, attached_room],
        ):
            resolved = create_or_update_room_merchant_runtime(
                stale_room,
                self.spawn_world,
            )

        self.assertEqual(resolved.id, runtime.id)
        resolved.refresh_from_db()
        self.assertTrue(resolved.is_active)
        self.assertEqual(resolved.profile, self.profile)

    def test_room_attachment_post_commit_catches_first_runtime_phantom(self):
        replacement_profile = MerchantProfile.objects.create(
            world=self.world,
            slug="post-commit-counter",
            name="The Post-commit Counter",
            settlement_currency=self.currency,
        )
        self.room.merchant_profile = replacement_profile
        self.room.save(update_fields=["merchant_profile"])

        with self.captureOnCommitCallbacks(execute=True):
            invalidate_room_merchant_runtimes(self.room)
            phantom = MerchantRuntime.objects.create(
                world=self.spawn_world,
                room=self.room,
                profile=self.profile,
                settlement_currency=self.currency,
                last_restocked_ts=timezone.now(),
            )

        phantom.refresh_from_db()
        self.assertEqual(phantom.profile, replacement_profile)
        self.assertIsNone(phantom.last_restocked_ts)
        self.assertTrue(phantom.is_active)

    def test_profile_edit_post_commit_catches_first_runtime_phantom(self):
        self.client.force_authenticate(self.user)
        manifest = """
kind: merchantprofile
metadata:
  slug: room-counter
spec:
  settlement_currency: obol
  pricing:
    sell_markup: 1.25
"""

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("builder-world-manifest-apply", args=[self.world.pk]),
                {"manifest": manifest},
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.data)
            phantom = MerchantRuntime.objects.create(
                world=self.spawn_world,
                room=self.room,
                profile=self.profile,
                settlement_currency=self.currency,
                last_restocked_ts=timezone.now(),
            )

        phantom.refresh_from_db()
        self.assertIsNone(phantom.last_restocked_ts)

    def test_instance_room_uses_base_world_profile_and_isolates_runtime(self):
        instance_template = World.objects.new_world(
            name="Merchant Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
        )
        instance_room = instance_template.zones.get().rooms.get()
        instance_room.merchant_profile = self.profile
        instance_room.save(update_fields=["merchant_profile"])
        instance_world = instance_template.create_spawn_world()
        instance_player = Player.objects.create(
            name="Instance Shopper",
            room=instance_room,
            user=self.user,
            world=instance_world,
        )

        payload = list_merchant_stock(instance_player, None)

        runtime = MerchantRuntime.objects.get(
            world=instance_world,
            room=instance_room,
        )
        self.assertEqual(runtime.profile, self.profile)
        self.assertEqual(payload["merchant"]["type"], "room")
        self.assertFalse(
            MerchantRuntime.objects.filter(
                world=self.spawn_world,
                room=instance_room,
            ).exists()
        )

    def test_one_authored_room_has_independent_parallel_runtime_shops(self):
        first_payload = list_merchant_stock(self.player, None)
        parallel_world = self.world.create_spawn_world(
            instance_ref="parallel-room-merchant",
        )
        parallel_player = Player.objects.create(
            name="Parallel Shopper",
            room=self.room,
            user=self.user,
            world=parallel_world,
        )

        second_payload = list_merchant_stock(parallel_player, None)

        runtimes = MerchantRuntime.objects.filter(room=self.room).order_by("world_id")
        self.assertEqual(runtimes.count(), 2)
        self.assertNotEqual(runtimes[0].world_id, runtimes[1].world_id)
        self.assertNotEqual(
            first_payload["stock"][0]["id"],
            second_payload["stock"][0]["id"],
        )
        self.assertTrue(
            all(
                entry.item.world_id == entry.runtime.world_id
                for entry in MerchantStockEntry.objects.filter(
                    runtime__room=self.room,
                    status=MerchantStockEntry.STATUS_AVAILABLE,
                ).select_related("item", "runtime")
            )
        )


class TestConcurrentRoomMerchantRuntime(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("concurrent@example.com", "p")
        self.world = World.objects.new_world(
            name="Concurrent Merchant World",
            author=self.user,
            config=WorldConfig.objects.create(),
        )
        apply_basic_stat_system(self.world)
        self.spawn_world = self.world.create_spawn_world()
        self.room = self.world.zones.get().rooms.get()
        self.currency = create_currency(
            world=self.world,
            code="coin",
            name="Coin",
            plural_name="Coins",
        )
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="concurrent-token",
            name="a concurrent token",
            keywords="concurrent token",
            item_type=adv_consts.ITEM_TYPE_INERT,
            cost=1,
            currency=self.currency,
        )
        profile = MerchantProfile.objects.create(
            world=self.world,
            slug="concurrent-counter",
            name="The Concurrent Counter",
            settlement_currency=self.currency,
        )
        MerchantStockSlot.objects.create(
            profile=profile,
            key="tokens",
            item_definition=item_definition,
            count=2,
        )
        self.room.merchant_profile = profile
        self.room.save(update_fields=["merchant_profile"])

    def test_concurrent_first_access_creates_one_runtime_and_one_stock_generation(self):
        barrier = Barrier(2)

        def first_access():
            close_old_connections()
            try:
                room = Room.objects.select_related(
                    "merchant_profile",
                    "merchant_profile__settlement_currency",
                ).get(pk=self.room.pk)
                world = World.objects.select_related(
                    "context",
                    "context__instance_of",
                ).get(pk=self.spawn_world.pk)
                barrier.wait()
                runtime = create_or_update_room_merchant_runtime(room, world)
                return runtime.pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            runtime_ids = list(executor.map(lambda _index: first_access(), range(2)))

        self.assertEqual(runtime_ids[0], runtime_ids[1])
        runtime = MerchantRuntime.objects.get(
            world=self.spawn_world,
            room=self.room,
        )
        self.assertEqual(
            runtime.stock_entries.filter(
                status=MerchantStockEntry.STATUS_AVAILABLE,
            ).count(),
            2,
        )
