from django.urls import reverse

from builders.models import Social
from core.socials import SOCIAL_CATALOG_MAX_DEFINITIONS
from tests.base import WorldTestCase
from worlds.models import WorldConfig


class TestSocialBuilderApi(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.list_endpoint = reverse(
            "builder-social-list",
            args=[self.world.pk],
        )

    def _payload(self, *, command="wave"):
        return {
            "cmd": command,
            "priority": 7,
            "msg_targetless_self": "You wave.",
            "msg_targetless_other": "{{ Actor }} waves.",
            "msg_targeted_self": "You wave at {{ target }}.",
            "msg_targeted_target": "{{ Actor }} waves at you.",
            "msg_targeted_other": "{{ Actor }} waves at {{ target }}.",
        }

    def test_create_normalizes_command_and_rejects_case_duplicate(self):
        response = self.client.post(
            self.list_endpoint,
            self._payload(command="  WaVe  "),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["cmd"], "wave")
        social = Social.objects.get(world=self.world)
        self.assertEqual(social.cmd, "wave")

        duplicate = self.client.post(
            self.list_endpoint,
            self._payload(command="WAVE"),
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertIn("already exists", str(duplicate.data))

    def test_create_rejects_unbounded_template_constructs(self):
        payload = self._payload(command="loop")
        payload["msg_targetless_other"] = (
            "{% for value in actor_state %}{{ value }}{% endfor %}"
        )

        response = self.client.post(
            self.list_endpoint,
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("not loops", str(response.data))
        self.assertFalse(Social.objects.filter(world=self.world).exists())

    def test_create_rejects_removed_mark_template_variables(self):
        cases = (
            ("actor-mark", "msg_targetless_other", "{{ actor_marks.badge }}"),
            ("target-mark", "msg_targeted_other", "{{ target_marks.oath }}"),
        )
        for command, field_name, template in cases:
            with self.subTest(command=command):
                payload = self._payload(command=command)
                payload[field_name] = template

                response = self.client.post(
                    self.list_endpoint,
                    payload,
                    format="json",
                )

                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("actor_state and target_state", str(response.data))
                self.assertFalse(
                    Social.objects.filter(world=self.world, cmd=command).exists()
                )

    def test_create_rejects_materializing_template_expressions(self):
        cases = (
            ("concat", "{{ actor_title ~ actor_title }}"),
            ("collection", "{{ [actor_title, actor_title] }}"),
        )
        for command, template in cases:
            with self.subTest(command=command):
                payload = self._payload(command=command)
                payload["msg_targetless_other"] = template

                response = self.client.post(
                    self.list_endpoint,
                    payload,
                    format="json",
                )

                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(
                    Social.objects.filter(world=self.world, cmd=command).exists()
                )

    def test_update_rejects_social_command_rename(self):
        social = Social.objects.create(
            world=self.world,
            **self._payload(command="wave"),
        )
        detail_endpoint = reverse(
            "builder-social-details",
            args=[self.world.pk, social.pk],
        )

        response = self.client.put(
            detail_endpoint,
            self._payload(command="salute"),
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        social.refresh_from_db()
        self.assertEqual(social.cmd, "wave")
        self.assertFalse(
            Social.objects.filter(world=self.world, cmd="salute").exists()
        )

    def test_create_rejects_catalogs_beyond_the_runtime_bound(self):
        Social.objects.bulk_create([
            Social(
                world=self.world,
                cmd=f"gesture{index:03d}",
                msg_targetless_self="You gesture.",
                msg_targetless_other="{{ Actor }} gestures.",
            )
            for index in range(SOCIAL_CATALOG_MAX_DEFINITIONS)
        ])

        response = self.client.post(
            self.list_endpoint,
            self._payload(command="overflow"),
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("at most 512 socials", str(response.data))
        self.assertFalse(
            Social.objects.filter(world=self.world, cmd="overflow").exists()
        )

    def test_instance_reads_base_socials_but_rejects_writes(self):
        social = Social.objects.create(
            world=self.world,
            **self._payload(command="nod"),
        )
        instance_world = self.world.__class__.objects.new_world(
            name="Inherited Social Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
        )
        instance_list = reverse(
            "builder-social-list",
            args=[instance_world.pk],
        )

        response = self.client.get(instance_list)
        self.assertEqual(response.status_code, 200, response.data)
        rows = (
            response.data.get("results", response.data)
            if isinstance(response.data, dict)
            else response.data
        )
        self.assertEqual([row["cmd"] for row in rows], ["nod"])

        detail_endpoint = reverse(
            "builder-social-details",
            args=[instance_world.pk, social.pk],
        )
        response = self.client.get(detail_endpoint)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["cmd"], "nod")

        response = self.client.post(
            instance_list,
            self._payload(command="wave"),
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("read-only", str(response.data))
        self.assertFalse(Social.objects.filter(world=instance_world).exists())

        response = self.client.delete(detail_endpoint)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(Social.objects.filter(pk=social.pk).exists())
