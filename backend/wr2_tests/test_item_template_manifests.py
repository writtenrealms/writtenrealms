import yaml

from rest_framework.reverse import reverse

from builders.models import Currency, ItemTemplate
from tests.base import WorldTestCase


class AuthenticatedBuilderWorldTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)


class TestItemTemplateManifests(AuthenticatedBuilderWorldTestCase):
    def setUp(self):
        super().setUp()
        self.apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[self.world.pk],
        )
        self.default_currency = Currency.objects.create(
            world=self.world,
            code="gold",
            name="Gold",
            is_default=True,
        )

    def test_item_template_detail_endpoint_includes_manifest_yaml(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name="a sword",
            currency=self.default_currency,
        )
        endpoint = reverse(
            "builder-item-template-detail",
            args=[self.world.pk, item_template.pk],
        )

        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("manifest", resp.data)
        self.assertIn("yaml", resp.data)

        manifest = yaml.safe_load(resp.data["yaml"])
        self.assertEqual(manifest["kind"], "itemtemplate")
        self.assertEqual(manifest["metadata"]["slug"], item_template.slug)
        self.assertEqual(manifest["spec"]["currency"], self.default_currency.code)

    def test_apply_item_template_manifest_updates_existing_item_template(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name="a ration",
            type="food",
            food_type="stamina",
            cost=5,
            currency=self.default_currency,
        )

        manifest = f"""
kind: itemtemplate
metadata:
  world: world.{self.world.id}
  slug: {item_template.slug}
spec:
  cost: 12
  notes: Updated through YAML.
  currency: {self.default_currency.code}
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["kind"], "itemtemplate")
        self.assertEqual(resp.data["operation"], "updated")

        item_template.refresh_from_db()
        self.assertEqual(item_template.cost, 12)
        self.assertEqual(item_template.notes, "Updated through YAML.")
        self.assertEqual(item_template.type, "food")
        self.assertEqual(item_template.food_type, "stamina")

    def test_apply_item_template_manifest_can_create_item_template(self):
        manifest = f"""
kind: itemtemplate
metadata:
  world: world.{self.world.id}
  slug: starter-blade
  name: a starter blade
spec:
  level: 3
  type: equippable
  cost: 12
  currency: {self.default_currency.code}
"""
        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["kind"], "itemtemplate")
        self.assertEqual(resp.data["operation"], "created")

        item_template = ItemTemplate.objects.get(slug="starter-blade")
        self.assertEqual(item_template.name, "a starter blade")
        self.assertEqual(item_template.level, 3)
        self.assertEqual(item_template.type, "equippable")
        self.assertEqual(item_template.cost, 12)
        self.assertEqual(item_template.currency, self.default_currency)
