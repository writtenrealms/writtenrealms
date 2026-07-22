from builders.currencies import create_currency
from spawns.models import Item, PlayerCurrencyBalance
from spawns.schemas import (
    Actor,
    Item as ItemSchema,
    World as WorldSchema,
    build_mock_state_sync,
)
from spawns.serializers import (
    AnimateItemSerializer,
    AnimatePlayerSerializer,
    AnimateWorldSerializer,
    player_economy_payload,
)
from spawns.state_payloads import (
    build_state_sync,
    get_player_with_related,
    serialize_actor,
    serialize_item,
    serialize_world,
)
from tests.base import WorldTestCase


class CurrencyPayloadTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.obol = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
            description="A stamped bronze coin.",
        )
        self.world.refresh_from_db()

    def test_world_payload_is_an_obol_only_code_keyed_catalog(self):
        drf_payload = dict(AnimateWorldSerializer(self.spawn_world).data)
        state_payload = serialize_world(self.spawn_world)
        expected_economy = {
            "revision": self.world.economy_revision,
            "default_currency": "obol",
            "currencies": {
                "obol": {
                    "name": "Obol",
                    "plural_name": "Obols",
                    "description": "A stamped bronze coin.",
                },
            },
        }

        for payload in (drf_payload, state_payload):
            self.assertEqual(payload["economy"], expected_economy)
            validated = WorldSchema.model_validate(payload).model_dump()
            self.assertNotIn("currencies", validated)
            self.assertEqual(validated["economy"], expected_economy)

    def test_actor_payload_includes_default_zero_without_legacy_money_fields(self):
        self.player.wallet_revision = 3
        self.player.save(update_fields=["wallet_revision", "modified_ts"])
        player = get_player_with_related(self.player.id)

        with self.assertNumQueries(0):
            economy = player_economy_payload(player)

        self.assertEqual(
            economy,
            {"wallet_revision": 3, "balances": {"obol": 0}},
        )

        drf_payload = dict(AnimatePlayerSerializer(player).data)
        actor_payload = serialize_actor(player, player.room).model_dump()
        for payload in (drf_payload, actor_payload):
            self.assertEqual(payload["economy"], economy)
            self.assertTrue(
                {"gold", "medals", "currencies"}.isdisjoint(payload),
            )
        Actor.model_validate(actor_payload)

    def test_state_sync_carries_the_authoritative_obol_balance(self):
        PlayerCurrencyBalance.objects.create(
            player=self.player,
            currency=self.obol,
            amount=17,
        )
        self.player.wallet_revision = 4
        self.player.save(update_fields=["wallet_revision", "modified_ts"])

        state = build_state_sync(
            get_player_with_related(self.player.id),
        ).model_dump()

        self.assertEqual(
            state["actor"]["economy"],
            {"wallet_revision": 4, "balances": {"obol": 17}},
        )
        self.assertEqual(
            state["world"]["economy"]["default_currency"],
            "obol",
        )
        self.assertEqual(
            set(state["world"]["economy"]["currencies"]),
            {"obol"},
        )

    def test_item_payload_has_explicit_money_or_no_value(self):
        priced_item = Item.objects.create(
            name="a bronze spear",
            world=self.spawn_world,
            cost=12,
            currency=self.obol,
        )
        unpriced_item = Item.objects.create(
            name="a smooth stone",
            world=self.spawn_world,
        )

        expected_value = {
            "amount": 12,
            "currency": "obol",
            "display": "12 Obols",
        }
        drf_priced = dict(AnimateItemSerializer(priced_item).data)
        self.assertEqual(drf_priced["value"], expected_value)
        self.assertNotIn("cost", drf_priced)
        self.assertNotIn("currency", drf_priced)
        ItemSchema.model_validate(drf_priced)

        state_priced = serialize_item(priced_item).model_dump()
        self.assertEqual(state_priced["value"], expected_value)
        self.assertNotIn("cost", state_priced)
        self.assertNotIn("currency", state_priced)

        drf_unpriced = dict(AnimateItemSerializer(unpriced_item).data)
        self.assertIsNone(drf_unpriced["value"])
        self.assertIsNone(serialize_item(unpriced_item).value)

    def test_mock_payload_uses_the_minimal_obol_economy(self):
        payload = build_mock_state_sync().model_dump()

        self.assertEqual(
            payload["actor"]["economy"]["balances"],
            {"obol": 347},
        )
        self.assertEqual(
            set(payload["world"]["economy"]["currencies"]),
            {"obol"},
        )
        self.assertTrue(
            {"gold", "medals", "currencies"}.isdisjoint(payload["actor"]),
        )
