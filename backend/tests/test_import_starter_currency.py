from django.contrib.auth import get_user_model
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from builders.currencies import create_currency, set_starting_balance
from builders.instance_templates import create_instance_template
from builders.models import Currency, ItemDefinition, WorldBuilder
from builders.world_export import manifest_stream_to_yaml, serialize_world_export_payload
from config import constants as adv_consts
from spawns.models import Player, PlayerCurrencyBalance
from worlds.models import World


class ImportStarterCurrencyTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "starter-currency@example.com", "p",
        )
        self.client.force_authenticate(self.user)
        self.source = World.objects.new_world(
            name="Phalanx", author=self.user, is_multiplayer=True,
        )
        self.obol = create_currency(
            world=self.source, code="obol", name="Obol", plural_name="Obols",
        )
        self.source.config.death_currency = self.obol
        self.source.config.clan_registration_currency = self.obol
        self.source.config.save(update_fields=[
            "death_currency", "clan_registration_currency",
        ])
        room = self.source.config.starting_room
        room.name = "Phalanx Gates"
        room.save(update_fields=["name"])

    def _new_target(self, **overrides):
        response = self.client.post(
            reverse("builder-world-list"),
            {"name": "Import Target", "is_multiplayer": True, **overrides},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return World.objects.get(pk=response.data["id"])

    def _apply(self, target, documents=None):
        if documents is None:
            documents = serialize_world_export_payload(self.source)["documents"]
        return self.client.post(
            reverse("builder-world-manifest-apply", args=[target.pk]),
            {"manifest": manifest_stream_to_yaml(documents)},
            format="json",
        )

    def test_complete_import_replaces_default_gold_and_reapplies_unchanged(self):
        # Cover both the API default plural and the UI's empty plural value.
        for plural in ("Gold", ""):
            with self.subTest(plural=plural):
                target = self._new_target(initial_currency_plural_name=plural)
                gold_id = target.default_currency_id
                builder = Player.objects.get(world__context=target, is_builder=True)
                # Empty wallet rows are also safe scaffold, not real balances.
                PlayerCurrencyBalance.objects.create(
                    player=builder, currency_id=gold_id, amount=0,
                )
                source_documents = serialize_world_export_payload(self.source)["documents"]
                for _ in range(2):
                    response = self._apply(target, source_documents)
                    self.assertEqual(response.status_code, 200, response.data)
                    target.refresh_from_db()
                    self.assertFalse(Currency.objects.filter(pk=gold_id).exists())
                    self.assertEqual(target.default_currency.code, "obol")
                    self.assertEqual(target.config.death_currency.code, "obol")
                    self.assertEqual(target.config.clan_registration_currency.code, "obol")
                    self.assertEqual(
                        serialize_world_export_payload(target)["documents"],
                        source_documents,
                    )
                self.assertFalse(
                    PlayerCurrencyBalance.objects.filter(currency_id=gold_id).exists()
                )
                self.assertTrue(Player.objects.filter(pk=builder.pk).exists())

    def test_bundle_cleans_currency_before_cloning_instance_configs(self):
        create_instance_template(
            base_world=self.source, author=self.user, name="Outpost",
            instance_slug="outpost",
        )
        target = self._new_target()
        gold_id = target.default_currency_id
        documents = serialize_world_export_payload(self.source)["documents"]
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 200, response.data)
        target.refresh_from_db()
        self.assertFalse(Currency.objects.filter(pk=gold_id).exists())
        instance = World.objects.get(instance_of=target, instance_slug="outpost")
        self.assertEqual(instance.config.death_currency.code, "obol")
        self.assertEqual(serialize_world_export_payload(target)["documents"], documents)

    def test_late_failure_restores_starter_currency_and_references(self):
        for bundle in (False, True):
            with self.subTest(bundle=bundle):
                if bundle:
                    create_instance_template(
                        base_world=self.source, author=self.user, name="Outpost",
                        instance_slug="outpost",
                    )
                target = self._new_target()
                gold_id = target.default_currency_id
                before = serialize_world_export_payload(target)["documents"]
                documents = serialize_world_export_payload(self.source)["documents"]
                documents[-1]["spec"]["unknown_config_field"] = True
                response = self._apply(target, documents)
                self.assertEqual(response.status_code, 400, response.data)
                target.refresh_from_db()
                self.assertEqual(target.default_currency_id, gold_id)
                self.assertEqual(target.config.death_currency_id, gold_id)
                self.assertEqual(target.config.clan_registration_currency_id, gold_id)
                self.assertEqual(serialize_world_export_payload(target)["documents"], before)
                self.assertFalse(World.objects.filter(instance_of=target).exists())

    def test_partial_manifest_keeps_default_gold(self):
        target = self._new_target()
        gold_id = target.default_currency_id
        documents = [
            {"kind": "currency", "metadata": {"code": "obol"}, "spec": {"name": "Obol"}},
            {"kind": "world", "spec": {"default_currency": "obol"}},
        ]
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(Currency.objects.filter(pk=gold_id).exists())

    def test_currency_and_full_config_without_room_documents_keep_gold(self):
        target = self._new_target()
        gold_id = target.default_currency_id
        documents = [
            doc for doc in serialize_world_export_payload(self.source)["documents"]
            if doc["kind"] in {"currency", "world"}
        ]
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(Currency.objects.filter(pk=gold_id).exists())

    def test_invalid_currency_reference_rolls_back_without_server_error(self):
        target = self._new_target()
        gold_id = target.default_currency_id
        documents = serialize_world_export_payload(self.source)["documents"]
        documents[-1]["spec"]["default_currency"] = {"invalid": "obol"}
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(Currency.objects.filter(pk=gold_id).exists())

    def test_import_with_gold_in_catalog_keeps_existing_identity(self):
        create_currency(world=self.source, code="gold", name="Golden coin")
        target = self._new_target()
        gold_id = target.default_currency_id
        response = self._apply(target)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Currency.objects.get(pk=gold_id).name, "Golden coin")

    def test_customized_or_used_starter_currency_is_preserved(self):
        for customization in (
            "description", "name", "code", "room", "wallet",
            "starting_balance", "item", "visitor", "second_currency",
        ):
            with self.subTest(customization=customization):
                target = self._new_target(**(
                    {"initial_currency_code": "silver"}
                    if customization == "code" else {}
                ))
                currency = target.default_currency
                if customization in {"description", "name"}:
                    setattr(currency, customization, "Authored currency")
                    currency.save(update_fields=[customization])
                elif customization == "room":
                    room = target.config.starting_room
                    room.note = "Authored room"
                    room.save(update_fields=["note"])
                elif customization == "wallet":
                    builder = Player.objects.get(world__context=target, is_builder=True)
                    PlayerCurrencyBalance.objects.create(
                        player=builder, currency=currency, amount=7,
                    )
                elif customization == "starting_balance":
                    set_starting_balance(currency=currency, amount=5)
                elif customization == "item":
                    ItemDefinition.objects.create(
                        world=target, name="Gold-priced item", cost=1, currency=currency,
                    )
                elif customization == "visitor":
                    Player.objects.create(
                        world=target.spawned_worlds.get(), user=self.user,
                        name="Visitor", room=target.config.starting_room,
                    )
                elif customization == "second_currency":
                    create_currency(world=target, code="silver", name="Silver")
                response = self._apply(target)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertTrue(Currency.objects.filter(pk=currency.pk).exists())
                if customization == "wallet":
                    self.assertEqual(
                        PlayerCurrencyBalance.objects.get(player=builder, currency=currency).amount,
                        7,
                    )

    def test_rank_two_import_cannot_remove_currency(self):
        target = self._new_target()
        gold_id = target.default_currency_id
        other_user = get_user_model().objects.create_user("junior@example.com", "p")
        WorldBuilder.objects.create(world=target, user=other_user, builder_rank=2)
        self.client.force_authenticate(other_user)
        response = self._apply(target)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(Currency.objects.filter(pk=gold_id).exists())

    def test_running_world_still_requires_stopping_before_currency_changes(self):
        target = self._new_target()
        gold_id = target.default_currency_id
        target.spawned_worlds.update(lifecycle=adv_consts.WORLD_LIFECYCLE_RUNNING)
        response = self._apply(target)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Stop active", str(response.data))
        self.assertTrue(Currency.objects.filter(pk=gold_id).exists())
