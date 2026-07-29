from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.reverse import reverse
from rest_framework.test import APIClient

from builders.currencies import (
    _trigger_snapshot_currency_references,
    create_currency,
    currency_usage_map,
    delete_currency,
    replace_starting_balances,
    select_default_currency,
)
from builders.models import (
    CraftingRecipe,
    Currency,
    ItemDefinition,
    MobDefinition,
    Trigger,
    WorldBuilder,
    WorldStartingCurrencyBalance,
)
from config import constants as adv_consts
from core.economy import MAX_CURRENCY_AMOUNT, economy_world
from spawns.models import (
    Player,
    PlayerCurrencyBalance,
    ScheduledTriggerRun,
)
from spawns.wallet import WalletError, balance_map, mutate_balances
from quests.models import QuestTemplate
from worlds.models import World


User = get_user_model()


class CurrencyTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("currency-builder@example.com", "p")
        self.world = World.objects.new_world(
            name="Currency World",
            author=self.user,
        )
        self.spawn_world = self.world.create_spawn_world()

    def create_player(self, *, name="Wallet Tester", world=None):
        world = world or self.spawn_world
        return Player.objects.create(
            name=name,
            user=self.user,
            world=world,
            room=world.effective_config.starting_room,
        )


class EconomyOwnershipTests(CurrencyTestCase):
    def test_base_spawn_instance_template_and_instance_run_share_base_economy(self):
        instance_template = World.objects.new_world(
            name="Currency Instance",
            author=self.user,
            instance_of=self.world,
        )
        instance_run = instance_template.create_spawn_world()

        self.assertEqual(economy_world(self.world), self.world)
        self.assertEqual(economy_world(self.spawn_world), self.world)
        self.assertEqual(economy_world(instance_template), self.world)
        self.assertEqual(economy_world(instance_run), self.world)


class CurrencyAuthoringTests(CurrencyTestCase):
    def test_first_currency_is_normalized_and_becomes_default(self):
        obol = create_currency(
            world=self.world,
            code=" Obol ",
            name=" Obol ",
            plural_name=" Obols ",
        )

        self.world.refresh_from_db()
        self.assertEqual(obol.code, "obol")
        self.assertEqual(obol.name, "Obol")
        self.assertEqual(obol.plural_name, "Obols")
        self.assertEqual(self.world.default_currency, obol)

    def test_currency_code_validation_rejects_invalid_codes(self):
        for code in ("", "9obol", "obol.coin", "obol coin"):
            with self.subTest(code=code):
                with self.assertRaises(ValidationError):
                    create_currency(world=self.world, code=code, name="Obol")

    def test_currency_codes_cannot_collide_by_case(self):
        create_currency(world=self.world, code="obol", name="Obol")

        with self.assertRaises(ValidationError):
            create_currency(world=self.world, code="OBOL", name="Other Obol")

        self.assertEqual(
            list(self.world.currencies.values_list("code", flat=True)),
            ["obol"],
        )

    def test_currency_edits_are_blocked_while_a_runtime_is_transitioning(self):
        self.spawn_world.lifecycle = adv_consts.WORLD_LIFECYCLE_STARTING
        self.spawn_world.save(update_fields=["lifecycle", "modified_ts"])

        with self.assertRaisesMessage(ValidationError, "transitioning worlds"):
            create_currency(world=self.world, code="obol", name="Obol")

        self.assertFalse(self.world.currencies.exists())

    def test_persisted_currency_code_is_immutable(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")

        obol.code = "drachma"
        with self.assertRaises(ValidationError):
            obol.save()

        obol.refresh_from_db()
        self.assertEqual(obol.code, "obol")

    def test_world_has_one_explicit_default_currency_pointer(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
        )

        self.world.refresh_from_db()
        self.assertEqual(self.world.default_currency, obol)

        select_default_currency(world=self.world, currency=drachma)

        self.world.refresh_from_db()
        self.assertEqual(self.world.default_currency, drachma)
        self.assertFalse(obol.default_for_worlds.exists())
        self.assertEqual(list(drachma.default_for_worlds.all()), [self.world])

    def test_replacing_starting_balances_is_exact_and_omits_zeroes(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
        )
        replace_starting_balances(
            world=self.world,
            balances={obol: 25, drachma: 3},
        )
        self.world.refresh_from_db()
        revision_before_replace = self.world.economy_revision

        replace_starting_balances(
            world=self.world,
            balances={"obol": 7, "drachma": 0},
        )

        self.world.refresh_from_db()
        self.assertEqual(
            list(
                WorldStartingCurrencyBalance.objects.filter(world=self.world)
                .values_list("currency__code", "amount")
            ),
            [("obol", 7)],
        )
        self.assertEqual(
            self.world.economy_revision,
            revision_before_replace + 1,
        )

    def test_delete_blocks_structured_quest_and_trigger_references(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        drachma = create_currency(world=self.world, code="drachma", name="Drachma")
        select_default_currency(world=self.world, currency=drachma)
        QuestTemplate.objects.create(
            world=self.world,
            slug="obol-reward",
            name="Obol Reward",
            reward_policy={
                "completion": [
                    {"type": "grant_currency", "currency": "obol", "amount": 1},
                ],
            },
        )
        Trigger.objects.create(
            world=self.world,
            scope="world",
            kind="command",
            conditions='{"gte": ["actor.balances.obol", 1]}',
        )

        with self.assertRaisesMessage(
            ValidationError,
            "1 quest template, 1 trigger",
        ):
            delete_currency(obol)

        self.assertTrue(Currency.objects.filter(pk=obol.pk).exists())

    def test_delete_blocks_structured_references_in_instance_templates(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        drachma = create_currency(world=self.world, code="drachma", name="Drachma")
        select_default_currency(world=self.world, currency=drachma)
        instance = World.objects.new_world(
            name="Currency Instance",
            author=self.user,
            instance_of=self.world,
        )
        Trigger.objects.create(
            world=instance,
            scope="world",
            kind="command",
            conditions='{"gte": ["actor.balances.obol", 1]}',
        )

        with self.assertRaisesMessage(ValidationError, "1 trigger"):
            delete_currency(obol)

        self.assertTrue(Currency.objects.filter(pk=obol.pk).exists())

    def test_delete_blocks_trigger_step_currency_debit_reference(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
        )
        select_default_currency(world=self.world, currency=drachma)
        Trigger.objects.create(
            world=self.world,
            scope="world",
            kind="command",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )

        with self.assertRaisesMessage(ValidationError, "1 trigger"):
            delete_currency(obol)

        self.assertTrue(Currency.objects.filter(pk=obol.pk).exists())

    def test_delete_blocks_instance_trigger_step_currency_debit_reference(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
        )
        select_default_currency(world=self.world, currency=drachma)
        instance = World.objects.new_world(
            name="Currency Toll Instance",
            author=self.user,
            instance_of=self.world,
        )
        Trigger.objects.create(
            world=instance,
            scope="world",
            kind="command",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )

        with self.assertRaisesMessage(ValidationError, "1 trigger"):
            delete_currency(obol)

        self.assertTrue(Currency.objects.filter(pk=obol.pk).exists())

    def test_delete_blocks_active_trigger_currency_debit_snapshot(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
        )
        select_default_currency(world=self.world, currency=drachma)
        trigger = Trigger.objects.create(
            world=self.world,
            scope="world",
            kind="command",
        )
        player = self.create_player()
        now = timezone.now()
        run = ScheduledTriggerRun.objects.create(
            trigger=trigger,
            runtime_world=self.spawn_world,
            room=player.room,
            actor_type="player",
            actor_id=player.id,
            actor_key=player.key,
            steps=[
                {
                    "after_seconds": 5,
                    "due_after_seconds": 5,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "currency_id": obol.id,
                            "amount": 10,
                        },
                    ],
                },
            ],
            next_run_ts=now,
            started_ts=now,
            status=ScheduledTriggerRun.STATUS_ACTIVE,
        )
        trigger.delete()
        run.refresh_from_db()
        self.assertIsNone(run.trigger_id)

        with self.assertRaisesMessage(
            ValidationError,
            "1 active trigger sequence",
        ):
            delete_currency(obol)

        self.assertTrue(Currency.objects.filter(pk=obol.pk).exists())

    def test_active_trigger_snapshot_scan_is_deferred_and_single_pass(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
        )
        player = self.create_player()
        now = timezone.now()
        ScheduledTriggerRun.objects.create(
            runtime_world=self.spawn_world,
            room=player.room,
            actor_type="player",
            actor_id=player.id,
            actor_key=player.key,
            steps=[
                {
                    "after_seconds": 5,
                    "due_after_seconds": 5,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "currency_id": obol.id,
                            "amount": 10,
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "drachma",
                            "currency_id": drachma.id,
                            "amount": 2,
                        },
                    ],
                },
            ],
            next_run_ts=now,
            started_ts=now,
            status=ScheduledTriggerRun.STATUS_ACTIVE,
        )

        with patch(
            "builders.currencies._trigger_snapshot_currency_references",
            wraps=_trigger_snapshot_currency_references,
        ) as collect_references:
            catalog_usages = currency_usage_map(
                world=self.world,
                currencies=[obol, drachma],
            )

        self.assertEqual(collect_references.call_count, 0)
        for currency in (obol, drachma):
            self.assertNotIn(
                {"type": "active trigger sequence", "count": 1},
                catalog_usages[currency.id],
            )

        with patch(
            "builders.currencies._trigger_snapshot_currency_references",
            wraps=_trigger_snapshot_currency_references,
        ) as collect_references:
            deletion_usages = currency_usage_map(
                world=self.world,
                currencies=[obol, drachma],
                include_active_trigger_sequences=True,
            )

        self.assertEqual(collect_references.call_count, 1)
        for currency in (obol, drachma):
            self.assertIn(
                {"type": "active trigger sequence", "count": 1},
                deletion_usages[currency.id],
            )

    def test_delete_blocks_crafting_recipe_cost_reference(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        drachma = create_currency(world=self.world, code="drachma", name="Drachma")
        select_default_currency(world=self.world, currency=drachma)
        output = ItemDefinition.objects.create(
            world=self.world,
            slug="priced-craft-output",
            name="a priced craft output",
            item_type=adv_consts.ITEM_TYPE_INERT,
        )
        CraftingRecipe.objects.create(
            world=self.world,
            slug="priced-craft",
            output_item_definition=output,
            cost=90,
            currency=obol,
        )

        with self.assertRaisesMessage(ValidationError, "1 crafting recipe cost"):
            delete_currency(obol)

        self.assertTrue(Currency.objects.filter(pk=obol.pk).exists())


class CurrencyBuilderEndpointTests(CurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.list_endpoint = reverse(
            "builder-currency-list",
            args=[self.world.pk],
        )

    def test_builder_can_author_starting_amount_and_select_a_new_default(self):
        response = self.client.post(
            self.list_endpoint,
            {
                "code": "obol",
                "name": "Obol",
                "plural_name": "Obols",
                "description": "A small silver coin.",
                "starting_amount": 12,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["is_default"])
        self.assertEqual(response.data["starting_amount"], 12)
        obol = Currency.objects.get(world=self.world, code="obol")

        response = self.client.post(
            self.list_endpoint,
            {
                "code": "drachma",
                "name": "Drachma",
                "plural_name": "Drachmas",
                "starting_amount": 0,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        drachma = Currency.objects.get(world=self.world, code="drachma")

        response = self.client.post(
            reverse(
                "builder-currency-make-default",
                args=[self.world.pk, drachma.pk],
            ),
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["is_default"])
        self.world.refresh_from_db()
        self.assertEqual(self.world.default_currency, drachma)

        response = self.client.put(
            reverse(
                "builder-currency-details",
                args=[self.world.pk, obol.pk],
            ),
            {
                "code": "obol",
                "name": "Obol",
                "plural_name": "Obols",
                "description": "The common Phalanx coin.",
                "starting_amount": 7,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["starting_amount"], 7)
        self.assertEqual(
            WorldStartingCurrencyBalance.objects.get(
                world=self.world,
                currency=obol,
            ).amount,
            7,
        )

    def test_create_rolls_back_currency_if_starting_balance_fails(self):
        with patch(
            "builders.currencies.set_starting_balance",
            side_effect=ValidationError("simulated starting-balance failure"),
        ):
            response = self.client.post(
                self.list_endpoint,
                {
                    "code": "obol",
                    "name": "Obol",
                    "starting_amount": 12,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(
            Currency.objects.filter(world=self.world, code="obol").exists()
        )
        self.world.refresh_from_db()
        self.assertIsNone(self.world.default_currency_id)

    def test_update_rolls_back_metadata_if_starting_balance_fails(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        self.world.refresh_from_db()
        revision_before = self.world.economy_revision

        with patch(
            "builders.currencies.set_starting_balance",
            side_effect=ValidationError("simulated starting-balance failure"),
        ):
            response = self.client.put(
                reverse(
                    "builder-currency-details",
                    args=[self.world.pk, obol.pk],
                ),
                {
                    "code": "obol",
                    "name": "Renamed Obol",
                    "starting_amount": 7,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400, response.data)
        obol.refresh_from_db()
        self.world.refresh_from_db()
        self.assertEqual(obol.name, "Obol")
        self.assertEqual(self.world.economy_revision, revision_before)
        self.assertFalse(
            WorldStartingCurrencyBalance.objects.filter(
                world=self.world,
                currency=obol,
            ).exists()
        )

    def test_rank_two_builder_can_read_but_cannot_mutate_currencies(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        rank_two = User.objects.create_user("rank-two-currency@example.com", "p")
        WorldBuilder.objects.create(
            world=self.world,
            user=rank_two,
            builder_rank=2,
        )
        self.client.force_authenticate(rank_two)

        response = self.client.get(self.list_endpoint)
        self.assertEqual(response.status_code, 200, response.data)

        response = self.client.put(
            reverse(
                "builder-currency-details",
                args=[self.world.pk, obol.pk],
            ),
            {"code": "obol", "name": "Changed"},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)

        response = self.client.post(
            reverse("builder-world-manifest-apply", args=[self.world.pk]),
            {
                "manifest": """
kind: currency
metadata:
  code: drachma
spec:
  name: Drachma
""",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(self.world.currencies.filter(code="drachma").exists())

    def test_instance_currency_route_cannot_mutate_base_catalog(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        instance = World.objects.new_world(
            name="Currency Instance",
            author=self.user,
            instance_of=self.world,
        )

        response = self.client.put(
            reverse(
                "builder-currency-details",
                args=[instance.pk, obol.pk],
            ),
            {"code": "obol", "name": "Changed"},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        obol.refresh_from_db()
        self.assertEqual(obol.name, "Obol")

    def test_mob_currency_reward_manifest_syncs_runtime_snapshot_once(self):
        obol = create_currency(world=self.world, code="obol", name="Obol")
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="guard",
            name="a guard",
        )
        mob = definition.spawn(
            self.spawn_world.effective_config.starting_room,
            self.spawn_world,
        )
        manifest = """
kind: mobdefinition
metadata:
  slug: guard
spec:
  rewards:
    currencies:
      obol: 7
"""

        from builders.mob_definitions import sync_spawned_mobs_from_definition

        with patch(
            "builders.mob_definitions.sync_spawned_mobs_from_definition",
            wraps=sync_spawned_mobs_from_definition,
        ) as sync_mock:
            response = self.client.post(
                reverse("builder-world-manifest-apply", args=[self.world.pk]),
                {"manifest": manifest},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(sync_mock.call_count, 1)
        mob.refresh_from_db()
        self.assertEqual(mob.currency_reward_snapshot, {"obol": 7})

        clear_manifest = """
kind: mobdefinition
metadata:
  slug: guard
spec:
  rewards:
    currencies:
      obol: 0
"""
        with patch(
            "builders.mob_definitions.sync_spawned_mobs_from_definition",
            wraps=sync_spawned_mobs_from_definition,
        ) as clear_sync_mock:
            response = self.client.post(
                reverse("builder-world-manifest-apply", args=[self.world.pk]),
                {"manifest": clear_manifest},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(clear_sync_mock.call_count, 1)
        self.assertFalse(definition.currency_rewards.exists())
        mob.refresh_from_db()
        self.assertEqual(mob.currency_reward_snapshot, {})

    def test_mob_currency_reward_manifest_accepts_zero_and_omits_reward(self):
        create_currency(world=self.world, code="obol", name="Obol")
        manifest = """
kind: mobdefinition
metadata:
  slug: guard
  name: a guard
spec:
  rewards:
    currencies:
      obol: 0
"""

        response = self.client.post(
            reverse("builder-world-manifest-apply", args=[self.world.pk]),
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        definition = MobDefinition.objects.get(world=self.world, slug="guard")
        self.assertFalse(definition.currency_rewards.exists())
        self.assertNotIn(
            "rewards",
            response.data["mob_definition"]["manifest"]["spec"],
        )

    def test_mob_currency_reward_manifest_still_rejects_negative_amount(self):
        create_currency(world=self.world, code="obol", name="Obol")
        manifest = """
kind: mobdefinition
metadata:
  slug: guard
  name: a guard
spec:
  rewards:
    currencies:
      obol: -1
"""

        response = self.client.post(
            reverse("builder-world-manifest-apply", args=[self.world.pk]),
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            response.data["spec.rewards.currencies.obol"],
            ["Must be at least 0."],
        )
        self.assertFalse(
            MobDefinition.objects.filter(world=self.world, slug="guard").exists()
        )


class WalletTests(CurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.obol = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        self.drachma = create_currency(
            world=self.world,
            code="drachma",
            name="Drachma",
            plural_name="Drachmas",
        )
        self.player = self.create_player()

    def mutate(self, deltas, *, reason="currency test"):
        return mutate_balances(
            self.player,
            deltas,
            reason=reason,
            emit_event=False,
        )

    def test_credit_and_debit_update_a_sparse_balance(self):
        credit = self.mutate({self.obol: 10}, reason="award")
        debit = self.mutate({self.obol: -4}, reason="purchase")

        self.assertEqual(credit.changes[0].before, 0)
        self.assertEqual(credit.changes[0].after, 10)
        self.assertEqual(debit.changes[0].before, 10)
        self.assertEqual(debit.changes[0].after, 6)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=self.obol,
            ).amount,
            6,
        )

    def test_batch_mutation_is_atomic_and_bumps_revision_once(self):
        self.player.refresh_from_db()
        revision_before = self.player.wallet_revision

        mutation = self.mutate({self.obol: 10, self.drachma: 3})

        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before + 1)
        self.assertEqual(mutation.revision, revision_before + 1)
        self.assertEqual(balance_map(self.player), {"obol": 10, "drachma": 3})

    def test_cross_world_currency_is_rejected_without_mutation(self):
        other_world = World.objects.new_world(
            name="Other Economy",
            author=self.user,
        )
        foreign_currency = create_currency(
            world=other_world,
            code="token",
            name="Token",
        )
        self.player.refresh_from_db()
        revision_before = self.player.wallet_revision

        with self.assertRaises(WalletError) as raised:
            self.mutate({foreign_currency: 1})

        self.player.refresh_from_db()
        self.assertEqual(raised.exception.code, "cross_world_currency")
        self.assertEqual(self.player.wallet_revision, revision_before)
        self.assertFalse(
            PlayerCurrencyBalance.objects.filter(
                player=self.player,
                currency=foreign_currency,
            ).exists()
        )

    def test_insufficient_funds_rolls_back_the_whole_batch(self):
        self.mutate({self.obol: 5, self.drachma: 2})
        self.player.refresh_from_db()
        revision_before = self.player.wallet_revision

        with self.assertRaises(WalletError) as raised:
            self.mutate({self.obol: -6, self.drachma: 10})

        self.player.refresh_from_db()
        self.assertEqual(raised.exception.code, "insufficient_funds")
        self.assertEqual(self.player.wallet_revision, revision_before)
        self.assertEqual(balance_map(self.player), {"obol": 5, "drachma": 2})

    def test_maximum_safe_amount_is_accepted_and_overflow_is_rejected(self):
        mutation = self.mutate({self.obol: MAX_CURRENCY_AMOUNT})
        revision_at_max = mutation.revision

        with self.assertRaises(WalletError) as raised:
            self.mutate({self.obol: 1})

        self.player.refresh_from_db()
        self.assertEqual(raised.exception.code, "amount_out_of_range")
        self.assertEqual(self.player.wallet_revision, revision_at_max)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=self.obol,
            ).amount,
            MAX_CURRENCY_AMOUNT,
        )

    def test_empty_and_failed_mutations_do_not_bump_wallet_revision(self):
        successful = self.mutate({self.obol: 1})
        empty = self.mutate({})

        with self.assertRaises(WalletError):
            self.mutate({self.obol: -2})

        self.player.refresh_from_db()
        self.assertEqual(empty.revision, successful.revision)
        self.assertEqual(self.player.wallet_revision, successful.revision)

    def test_reset_replaces_wallet_with_configured_starting_balances(self):
        token = create_currency(
            world=self.world,
            code="token",
            name="Token",
        )
        replace_starting_balances(
            world=self.world,
            balances={self.obol: 25, self.drachma: 2},
        )
        self.mutate({self.obol: 3, self.drachma: 9, token: 4})
        self.player.refresh_from_db()
        revision_before_reset = self.player.wallet_revision

        self.player.initialize(reset=True, include_starting_equipment=False)

        self.player.refresh_from_db()
        self.assertEqual(
            balance_map(self.player),
            {"obol": 25, "drachma": 2, "token": 0},
        )
        self.assertEqual(
            self.player.wallet_revision,
            revision_before_reset + 1,
        )


class ObolOnlyWorldTests(CurrencyTestCase):
    def test_world_can_initialize_and_transact_with_obol_as_its_only_currency(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        replace_starting_balances(world=self.world, balances={obol: 11})
        player = self.create_player(name="Phalanx Tester")

        player.initialize(include_starting_equipment=False)

        self.world.refresh_from_db()
        self.assertEqual(self.world.default_currency, obol)
        self.assertEqual(
            list(self.world.currencies.values_list("code", flat=True)),
            ["obol"],
        )
        self.assertFalse(
            Currency.objects.filter(
                world=self.world,
                code__in=("gold", "medals"),
            ).exists()
        )
        self.assertNotIn("gold", {field.name for field in Player._meta.fields})
        self.assertEqual(balance_map(player), {"obol": 11})

        mutate_balances(
            player,
            {obol: -3},
            reason="Phalanx purchase",
            emit_event=False,
        )
        self.assertEqual(balance_map(player), {"obol": 8})
