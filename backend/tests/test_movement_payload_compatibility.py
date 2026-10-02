from django.contrib.contenttypes.models import ContentType

from builders.currencies import create_currency
from builders.models import (
    CraftMaterial,
    ItemDefinition,
    ItemSalvageYield,
    MerchantProfile,
    TrainerProfile,
    Trigger,
)
from config import constants as adv_consts
from spawns.handlers import dispatch_command
from spawns.models import Item, Player, PlayerCurrencyBalance
from spawns.state_payloads import get_player_with_related, serialize_actor
from tests.base import WorldTestCase
from tests.utils import apply_basic_stat_system, capture_game_messages
from worlds.models import Room, World, WorldConfig


class MovementPayloadCompatibilityTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.currency = create_currency(
            world=self.world, code="token", name="Token", plural_name="Tokens"
        )

    def test_eager_payload_matches_lazy_relations_for_custom_and_instance_worlds(self):
        template = World.objects.new_world(
            name="Custom instance", author=self.user, instance_of=self.world,
            config=WorldConfig.objects.create(),
        )
        instance_runtime = template.create_spawn_world(instance_ref="custom-payload")
        for runtime, room in (
            (self.spawn_world, self.room),
            (instance_runtime, template.config.starting_room),
        ):
            with self.subTest(runtime=runtime.pk):
                player = self.create_player("Custom traveller", world=runtime, room=room)
                player.archetype = "warrior"
                player.attributes = {"brawn": 3, "grit": 4, "focus": 2}
                player.save(update_fields=["archetype", "attributes"])
                PlayerCurrencyBalance.objects.create(
                    player=player, currency=self.currency, amount=17
                )
                item = Item.objects.create(
                    world=runtime, container=player.equipment, name="Lucky blade",
                    type=adv_consts.ITEM_TYPE_EQUIPPABLE, health_max=7,
                    cost=5, currency=self.currency,
                )
                player.equipment.weapon = item
                player.equipment.save(update_fields=["weapon"])

                lazy = serialize_actor(
                    Player.objects.get(pk=player.pk), Room.objects.get(pk=room.pk)
                ).model_dump()
                loaded = get_player_with_related(player.pk)
                eager = serialize_actor(loaded, loaded.room).model_dump()

                self.assertEqual(eager, lazy)
                self.assertEqual(set(eager["attributes"]), {"brawn", "grit", "focus"})
                self.assertEqual(eager["economy"]["balances"], {"token": 17})
                self.assertEqual(eager["equipment"]["weapon"]["value"]["currency"], "token")

    def test_equipment_batch_preserves_item_actions_and_salvageability(self):
        definition = ItemDefinition.objects.create(
            world=self.world, slug="salvage-blade", name="A salvage blade",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
        )
        material = CraftMaterial.objects.create(
            world=self.world, slug="iron", name="Iron"
        )
        ItemSalvageYield.objects.create(
            item_definition=definition, material=material, quantity=1
        )
        blade = Item.objects.create(
            world=self.spawn_world, container=self.player.equipment,
            name="A salvage blade", definition=definition,
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
        )
        hat = Item.objects.create(
            world=self.spawn_world, container=self.player.equipment,
            name="A plain hat", type=adv_consts.ITEM_TYPE_EQUIPPABLE,
        )
        self.player.equipment.weapon = blade
        self.player.equipment.head = hat
        self.player.equipment.save(update_fields=["weapon", "head"])
        for item, command in ((blade, "polish blade"), (hat, "tip hat")):
            Trigger.objects.create(
                world=self.world, kind=adv_consts.TRIGGER_KIND_COMMAND,
                scope=adv_consts.TRIGGER_SCOPE_ROOM,
                target_type=ContentType.objects.get_for_model(Item), target_id=item.pk,
                match=command, script="/echo -- Done.", display_action_in_room=True,
            )

        player = get_player_with_related(self.player.pk)
        equipment = serialize_actor(player, player.room).equipment

        self.assertEqual(equipment.weapon.actions, ["polish blade"])
        self.assertEqual(equipment.head.actions, ["tip hat"])
        self.assertTrue(equipment.weapon.is_salvageable)
        self.assertFalse(equipment.head.is_salvageable)

    def test_movement_carries_destination_service_profile_metadata(self):
        destination = self.room.create_at(adv_consts.DIRECTION_EAST)
        destination.merchant_profile = MerchantProfile.objects.create(
            world=self.world, slug="market", name="Token Exchange",
            settlement_currency=self.currency,
        )
        destination.trainer_profile = TrainerProfile.objects.create(
            world=self.world, slug="training", name="Training Hall",
        )
        destination.save(update_fields=["merchant_profile", "trainer_profile"])
        self.player.stamina = 100
        self.player.save(update_fields=["stamina"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="move", player_id=self.player.pk,
                payload={"direction": "east"},
            )

        move = next(
            message["message"] for message in messages
            if message["message"]["type"] == "cmd.move.success"
        )
        room = move["data"]["room"]
        self.assertEqual(room["merchant_provider"]["name"], "Token Exchange")
        self.assertEqual(room["training_provider"]["name"], "Training Hall")
        self.assertEqual(room["training_provider"]["profile"]["slug"], "training")
