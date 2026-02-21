from django.contrib.contenttypes.models import ContentType

from rest_framework.reverse import reverse

from builders.models import MobTemplate, Trigger
from config import constants as adv_consts
from tests.base import WorldTestCase


class TestMobTemplateTriggerEndpoints(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.mob_template = MobTemplate.objects.create(world=self.world)
        self.endpoint = reverse(
            "builder-mob-template-reactions",
            args=[self.world.pk, self.mob_template.key],
        )

    def _event_triggers_for_template(self):
        return Trigger.objects.filter(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobTemplate),
            target_id=self.mob_template.id,
        ).order_by("id")

    def test_trigger_list_includes_yaml_and_template(self):
        self._event_triggers_for_template().delete()
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobTemplate),
            target_id=self.mob_template.id,
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

    def test_add_mob_template_trigger(self):
        resp = self.client.post(
            self.endpoint,
            {
                "event": "enter",
                "reaction": "say hi!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._event_triggers_for_template().count(), 1)
        trigger = self._event_triggers_for_template().first()
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
        self.assertEqual(self._event_triggers_for_template().count(), 2)

    def test_add_mob_template_trigger_with_condition(self):
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
        self.assertEqual(self._event_triggers_for_template().first().conditions, "is_mob")

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
