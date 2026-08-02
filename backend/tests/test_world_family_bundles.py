import copy
from unittest import mock

import yaml

from django.contrib.auth import get_user_model

from rest_framework import serializers
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from builders import world_export as builder_world_export
from builders.currencies import create_currency
from builders.instance_templates import create_instance_template
from builders.models import RoomGetTrigger, WorldBuilder
from config import constants as adv_consts
from worlds.models import Room, World, WorldConfig


User = get_user_model()


class WorldFamilyBundleTests(APITestCase):

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            "world-family-bundles@example.com",
            "p",
        )
        self.client.force_authenticate(self.user)

        self.source_world = World.objects.new_world(
            name="Phalanx",
            author=self.user,
            config=WorldConfig.objects.create(),
            is_multiplayer=True,
        )
        create_currency(
            world=self.source_world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        self.source_start = self.source_world.config.starting_room
        self.source_start.name = "Phalanx Muster Yard"
        self.source_start.save(update_fields=["name"])
        self.source_gate = Room.objects.create(
            world=self.source_world,
            zone=self.source_start.zone,
            name="Hades Gate",
            x=1,
            y=0,
            z=0,
        )

        self.source_instance = create_instance_template(
            base_world=self.source_world,
            author=self.user,
            name="Hades",
            instance_slug="hades",
        )
        self.source_instance_start = (
            self.source_instance.config.starting_room
        )
        self.source_instance_start.name = "Hades Entrance"
        self.source_instance_start.save(update_fields=["name"])
        self.source_arrival = Room.objects.create(
            world=self.source_instance,
            zone=self.source_instance_start.zone,
            name="The Far Bank",
            x=1,
            y=0,
            z=0,
        )

        self.source_gate.enters_instance = self.source_instance
        self.source_gate.transfer_to = self.source_arrival
        self.source_gate.save(
            update_fields=["enters_instance", "transfer_to"]
        )
        self.source_arrival.exits_to = self.source_gate
        self.source_arrival.save(update_fields=["exits_to"])
        self.source_instance.config.exits_to = self.source_gate
        self.source_instance.config.save(update_fields=["exits_to"])

    def _export_bundle(self):
        response = self.client.get(
            reverse(
                "builder-world-export",
                args=[self.source_world.pk],
            )
        )
        self.assertEqual(response.status_code, 200, response.data)
        documents = [
            document
            for document in yaml.safe_load_all(response.data["yaml"])
            if document is not None
        ]
        return response, documents

    def _new_target(self, *, multiplayer=True, name="Import Target"):
        return World.objects.new_world(
            name=name,
            author=self.user,
            config=WorldConfig.objects.create(),
            is_multiplayer=multiplayer,
        )

    def _apply_documents(self, *, target_world, documents):
        return self.client.post(
            reverse(
                "builder-world-manifest-apply",
                args=[target_world.pk],
            ),
            {
                "manifest": yaml.safe_dump_all(
                    documents,
                    sort_keys=False,
                    explicit_start=True,
                ),
            },
            format="json",
        )

    def _assert_imported_links(self, *, target_world):
        imported_instance = World.objects.get(
            instance_of=target_world,
            context__isnull=True,
            instance_slug="hades",
        )
        imported_gate = target_world.rooms.get(
            relative_id=self.source_gate.relative_id,
        )
        imported_arrival = imported_instance.rooms.get(
            relative_id=self.source_arrival.relative_id,
        )
        imported_gate.refresh_from_db()
        imported_arrival.refresh_from_db()
        imported_instance.config.refresh_from_db()

        self.assertEqual(
            imported_gate.enters_instance_id,
            imported_instance.id,
        )
        self.assertEqual(
            imported_gate.transfer_to_id,
            imported_arrival.id,
        )
        self.assertEqual(
            imported_arrival.exits_to_id,
            imported_gate.id,
        )
        self.assertEqual(
            imported_instance.config.exits_to_id,
            imported_gate.id,
        )
        return imported_instance, imported_gate, imported_arrival

    def test_export_has_family_header_scopes_and_all_central_links(self):
        response, documents = self._export_bundle()

        self.assertEqual(response.data["summary"]["worlds"], 2)
        self.assertEqual(response.data["summary"]["instances"], 1)
        self.assertEqual(response.data["summary"]["links"], 4)
        header = documents[0]
        self.assertEqual(
            header["apiVersion"],
            "writtenrealms.com/v1alpha3",
        )
        self.assertEqual(header["kind"], "worldbundle")
        self.assertEqual(
            header["spec"]["worlds"],
            [
                {
                    "ref": "world@base",
                    "role": "base",
                    "name": "Phalanx",
                },
                {
                    "ref": "instance.hades",
                    "role": "instance",
                    "slug": "hades",
                    "name": "Hades",
                    "parent": "world@base",
                },
            ],
        )
        self.assertEqual(header["spec"]["links_mode"], "replace")

        gate_ref = f"room@{self.source_gate.relative_id}"
        arrival_ref = f"room@{self.source_arrival.relative_id}"
        links_by_relation = {
            link["relation"]: link
            for link in header["spec"]["links"]
        }
        self.assertEqual(
            links_by_relation,
            {
                "room.enters_instance": {
                    "relation": "room.enters_instance",
                    "source": {
                        "world": "world@base",
                        "room": gate_ref,
                    },
                    "target": {
                        "world": "instance.hades",
                    },
                },
                "room.transfer_to": {
                    "relation": "room.transfer_to",
                    "source": {
                        "world": "world@base",
                        "room": gate_ref,
                    },
                    "target": {
                        "world": "instance.hades",
                        "room": arrival_ref,
                    },
                },
                "room.exits_to": {
                    "relation": "room.exits_to",
                    "source": {
                        "world": "instance.hades",
                        "room": arrival_ref,
                    },
                    "target": {
                        "world": "world@base",
                        "room": gate_ref,
                    },
                },
                "world_config.exits_to": {
                    "relation": "world_config.exits_to",
                    "source": {
                        "world": "instance.hades",
                    },
                    "target": {
                        "world": "world@base",
                        "room": gate_ref,
                    },
                },
            },
        )

        scoped_documents = documents[1:]
        self.assertTrue(scoped_documents)
        self.assertEqual(
            {
                document["metadata"]["world_ref"]
                for document in scoped_documents
            },
            {"world@base", "instance.hades"},
        )
        for document in scoped_documents:
            self.assertIn("world_ref", document["metadata"])
        for world_ref in ("world@base", "instance.hades"):
            self.assertEqual(
                sum(
                    document["kind"] == "world"
                    and document["metadata"]["world_ref"] == world_ref
                    for document in scoped_documents
                ),
                1,
            )

    def test_fresh_multiplayer_import_preserves_identity_and_is_idempotent(self):
        _, documents = self._export_bundle()
        target_world = self._new_target()

        first_response = self._apply_documents(
            target_world=target_world,
            documents=documents,
        )
        self.assertEqual(
            first_response.status_code,
            200,
            first_response.data,
        )
        self.assertEqual(first_response.data["kind"], "worldbundle")
        self.assertEqual(first_response.data["summary"]["worlds"], 2)
        self.assertEqual(first_response.data["summary"]["links"], 4)

        imported_instance, imported_gate, imported_arrival = (
            self._assert_imported_links(target_world=target_world)
        )
        self.assertNotEqual(target_world.id, self.source_world.id)
        self.assertNotEqual(
            imported_instance.id,
            self.source_instance.id,
        )
        self.assertNotEqual(imported_gate.id, self.source_gate.id)
        self.assertNotEqual(
            imported_arrival.id,
            self.source_arrival.id,
        )
        self.assertEqual(
            imported_gate.relative_id,
            self.source_gate.relative_id,
        )
        self.assertEqual(
            imported_arrival.relative_id,
            self.source_arrival.relative_id,
        )
        self.assertEqual(imported_instance.instance_slug, "hades")

        imported_ids = {
            "instance": imported_instance.id,
            "gate": imported_gate.id,
            "arrival": imported_arrival.id,
        }
        base_room_count = target_world.rooms.count()
        instance_room_count = imported_instance.rooms.count()

        second_response = self._apply_documents(
            target_world=target_world,
            documents=documents,
        )
        self.assertEqual(
            second_response.status_code,
            200,
            second_response.data,
        )
        reimported_instance, reimported_gate, reimported_arrival = (
            self._assert_imported_links(target_world=target_world)
        )
        self.assertEqual(reimported_instance.id, imported_ids["instance"])
        self.assertEqual(reimported_gate.id, imported_ids["gate"])
        self.assertEqual(reimported_arrival.id, imported_ids["arrival"])
        self.assertEqual(target_world.rooms.count(), base_room_count)
        self.assertEqual(
            reimported_instance.rooms.count(),
            instance_room_count,
        )
        self.assertEqual(
            World.objects.filter(
                instance_of=target_world,
                context__isnull=True,
                instance_slug="hades",
            ).count(),
            1,
        )

        reexport = self.client.get(
            reverse("builder-world-export", args=[target_world.pk])
        )
        self.assertEqual(reexport.status_code, 200, reexport.data)
        self.assertEqual(
            [
                document
                for document in yaml.safe_load_all(reexport.data["yaml"])
                if document is not None
            ],
            documents,
        )

    def test_late_failure_rolls_back_templates_rooms_and_links(self):
        _, documents = self._export_bundle()
        target_world = self._new_target(name="Rollback Target")
        target_start = target_world.config.starting_room
        legacy_instance = create_instance_template(
            base_world=target_world,
            author=self.user,
            name="Legacy Trial",
            instance_slug="legacy-trial",
        )
        legacy_room = legacy_instance.config.starting_room
        target_start.enters_instance = legacy_instance
        target_start.transfer_to = legacy_room
        target_start.save(
            update_fields=["enters_instance", "transfer_to"]
        )
        legacy_room.exits_to = target_start
        legacy_room.save(update_fields=["exits_to"])
        legacy_instance.config.exits_to = target_start
        legacy_instance.config.save(update_fields=["exits_to"])

        original_apply_links = (
            builder_world_export.apply_world_bundle_links
        )

        def apply_links_then_fail(*args, **kwargs):
            original_apply_links(*args, **kwargs)
            raise serializers.ValidationError(
                "Injected failure after bundle links were applied."
            )

        with mock.patch(
            "builders.world_export.apply_world_bundle_links",
            side_effect=apply_links_then_fail,
        ):
            response = self._apply_documents(
                target_world=target_world,
                documents=documents,
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(
            World.objects.filter(
                instance_of=target_world,
                context__isnull=True,
                instance_slug="hades",
            ).exists()
        )
        self.assertEqual(
            set(
                World.objects.filter(
                    instance_of=target_world,
                    context__isnull=True,
                ).values_list("id", flat=True)
            ),
            {legacy_instance.id},
        )
        self.assertEqual(
            set(target_world.rooms.values_list("id", flat=True)),
            {target_start.id},
        )

        target_world.refresh_from_db()
        target_start.refresh_from_db()
        legacy_room.refresh_from_db()
        legacy_instance.config.refresh_from_db()
        self.assertEqual(target_world.name, "Rollback Target")
        self.assertEqual(target_start.name, "Starting Room")
        self.assertEqual(
            target_start.enters_instance_id,
            legacy_instance.id,
        )
        self.assertEqual(
            target_start.transfer_to_id,
            legacy_room.id,
        )
        self.assertEqual(legacy_room.exits_to_id, target_start.id)
        self.assertEqual(
            legacy_instance.config.exits_to_id,
            target_start.id,
        )

    def test_import_rejects_single_player_target(self):
        _, documents = self._export_bundle()
        target_world = self._new_target(multiplayer=False)

        response = self._apply_documents(
            target_world=target_world,
            documents=documents,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("multiplayer", str(response.data).lower())
        self.assertFalse(
            World.objects.filter(
                instance_of=target_world,
                context__isnull=True,
            ).exists()
        )

    def test_rank_two_builder_cannot_import_bundle_or_create_data(self):
        _, documents = self._export_bundle()
        target_world = self._new_target()
        original_room_ids = set(
            target_world.rooms.values_list("id", flat=True)
        )
        builder_user = User.objects.create_user(
            "rank-two-family-builder@example.com",
            "p",
        )
        WorldBuilder.objects.create(
            world=target_world,
            user=builder_user,
            builder_rank=2,
        )
        self.client.force_authenticate(builder_user)

        response = self._apply_documents(
            target_world=target_world,
            documents=documents,
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(
            set(target_world.rooms.values_list("id", flat=True)),
            original_room_ids,
        )
        self.assertFalse(
            World.objects.filter(
                instance_of=target_world,
                context__isnull=True,
            ).exists()
        )

    def test_import_rejects_archived_instance_slug_collision(self):
        _, documents = self._export_bundle()
        target_world = self._new_target()
        archived_template = create_instance_template(
            base_world=target_world,
            author=self.user,
            name="Archived Hades",
            instance_slug="hades",
        )
        archived_template.lifecycle = adv_consts.WORLD_STATE_ARCHIVED
        archived_template.save(update_fields=["lifecycle"])
        original_base_room_ids = set(
            target_world.rooms.values_list("id", flat=True)
        )
        original_template_room_ids = set(
            archived_template.rooms.values_list("id", flat=True)
        )

        response = self._apply_documents(
            target_world=target_world,
            documents=documents,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("archived", str(response.data).lower())
        self.assertEqual(
            World.objects.filter(
                instance_of=target_world,
                context__isnull=True,
                instance_slug="hades",
            ).count(),
            1,
        )
        self.assertEqual(
            set(target_world.rooms.values_list("id", flat=True)),
            original_base_room_ids,
        )
        self.assertEqual(
            set(
                archived_template.rooms.values_list("id", flat=True)
            ),
            original_template_room_ids,
        )
        archived_template.refresh_from_db()
        self.assertEqual(
            archived_template.lifecycle,
            adv_consts.WORLD_STATE_ARCHIVED,
        )

    def test_import_preserves_authored_default_looking_target_room(self):
        _, documents = self._export_bundle()
        documents = copy.deepcopy(documents)
        source_start_ref = (
            f"room@{self.source_start.relative_id}"
        )
        source_gate_ref = f"room@{self.source_gate.relative_id}"
        documents = [
            document
            for document in documents
            if not (
                document["kind"] == "room"
                and document["metadata"]["world_ref"] == "world@base"
                and document["metadata"]["ref"] == source_start_ref
            )
        ]
        base_world_document = next(
            document
            for document in documents
            if document["kind"] == "world"
            and document["metadata"]["world_ref"] == "world@base"
        )
        base_world_document["spec"]["starting_room"] = source_gate_ref
        base_world_document["spec"]["death_room"] = source_gate_ref

        target_world = self._new_target()
        target_start = target_world.config.starting_room
        target_start.initial_state = {"builder_note": "keep me"}
        target_start.save(update_fields=["initial_state"])
        room_get_trigger = RoomGetTrigger.objects.create(
            room=target_start,
            name="Preserve target scaffold",
            action=adv_consts.ROOM_TRIGGER_ACTION_MESSAGE,
            message="This room is authored.",
        )

        response = self._apply_documents(
            target_world=target_world,
            documents=documents,
        )

        self.assertEqual(response.status_code, 200, response.data)
        target_start.refresh_from_db()
        self.assertEqual(
            target_start.initial_state,
            {"builder_note": "keep me"},
        )
        self.assertTrue(
            RoomGetTrigger.objects.filter(pk=room_get_trigger.pk).exists()
        )
        self.assertTrue(
            target_world.rooms.filter(
                relative_id=self.source_gate.relative_id,
            ).exists()
        )

    def test_import_rejects_incomplete_link_endpoints(self):
        _, documents = self._export_bundle()
        target_world = self._new_target()
        cases = (
            ("room.enters_instance", "source", "room"),
            ("room.transfer_to", "target", "room"),
            ("room.exits_to", "source", "room"),
            ("world_config.exits_to", "target", "room"),
        )

        for relation, endpoint, field_name in cases:
            with self.subTest(
                relation=relation,
                endpoint=endpoint,
                field=field_name,
            ):
                invalid_documents = copy.deepcopy(documents)
                link = next(
                    candidate
                    for candidate in invalid_documents[0]["spec"]["links"]
                    if candidate["relation"] == relation
                )
                link[endpoint].pop(field_name)

                response = self._apply_documents(
                    target_world=target_world,
                    documents=invalid_documents,
                )

                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(
                    World.objects.filter(
                        instance_of=target_world,
                        context__isnull=True,
                    ).exists()
                )

    def test_replace_mode_clears_an_omitted_link_without_resurrecting_it(self):
        _, documents = self._export_bundle()
        target_world = self._new_target()
        initial_response = self._apply_documents(
            target_world=target_world,
            documents=documents,
        )
        self.assertEqual(
            initial_response.status_code,
            200,
            initial_response.data,
        )
        self._assert_imported_links(target_world=target_world)

        replacement_documents = copy.deepcopy(documents)
        replacement_documents[0]["spec"]["links"] = [
            link
            for link in replacement_documents[0]["spec"]["links"]
            if link["relation"] != "room.enters_instance"
        ]
        replacement_response = self._apply_documents(
            target_world=target_world,
            documents=replacement_documents,
        )
        self.assertEqual(
            replacement_response.status_code,
            200,
            replacement_response.data,
        )

        imported_instance = World.objects.get(
            instance_of=target_world,
            context__isnull=True,
            instance_slug="hades",
        )
        imported_gate = target_world.rooms.get(
            relative_id=self.source_gate.relative_id,
        )
        imported_arrival = imported_instance.rooms.get(
            relative_id=self.source_arrival.relative_id,
        )
        imported_gate.refresh_from_db()
        imported_arrival.refresh_from_db()
        imported_instance.config.refresh_from_db()
        self.assertIsNone(imported_gate.enters_instance_id)
        self.assertEqual(
            imported_gate.transfer_to_id,
            imported_arrival.id,
        )
        self.assertEqual(
            imported_arrival.exits_to_id,
            imported_gate.id,
        )
        self.assertEqual(
            imported_instance.config.exits_to_id,
            imported_gate.id,
        )

        reexport = self.client.get(
            reverse("builder-world-export", args=[target_world.pk])
        )
        self.assertEqual(reexport.status_code, 200, reexport.data)
        reexported_header = next(
            document
            for document in yaml.safe_load_all(reexport.data["yaml"])
            if document is not None
        )
        self.assertEqual(
            {
                link["relation"]
                for link in reexported_header["spec"]["links"]
            },
            {
                "room.transfer_to",
                "room.exits_to",
                "world_config.exits_to",
            },
        )
