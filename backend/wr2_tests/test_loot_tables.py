from builders.models import ItemDefinition, MobDefinition
from config import constants as adv_consts
from spawns.actions.combat import _append_mob_defeat_events
from spawns.models import Item
from tests.base import WorldTestCase
from wr2_tests.utils import apply_basic_stat_system


class TestLootTables(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

    def _item_definition(self, slug):
        return ItemDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=slug.replace("-", " "),
        )

    def test_death_loot_rolls_each_entry_independently_into_corpse(self):
        weapon_slugs = [
            "rusty-sword",
            "chipped-axe",
            "hunting-knife",
            "war-club",
            "short-spear",
        ]
        charm_slugs = [
            "bone-charm",
            "river-stone",
        ]
        for slug in [*weapon_slugs, *charm_slugs]:
            self._item_definition(slug)
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="scavenger",
            name="a scavenger",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"health_max": 10},
            loot={
                "entries": [
                    {
                        "slug": "weapon-drop",
                        "probability": 100,
                        "quantity": 1,
                        "source_pool": [
                            {"ref": f"itemdefinition.{slug}", "weight": 1}
                            for slug in weapon_slugs
                        ],
                    },
                    {
                        "slug": "charm-drop",
                        "probability": 100,
                        "quantity": 1,
                        "source_pool": [
                            {"ref": f"itemdefinition.{slug}", "weight": 1}
                            for slug in charm_slugs
                        ],
                    },
                ],
            },
        )
        mob = definition.spawn(self.room, self.spawn_world)

        _append_mob_defeat_events(
            player=self.player,
            target_mob=mob,
            room=self.room,
            events=[],
        )

        corpse = Item.objects.get(
            world=self.spawn_world,
            type=adv_consts.ITEM_TYPE_CORPSE,
        )
        loot_slugs = set(
            corpse.inventory
            .exclude(definition__isnull=True)
            .values_list("definition__slug", flat=True)
        )
        self.assertEqual(len(loot_slugs & set(weapon_slugs)), 1)
        self.assertEqual(len(loot_slugs & set(charm_slugs)), 1)
        self.assertEqual(len(loot_slugs), 2)
