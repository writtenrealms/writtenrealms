from django.contrib.contenttypes.models import ContentType

import yaml

from rest_framework.reverse import reverse

from builders.models import MobDefinition, Trigger
from config import constants as adv_consts
from tests.base import WorldTestCase


class TestMobDefinitionTriggerEndpoints(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.mob_definition = MobDefinition.objects.create(world=self.world)
        self.endpoint = reverse(
            "builder-mob-definition-reactions",
            args=[self.world.pk, self.mob_definition.key],
        )

    def _event_triggers_for_definition(self):
        return Trigger.objects.filter(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=self.mob_definition.id,
        ).order_by("id")

    def test_trigger_list_includes_yaml_and_definition(self):
        self._event_triggers_for_definition().delete()
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=self.mob_definition.id,
            event=adv_consts.MOB_REACTION_EVENT_SAYING,
            match="hello",
            script="say Greetings.",
            display_action_in_room=False,
        )

        resp = self.client.get(self.endpoint, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("new_trigger_template", resp.data)
        self.assertIn("triggers", resp.data)
        self.assertEqual(len(resp.data["triggers"]), 1)
        self.assertIn("yaml", resp.data["triggers"][0])
        self.assertIn("delete_yaml", resp.data["triggers"][0])
        self.assertIn("kind: trigger", resp.data["new_trigger_template"]["yaml"])
        self.assertIn("match:", resp.data["new_trigger_template"]["yaml"])
        self.assertIn("match:", resp.data["triggers"][0]["yaml"])
        self.assertEqual(resp.data["data"][0]["match"], "hello")
        template_manifest = resp.data["new_trigger_template"]["manifest"]
        self.assertEqual(
            template_manifest["apiVersion"],
            "writtenrealms.com/v1alpha3",
        )
        self.assertEqual(
            template_manifest["spec"]["target"],
            f"mobdefinition.{self.mob_definition.slug}",
        )
        self.assertEqual(
            yaml.safe_load(resp.data["new_trigger_template"]["yaml"])["spec"]["target"],
            f"mobdefinition.{self.mob_definition.slug}",
        )
        self.assertEqual(
            resp.data["triggers"][0]["manifest"]["spec"]["target"],
            f"mobdefinition.{self.mob_definition.slug}",
        )

    def test_add_mob_definition_trigger(self):
        resp = self.client.post(
            self.endpoint,
            {
                "event": "enter",
                "reaction": "say hi!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._event_triggers_for_definition().count(), 1)
        trigger = self._event_triggers_for_definition().first()
        self.assertEqual(trigger.kind, adv_consts.TRIGGER_KIND_EVENT)
        self.assertEqual(trigger.event, adv_consts.MOB_REACTION_EVENT_ENTERING)
        self.assertEqual(trigger.script, "say hi!")

        # Blank match is allowed for events that do not require a payload match.
        resp = self.client.post(
            self.endpoint,
            {
                "event": "enter",
                "reaction": "say hi!",
                "match": "",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._event_triggers_for_definition().count(), 2)

    def test_add_mob_definition_trigger_with_condition(self):
        resp = self.client.post(
            self.endpoint,
            {
                "event": "enter",
                "reaction": "say hi!",
                "conditions": "is_mob",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._event_triggers_for_definition().first().conditions, "is_mob")

    def test_update_mob_definition_trigger_rejects_room_event(self):
        trigger = Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=self.mob_definition.id,
            event=adv_consts.MOB_REACTION_EVENT_ENTERING,
            script="say Hello.",
            display_action_in_room=False,
        )
        detail_endpoint = reverse(
            "builder-mob-definition-reaction-detail",
            args=[self.world.pk, self.mob_definition.id, trigger.id],
        )

        resp = self.client.put(
            detail_endpoint,
            {
                "event": adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
                "reaction": "say This should not save.",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        trigger.refresh_from_db()
        self.assertEqual(trigger.event, adv_consts.MOB_REACTION_EVENT_ENTERING)
        self.assertEqual(trigger.script, "say Hello.")

    def test_match_is_required_for_say_event(self):
        resp = self.client.post(
            self.endpoint,
            {
                "event": "say",
                "reaction": "say hi!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
