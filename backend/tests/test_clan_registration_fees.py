from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import serializers
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from builders.currencies import create_currency
from builders.instance_templates import create_instance_template
from builders.world_export import manifest_stream_to_yaml, serialize_world_export_payload
from spawns.models import Clan, Player, PlayerCurrencyBalance
from spawns.wallet import balance_map
from system.serializers import ClanRegisterDeserializer
from worlds.models import World


class ClanRegistrationFeeTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("clan-fees@example.com", "p")
        self.client.force_authenticate(self.user)
        self.base = World.objects.new_world(
            name="Phalanx", author=self.user, is_multiplayer=True,
        )
        self.obol = create_currency(
            world=self.base, code="obol", name="Obol", plural_name="Obols",
        )
        self.drachma = create_currency(
            world=self.base, code="drachma", name="Drachma", plural_name="Drachmas",
        )
        self._set_fee(self.base, self.obol, 1000)
        self.instance = create_instance_template(
            base_world=self.base, author=self.user, name="Outpost",
            instance_slug="outpost",
        )

    def _set_fee(self, world, currency, cost):
        world.config.clan_registration_currency = currency
        world.config.clan_registration_cost = cost
        world.config.save(update_fields=[
            "clan_registration_currency", "clan_registration_cost",
        ])

    def _player(self, template):
        runtime = template.spawned_worlds.filter(is_multiplayer=True).first()
        if runtime is None:
            runtime = template.create_spawn_world()
        return Player.objects.create(
            user=self.user, name="Clan Founder", world=runtime,
            room=template.config.starting_room,
        )

    def _registration(self, player, name="The Phalanx"):
        return ClanRegisterDeserializer(data={"player": player.pk, "clan": name})

    def _assert_paid_registration(self, template, currency, cost, other_currency):
        player = self._player(template)
        # A large balance in the wrong currency cannot pay the fee.
        PlayerCurrencyBalance.objects.create(
            player=player, currency=other_currency, amount=10000,
        )
        registration = self._registration(player)
        self.assertFalse(registration.is_valid())
        self.assertIn(
            f"Registering a clan costs {cost} {currency.plural_name}.",
            str(registration.errors),
        )
        self.assertFalse(player.clan_memberships.exists())

        PlayerCurrencyBalance.objects.create(
            player=player, currency=currency, amount=cost + 7,
        )
        registration = self._registration(player)
        with CaptureQueriesContext(connection) as queries:
            self.assertTrue(registration.is_valid(), registration.errors)
        self.assertLessEqual(len(queries), 6)
        clan = registration.save()
        self.assertEqual(player.clan_memberships.get().clan_id, clan.pk)
        self.assertEqual(balance_map(player)[currency.code], 7)
        self.assertEqual(balance_map(player)[other_currency.code], 10000)
        return player, clan

    def test_family_import_and_reimport_preserve_base_and_instance_fees(self):
        documents = serialize_world_export_payload(self.base)["documents"]
        response = self.client.post(
            reverse("builder-world-list"),
            {"name": "Import Target", "is_multiplayer": True}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        target = World.objects.get(pk=response.data["id"])
        self.assertNotEqual(target.pk, self.base.pk)
        for _ in range(2):
            response = self.client.post(
                reverse("builder-world-manifest-apply", args=[target.pk]),
                {"manifest": manifest_stream_to_yaml(documents)}, format="json",
            )
            self.assertEqual(response.status_code, 200, response.data)
        imported_instance = World.objects.get(instance_of=target, instance_slug="outpost")
        for base, template in (
            (self.base, self.base), (self.base, self.instance),
            (target, target), (target, imported_instance),
        ):
            with self.subTest(world=template.pk):
                self._assert_paid_registration(
                    template, base.currencies.get(code="obol"), 1000,
                    base.currencies.get(code="drachma"),
                )

    def test_existing_instance_uses_updated_base_fee_and_currency(self):
        self._set_fee(self.instance, self.obol, 1000)
        self.instance.create_spawn_world()
        self._set_fee(self.base, self.drachma, 200)
        player, clan = self._assert_paid_registration(
            self.instance, self.drachma, 200, self.obol,
        )

        # Renaming a clan uses the same policy and the same wallet debit.
        PlayerCurrencyBalance.objects.filter(player=player, currency=self.drachma).update(amount=200)
        registration = self._registration(player, "Renamed Phalanx")
        self.assertTrue(registration.is_valid(), registration.errors)
        self.assertEqual(registration.save().pk, clan.pk)
        clan.refresh_from_db()
        self.assertEqual(clan.name, "Renamed Phalanx")
        self.assertEqual(balance_map(player)["drachma"], 0)

    def test_free_base_policy_ignores_stale_instance_fee(self):
        self._set_fee(self.instance, self.obol, 1000)
        self._set_fee(self.base, None, 0)
        player = self._player(self.instance)
        registration = self._registration(player)
        self.assertTrue(registration.is_valid(), registration.errors)
        registration.save()
        self.assertFalse(player.currency_balances.exists())

    def test_missing_base_currency_does_not_fall_back_to_instance_policy(self):
        self._set_fee(self.instance, self.obol, 1000)
        self._set_fee(self.base, None, 1000)
        registration = self._registration(self._player(self.instance))
        self.assertFalse(registration.is_valid())
        self.assertIn("Clan registration currency is not configured.", str(registration.errors))

    def test_wallet_is_rechecked_before_creating_clan(self):
        player = self._player(self.instance)
        balance = PlayerCurrencyBalance.objects.create(
            player=player, currency=self.obol, amount=1000,
        )
        registration = self._registration(player)
        self.assertTrue(registration.is_valid(), registration.errors)
        balance.amount = 999
        balance.save(update_fields=["amount"])
        with self.assertRaises(serializers.ValidationError):
            registration.save()
        self.assertFalse(Clan.objects.exists())
        self.assertFalse(player.clan_memberships.exists())
        balance.refresh_from_db()
        self.assertEqual(balance.amount, 999)

    def test_new_instance_does_not_snapshot_base_clan_policy(self):
        self.assertEqual(self.instance.config.clan_registration_cost, 0)
        self.assertIsNone(self.instance.config.clan_registration_currency_id)
