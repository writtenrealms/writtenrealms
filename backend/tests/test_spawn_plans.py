import random
from datetime import timedelta
from unittest import mock

import yaml

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from builders import world_export as builder_world_export
from builders.models import ItemDefinition, MobDefinition, Path, PathRoom, SpawnEntry, SpawnPlan, SpawnPlacement, SpawnPlanRun
from config import constants as adv_consts
from spawns.loading import (
    _initialize_door_reset_schedules,
    run_spawn_plans_for_world,
)
from spawns.models import DoorState, Item, Mob
from spawns.spawn_plans import (
    SpawnReconcileContext,
    _choose_room_for_entry,
    _target_entry_slug,
    run_spawn_plans,
)
from spawns.tasks import run_mob_roaming
from tests.base import WorldTestCase
from worlds.models import (
    Door,
    Doorway,
    Room,
    RoomFlag,
    World,
    WorldConfig,
    ZoneDoorResetSchedule,
)
from worlds.services import WorldSmith


class TestSpawnPlanManifests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.apply_ep = reverse("builder-world-manifest-apply", args=[self.world.pk])
        self.zone_ep = reverse("builder-zone-detail", args=[self.world.pk, self.zone.pk])
        self.mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-dummy",
            name="a practice dummy",
            mob_type=adv_consts.MOB_TYPE_CONSTRUCT,
            base_properties={"health_max": 10},
        )

    def test_zone_detail_exposes_manifest_ref(self):
        resp = self.client.get(self.zone_ep)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["relative_id"], self.zone.relative_id)
        self.assertEqual(resp.data["manifest_ref"], f"zone@{self.zone.relative_id}")
        self.assertEqual(
            resp.data["respawn"],
            {"mode": "fixed", "seconds": 300},
        )
        self.assertEqual(
            resp.data["door_reset"],
            {"mode": "fixed", "seconds": 300},
        )
        self.assertNotIn("respawn_wait", resp.data)

    def test_apply_zone_manifest_updates_typed_policies(self):
        manifest = f"""
kind: zone
metadata:
  ref: zone@{self.zone.relative_id}
  name: Starting Zone
spec:
  respawn:
    mode: none
  door_reset:
    mode: fixed
    seconds: 90
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.respawn_mode, "none")
        self.assertIsNone(self.zone.respawn_seconds)
        self.assertEqual(self.zone.door_reset_mode, "fixed")
        self.assertEqual(self.zone.door_reset_seconds, 90)
        self.assertEqual(resp.data["zone"]["respawn"], {"mode": "none"})
        self.assertEqual(
            resp.data["zone"]["door_reset"],
            {"mode": "fixed", "seconds": 90},
        )
        exported = yaml.safe_load(resp.data["zone"]["yaml"])
        self.assertEqual(
            exported["apiVersion"],
            builder_world_export.CANONICAL_MANIFEST_API_VERSION,
        )
        self.assertEqual(exported["spec"]["respawn"], {"mode": "none"})
        self.assertEqual(
            exported["spec"]["door_reset"],
            {"mode": "fixed", "seconds": 90},
        )

    def test_zone_manifest_round_trips_nullable_default_roam_chance(self):
        for value, yaml_value in (
            (0, "0"),
            (10, "'10'"),
            (100, "100"),
            (None, "null"),
        ):
            with self.subTest(value=value):
                manifest = f"""
kind: zone
metadata:
  ref: zone@{self.zone.relative_id}
  name: Starting Zone
spec:
  default_roam_chance: {yaml_value}
"""

                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 200, resp.data)
                self.zone.refresh_from_db()
                self.assertEqual(self.zone.default_roam_chance, value)
                exported = yaml.safe_load(resp.data["zone"]["yaml"])
                self.assertEqual(
                    exported["spec"]["default_roam_chance"],
                    value,
                )

    def test_zone_manifest_omitted_default_roam_chance_preserves_value(self):
        self.zone.default_roam_chance = 37
        self.zone.save(update_fields=["default_roam_chance"])
        manifest = f"""
kind: zone
metadata:
  ref: zone@{self.zone.relative_id}
  name: Starting Zone
spec:
  notes: Leave the roaming default unchanged.
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.default_roam_chance, 37)

    def test_zone_manifest_rejects_invalid_default_roam_chance(self):
        invalid_values = ("-1", "101", "1.5", "true", "'often'")

        for yaml_value in invalid_values:
            with self.subTest(value=yaml_value):
                manifest = f"""
kind: zone
metadata:
  ref: zone@{self.zone.relative_id}
  name: Starting Zone
spec:
  default_roam_chance: {yaml_value}
"""

                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 400, resp.data)
                self.assertIn("default_roam_chance", str(resp.data))

    def test_apply_zone_manifest_rejects_invalid_policy_shapes(self):
        invalid_specs = (
            ("respawn: fixed", "spec.respawn must be a mapping"),
            (
                "respawn:\n    mode: fixed",
                "spec.respawn.seconds is required",
            ),
            (
                "respawn:\n    mode: fixed\n    seconds: -1",
                "spec.respawn.seconds must be a non-negative integer",
            ),
            (
                "respawn:\n    mode: fixed\n    seconds: 1.5",
                "spec.respawn.seconds must be a non-negative integer",
            ),
            (
                "respawn:\n    mode: fixed\n    seconds: 2147483648",
                "spec.respawn.seconds must be a non-negative integer",
            ),
            (
                "door_reset:\n    mode: fixed\n    seconds: true",
                "spec.door_reset.seconds must be a non-negative integer",
            ),
            (
                "door_reset:\n    mode: none\n    seconds: 60",
                "spec.door_reset.seconds is not supported",
            ),
            (
                "door_reset:\n    mode: someday",
                "spec.door_reset.mode must be one of",
            ),
            (
                "respawn:\n    mode: fixed\n    seconds: 60\n    delay: 60",
                "spec.respawn has unsupported field(s): delay",
            ),
            ("pvp_zone: 'false'", "spec.pvp_zone must be a boolean"),
            ("respawn_wait: 60", "spec has unsupported field(s): respawn_wait"),
        )

        for spec, expected_error in invalid_specs:
            with self.subTest(spec=spec):
                manifest = f"""
kind: zone
metadata:
  ref: zone@{self.zone.relative_id}
  name: Starting Zone
spec:
  {spec}
"""
                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 400, resp.data)
                self.assertIn(expected_error, str(resp.data))

    def test_door_policy_edit_lazily_reschedules_runtime_without_stale_reset(self):
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        door_schedule = ZoneDoorResetSchedule.objects.get(
            world=spawn_world,
            zone=self.zone,
        )
        original_deadline = door_schedule.next_reset_ts
        original_policy_version = door_schedule.policy_version
        self.assertIsNotNone(original_deadline)

        destination = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Policy Edit Annex",
            x=99,
            y=1,
            z=0,
        )
        doorway = Doorway.objects.create(
            world=self.world,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=doorway,
            direction="east",
            from_room=self.room,
            to_room=destination,
            name="policy gate",
        )
        door_state = DoorState.objects.create(
            world=spawn_world,
            doorway=doorway,
            state=adv_consts.DOOR_STATE_CLOSED,
            revision=7,
        )

        disable_manifest = f"""
kind: zone
metadata:
  ref: zone@{self.zone.relative_id}
  name: Starting Zone
spec:
  door_reset:
    mode: none
"""
        disable_resp = self.client.post(
            self.apply_ep,
            {"manifest": disable_manifest},
            format="json",
        )

        self.assertEqual(disable_resp.status_code, 200, disable_resp.data)
        self.zone.refresh_from_db()
        door_schedule.refresh_from_db()
        disabled_policy_version = self.zone.door_reset_policy_version
        self.assertEqual(disabled_policy_version, original_policy_version + 1)
        self.assertEqual(door_schedule.next_reset_ts, original_deadline)
        self.assertEqual(door_schedule.policy_version, original_policy_version)

        door_schedule.next_reset_ts = timezone.now() - timedelta(seconds=1)
        door_schedule.save(update_fields=["next_reset_ts"])

        before_enable = timezone.now()
        enable_manifest = f"""
kind: zone
metadata:
  ref: zone@{self.zone.relative_id}
  name: Starting Zone
spec:
  door_reset:
    mode: fixed
    seconds: 90
"""
        enable_resp = self.client.post(
            self.apply_ep,
            {"manifest": enable_manifest},
            format="json",
        )

        self.assertEqual(enable_resp.status_code, 200, enable_resp.data)
        self.zone.refresh_from_db()
        door_schedule.refresh_from_db()
        self.assertEqual(
            self.zone.door_reset_policy_version,
            disabled_policy_version + 1,
        )
        self.assertLess(door_schedule.next_reset_ts, before_enable)
        self.assertEqual(door_schedule.policy_version, original_policy_version)

        output = run_spawn_plans_for_world(world=spawn_world)

        door_schedule.refresh_from_db()
        door_state.refresh_from_db()
        self.assertGreaterEqual(
            door_schedule.next_reset_ts,
            before_enable + timedelta(seconds=90),
        )
        self.assertEqual(
            door_schedule.policy_version,
            self.zone.door_reset_policy_version,
        )
        self.assertEqual(door_state.state, adv_consts.DOOR_STATE_CLOSED)
        self.assertEqual(door_state.revision, 7)
        self.assertEqual(output["doors"], [])

    def test_apply_zone_manifest_refetches_locked_zone_before_saving(self):
        normalize_policy = builder_world_export._normalize_zone_policy
        updated_concurrently = False

        def update_then_normalize(*args, **kwargs):
            nonlocal updated_concurrently
            if not updated_concurrently:
                updated_concurrently = True
                self.world.zones.filter(pk=self.zone.pk).update(
                    description="A concurrent description edit.",
                )
            return normalize_policy(*args, **kwargs)

        manifest = f"""
kind: zone
metadata:
  ref: zone@{self.zone.relative_id}
  name: Starting Zone
spec:
  notes: Applied notes.
  respawn:
    mode: fixed
    seconds: 75
"""
        with mock.patch.object(
            builder_world_export,
            "_normalize_zone_policy",
            side_effect=update_then_normalize,
        ):
            resp = self.client.post(
                self.apply_ep,
                {"manifest": manifest},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.zone.refresh_from_db()
        self.assertEqual(
            self.zone.description,
            "A concurrent description edit.",
        )
        self.assertEqual(self.zone.notes, "Applied notes.")
        self.assertEqual(self.zone.respawn_seconds, 75)

    def test_apply_zone_manifest_can_require_an_existing_target(self):
        manifest = {
            "kind": "zone",
            "metadata": {
                "ref": "zone@999999",
                "name": "Missing Zone",
            },
            "spec": {
                "door_reset": {"mode": "none"},
            },
        }
        original_zone_count = self.world.zones.count()

        with self.assertRaisesRegex(
            serializers.ValidationError,
            "target does not exist",
        ):
            builder_world_export.apply_zone_manifest(
                world=self.world,
                manifest=manifest,
                require_existing=True,
            )

        self.assertEqual(self.world.zones.count(), original_zone_count)

    def test_world_export_canonicalizes_legacy_numeric_spawn_source(self):
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="numeric-source",
            name="Numeric Source",
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="practice-dummy",
            source=f"mobdefinition.{self.mob_definition.id}",
            target_room=self.room,
        )

        documents = builder_world_export.serialize_world_documents(self.world)
        exported = next(
            document
            for document in documents
            if document["kind"] == "spawnplan"
            and document["metadata"]["slug"] == plan.slug
        )

        self.assertEqual(
            exported["spec"]["entries"][0]["source"],
            f"mobdefinition.{self.mob_definition.slug}",
        )

    def test_world_export_rejects_stale_spawn_source(self):
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="stale-source",
            name="Stale Source",
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="missing-mob",
            source="mobdefinition.missing-mob",
            target_room=self.room,
        )

        with self.assertRaises(serializers.ValidationError) as raised:
            builder_world_export.serialize_world_documents(self.world)

        self.assertIn(
            "Spawn plan 'stale-source' entry 'missing-mob' source",
            str(raised.exception),
        )
        self.assertIn(
            "does not resolve to authored content",
            str(raised.exception),
        )

    def test_world_export_emits_scalar_spawn_targets(self):
        path = Path.objects.create(
            world=self.world,
            zone=self.zone,
            name="Training Route",
            entry_room=self.room,
        )
        PathRoom.objects.create(path=path, room=self.room)
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="scalar-targets",
            name="Scalar Targets",
        )
        room_entry = SpawnEntry.objects.create(
            plan=plan,
            slug="room-target",
            order=1,
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_room=self.room,
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="zone-target",
            order=2,
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_zone=self.zone,
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="path-target",
            order=3,
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_path=path,
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="entry-target",
            order=4,
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_entry=room_entry,
        )

        documents = builder_world_export.serialize_world_documents(self.world)
        exported = next(
            document
            for document in documents
            if document["kind"] == "spawnplan"
            and document["metadata"]["slug"] == plan.slug
        )
        targets = {
            entry["slug"]: entry["target"]
            for entry in exported["spec"]["entries"]
        }

        self.assertEqual(targets["room-target"], f"room@{self.room.relative_id}")
        self.assertEqual(targets["zone-target"], f"zone@{self.zone.relative_id}")
        self.assertEqual(targets["path-target"], f"path@{path.relative_id}")
        self.assertEqual(targets["entry-target"], "entry.room-target")

    def test_spawn_plan_export_rejects_invalid_entry_dependencies(self):
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="dependency-validation",
        )
        parent = SpawnEntry.objects.create(
            plan=plan,
            slug="parent",
            order=1,
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_room=self.room,
        )
        child = SpawnEntry.objects.create(
            plan=plan,
            slug="child",
            order=2,
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_entry=parent,
        )
        other_plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="other-plan",
        )
        outside_parent = SpawnEntry.objects.create(
            plan=other_plan,
            slug="outside-parent",
            order=0,
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_room=self.room,
        )

        child.target_entry = outside_parent
        child.save(update_fields=["target_entry"])
        with self.assertRaisesRegex(serializers.ValidationError, "outside its spawn plan"):
            builder_world_export.serialize_spawn_plan_payload(plan)

        child.target_entry = parent
        child.order = 1
        child.save(update_fields=["target_entry", "order"])
        with self.assertRaisesRegex(serializers.ValidationError, "lower order"):
            builder_world_export.serialize_spawn_plan_payload(plan)

        child.order = 2
        child.save(update_fields=["order"])
        parent.is_active = False
        parent.save(update_fields=["is_active"])
        with self.assertRaisesRegex(serializers.ValidationError, "active entry"):
            builder_world_export.serialize_spawn_plan_payload(plan)

    def test_apply_spawn_plan_manifest_creates_plan_and_entries(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: training-grounds
  name: Training Grounds
spec:
  zone: zone@{self.zone.relative_id}
  respawn:
    mode: fixed
    seconds: 0
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      count: 1
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["kind"], "spawnplan")
        plan = SpawnPlan.objects.get(world=self.world, slug="training-grounds")
        self.assertEqual(plan.zone, self.zone)
        self.assertEqual(plan.respawn_policy["seconds"], 0)
        entry = plan.entries.get(slug="practice-dummy")
        self.assertEqual(entry.source, "mobdefinition.practice-dummy")
        self.assertEqual(entry.target_room, self.room)
        self.assertIsNone(entry.target_zone)
        self.assertIsNone(entry.target_path)
        self.assertIsNone(entry.target_entry)
        self.assertEqual(entry.count, 1)
        self.assertEqual(
            resp.data["spawn_plan"]["manifest"]["spec"]["entries"][0]["target"],
            f"room@{self.room.relative_id}",
        )

    def test_spawn_plan_manifest_round_trips_nullable_default_roam_chance(self):
        for index, (value, yaml_value) in enumerate(
            ((0, "0"), (10, "'10'"), (100, "100"), (None, "null")),
        ):
            with self.subTest(value=value):
                slug = f"roaming-default-{index}"
                manifest = f"""
kind: spawnplan
metadata:
  slug: {slug}
  name: Roaming Default {index}
spec:
  zone: zone@{self.zone.relative_id}
  default_roam_chance: {yaml_value}
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target: room@{self.room.relative_id}
      count: 1
"""

                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 201, resp.data)
                plan = SpawnPlan.objects.get(world=self.world, slug=slug)
                self.assertEqual(plan.default_roam_chance, value)
                self.assertEqual(
                    resp.data["spawn_plan"]["manifest"]["spec"][
                        "default_roam_chance"
                    ],
                    value,
                )

    def test_spawn_plan_manifest_omitted_default_roam_chance_resets_to_null(self):
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="replace-roaming-default",
            name="Replace Roaming Default",
            default_roam_chance=37,
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="practice-dummy",
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_room=self.room,
            count=1,
        )
        manifest = f"""
kind: spawnplan
metadata:
  slug: {plan.slug}
  name: Replace Roaming Default
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target: room@{self.room.relative_id}
      count: 1
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        plan.refresh_from_db()
        self.assertIsNone(plan.default_roam_chance)
        self.assertIsNone(
            resp.data["spawn_plan"]["manifest"]["spec"][
                "default_roam_chance"
            ]
        )

    def test_spawn_plan_manifest_rejects_invalid_default_roam_chance(self):
        invalid_values = ("-1", "101", "1.5", "true", "'often'")

        for index, yaml_value in enumerate(invalid_values):
            with self.subTest(value=yaml_value):
                slug = f"invalid-roaming-default-{index}"
                manifest = f"""
kind: spawnplan
metadata:
  slug: {slug}
  name: Invalid Roaming Default {index}
spec:
  zone: zone@{self.zone.relative_id}
  default_roam_chance: {yaml_value}
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target: room@{self.room.relative_id}
      count: 1
"""

                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 400, resp.data)
                self.assertIn("default_roam_chance", str(resp.data))
                self.assertFalse(
                    SpawnPlan.objects.filter(
                        world=self.world,
                        slug=slug,
                    ).exists()
                )

    def test_apply_spawn_plan_manifest_accepts_all_scalar_target_types(self):
        path = Path.objects.create(
            world=self.world,
            zone=self.zone,
            name="Training Route",
            entry_room=self.room,
        )
        PathRoom.objects.create(path=path, room=self.room)
        manifest = f"""
kind: spawnplan
metadata:
  slug: scalar-targets
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: room-target
      order: 1
      source: mobdefinition.{self.mob_definition.slug}
      target: room@{self.room.relative_id}
    - slug: zone-target
      order: 2
      source: mobdefinition.{self.mob_definition.slug}
      target: zone@{self.zone.relative_id}
    - slug: path-target
      order: 3
      source: mobdefinition.{self.mob_definition.slug}
      target: path@{path.relative_id}
    - slug: entry-target
      order: 4
      source: mobdefinition.{self.mob_definition.slug}
      target: entry.room-target
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        plan = SpawnPlan.objects.get(world=self.world, slug="scalar-targets")
        room_entry = plan.entries.get(slug="room-target")
        self.assertEqual(room_entry.target_room, self.room)
        self.assertEqual(plan.entries.get(slug="zone-target").target_zone, self.zone)
        self.assertEqual(plan.entries.get(slug="path-target").target_path, path)
        self.assertEqual(
            plan.entries.get(slug="entry-target").target_entry,
            room_entry,
        )
        exported_targets = {
            entry["slug"]: entry["target"]
            for entry in resp.data["spawn_plan"]["manifest"]["spec"]["entries"]
        }
        self.assertEqual(
            exported_targets,
            {
                "room-target": f"room@{self.room.relative_id}",
                "zone-target": f"zone@{self.zone.relative_id}",
                "path-target": f"path@{path.relative_id}",
                "entry-target": "entry.room-target",
            },
        )

    def test_spawn_plan_rejects_quoted_numeric_room_target(self):
        Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="123",
            x=123,
            y=0,
            z=0,
        )
        manifest = f"""
kind: spawnplan
metadata:
  slug: numeric-room-target
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target: "123"
"""

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("ambiguous bare numeric", str(response.data))

    def test_apply_spawn_plan_manifest_rejects_conflicting_legacy_targets(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: conflicting-targets
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.relative_id}
        zone: zone@{self.zone.relative_id}
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("exactly one", str(resp.data))
        self.assertFalse(
            SpawnPlan.objects.filter(
                world=self.world,
                slug="conflicting-targets",
            ).exists()
        )

    def test_apply_spawn_plan_manifest_requires_exactly_one_target(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: missing-target
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("target must be", str(resp.data))
        self.assertFalse(
            SpawnPlan.objects.filter(
                world=self.world,
                slug="missing-target",
            ).exists()
        )

    def test_apply_spawn_plan_manifest_rejects_invalid_respawn_policies(self):
        invalid_policies = (
            (
                "  respawn:\n    mode: never",
                "spec.respawn.mode must be one of",
            ),
            (
                "  respawn: never",
                "spec.respawn must be a mapping",
            ),
            (
                "  respawn:\n    mode: fixed\n    seconds: -1",
                "spec.respawn.seconds must be a non-negative integer",
            ),
            (
                "  respawn:\n    mode: fixed\n    seconds: 1.5",
                "spec.respawn.seconds must be a non-negative integer",
            ),
            (
                "  respawn:\n    mode: none\n    seconds: 60",
                "spec.respawn.seconds is not supported when mode is none",
            ),
            (
                "  respawn:\n    mode: fixed\n    delay: 60",
                "spec.respawn has unsupported field",
            ),
        )

        for index, (respawn_yaml, expected_error) in enumerate(
            invalid_policies,
        ):
            slug = f"invalid-respawn-{index}"
            manifest = f"""
kind: spawnplan
metadata:
  slug: {slug}
spec:
  zone: zone@{self.zone.relative_id}
{respawn_yaml}
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
"""

            with self.subTest(respawn_yaml=respawn_yaml):
                resp = self.client.post(
                    self.apply_ep,
                    {"manifest": manifest},
                    format="json",
                )

                self.assertEqual(resp.status_code, 400, resp.data)
                self.assertIn(expected_error, str(resp.data))
                self.assertFalse(
                    SpawnPlan.objects.filter(
                        world=self.world,
                        slug=slug,
                    ).exists()
                )

    def test_apply_spawn_plan_manifest_canonicalizes_respawn_policy(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: inherited-respawn
spec:
  zone: zone@{self.zone.relative_id}
  respawn:
    mode: " InHeRiT_ZoNe "
    seconds: "300"
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
"""

        resp = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        plan = SpawnPlan.objects.get(
            world=self.world,
            slug="inherited-respawn",
        )
        self.assertEqual(
            plan.respawn_policy,
            {"mode": "inherit_zone", "seconds": 300},
        )
        self.assertEqual(
            resp.data["spawn_plan"]["manifest"]["spec"]["respawn"],
            {"mode": "inherit_zone", "seconds": 300},
        )

    def test_spawn_entry_initial_state_round_trips_for_mobs(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: captive-camp
  name: Captive Camp
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: greek-commander
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      count: 1
      initial_state:
        captive: true
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        plan = SpawnPlan.objects.get(world=self.world, slug="captive-camp")
        entry = plan.entries.get(slug="greek-commander")
        self.assertEqual(entry.initial_state, {"captive": True})
        detail_ep = reverse(
            "builder-zone-spawn-plan-detail",
            args=[self.world.pk, self.zone.pk, plan.pk],
        )
        exported = yaml.safe_load(self.client.get(detail_ep).data["yaml"])
        self.assertEqual(
            exported["spec"]["entries"][0]["initial_state"],
            {"captive": True},
        )

    def test_spawn_entry_initial_state_rejects_item_sources(self):
        item = ItemDefinition.objects.create(
            world=self.world,
            slug="iron-key",
            name="Iron Key",
        )
        manifest = f"""
kind: spawnplan
metadata:
  slug: invalid-item-state
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: iron-key
      source: itemdefinition.{item.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      initial_state: {{}}
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("only supported", str(resp.data))

    def test_apply_spawn_plan_manifest_ignores_legacy_reset_key(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: training-grounds
  name: Training Grounds
spec:
  zone: zone@{self.zone.relative_id}
  reset:
    mode: world_start
  respawn:
    mode: fixed
    seconds: 0
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      count: 1
"""

        apply_resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(apply_resp.status_code, 201, apply_resp.data)
        plan = SpawnPlan.objects.get(world=self.world, slug="training-grounds")
        detail_ep = reverse(
            "builder-zone-spawn-plan-detail",
            args=[self.world.pk, self.zone.pk, plan.pk],
        )
        detail_resp = self.client.get(detail_ep)
        exported_manifest = yaml.safe_load(detail_resp.data["yaml"])
        self.assertNotIn("reset", exported_manifest["spec"])

    def test_apply_spawn_plan_manifest_accepts_legacy_target_metadata_but_drops_name(self):
        manifest = f"""
kind: spawnplan
metadata:
  world: world.{self.world.id}
  slug: training-patrols
  name: Training Patrols
spec:
  zone: zone@{self.zone.relative_id}
  zone_ref: zone@{self.zone.relative_id}
  entries:
    - slug: dummy-patrol
      source_pool:
        - ref: mobdefinition.{self.mob_definition.slug}
          weight: 2
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
        room_ref: room.{self.room.id}
        name: {self.room.name}
      count:
        min: 1
        max: 1
      affixes:
        guaranteed:
          - sturdy
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        plan = SpawnPlan.objects.get(world=self.world, slug="training-patrols")
        entry = plan.entries.get(slug="dummy-patrol")
        self.assertEqual(entry.source["pool"][0]["ref"], "mobdefinition.practice-dummy")
        self.assertEqual(entry.source["pool"][0]["weight"], 2)
        self.assertEqual(entry.target_room, self.room)
        exported_entry = resp.data["spawn_plan"]["manifest"]["spec"]["entries"][0]
        self.assertEqual(exported_entry["target"], f"room@{self.room.relative_id}")
        self.assertNotIn("name", exported_entry)
        self.assertEqual(entry.count, {"min": 1, "max": 1})
        self.assertEqual(entry.traits["guaranteed"], [{"key": "sturdy"}])

    def test_apply_spawn_plan_manifest_accepts_traits(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: traited-patrols
  name: Traited Patrols
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: dummy-patrol
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      traits:
        guaranteed:
          - key: resilient
            modifiers:
              resilience_multiplier: 1.5
        chance: 25
        pool:
          - key: enraged
            weight: 2
            modifiers:
              attack_power_multiplier: 1.5
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        entry = SpawnPlan.objects.get(world=self.world, slug="traited-patrols").entries.get()
        self.assertEqual(entry.traits["chance"], 25)
        self.assertEqual(entry.traits["guaranteed"][0]["key"], "resilient")
        self.assertEqual(entry.traits["pool"][0]["key"], "enraged")
        self.assertEqual(entry.traits["pool"][0]["weight"], 2)

    def test_apply_spawn_plan_manifest_accepts_cohort_metadata(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: cohort-patrols
  name: Cohort Patrols
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: patrol-leader
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      cohort: west-patrol
      cohort_role: leader
      cohort_policy: refill_missing
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        entry = SpawnPlan.objects.get(world=self.world, slug="cohort-patrols").entries.get()
        self.assertEqual(entry.placement["cohort"], "west-patrol")
        self.assertEqual(entry.placement["cohort_role"], "leader")
        self.assertEqual(entry.placement["cohort_policy"], "refill_missing")

    def test_apply_spawn_plan_manifest_accepts_cohort_followers_targeting_leader(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: paired-patrols
  name: Paired Patrols
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: patrol-leaders
      order: 1
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      count: 4
      cohort: west-patrol
      cohort_role: leader
    - slug: patrol-followers
      order: 2
      source: mobdefinition.{self.mob_definition.slug}
      target:
        entry: patrol-leaders
      count: 1
      cohort: west-patrol
      cohort_role: follower
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        plan = SpawnPlan.objects.get(world=self.world, slug="paired-patrols")
        leader = plan.entries.get(slug="patrol-leaders")
        follower = plan.entries.get(slug="patrol-followers")
        self.assertEqual(leader.count, 4)
        self.assertEqual(follower.target_entry, leader)
        self.assertEqual(follower.placement["cohort_role"], "follower")

    def test_spawn_plan_manifest_rejects_follower_without_entry_target(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: detached-followers
  name: Detached Followers
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: patrol-follower
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      cohort: west-patrol
      cohort_role: follower
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("cohort_role follower requires target.entry", str(resp.data))
        self.assertFalse(SpawnPlan.objects.filter(world=self.world, slug="detached-followers").exists())

    def test_spawn_plan_manifest_rejects_entry_target_after_child(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: late-parent
  name: Late Parent
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: child
      order: 1
      source: mobdefinition.{self.mob_definition.slug}
      target:
        entry: parent
    - slug: parent
      order: 2
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("earlier entry", str(resp.data))
        self.assertFalse(SpawnPlan.objects.filter(world=self.world, slug="late-parent").exists())

    def test_spawn_plan_manifest_rejects_entry_target_to_inactive_parent(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: inactive-parent
  name: Inactive Parent
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: parent
      order: 1
      is_active: false
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
    - slug: child
      order: 2
      source: mobdefinition.{self.mob_definition.slug}
      target:
        entry: parent
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("must reference an active entry", str(resp.data))
        self.assertFalse(SpawnPlan.objects.filter(world=self.world, slug="inactive-parent").exists())

    def test_spawn_plan_manifest_rejects_follower_cohort_mismatch(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: mismatched-cohort
  name: Mismatched Cohort
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: patrol-leader
      order: 1
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      cohort: west-patrol
      cohort_role: leader
    - slug: patrol-follower
      order: 2
      source: mobdefinition.{self.mob_definition.slug}
      target:
        entry: patrol-leader
      cohort: east-patrol
      cohort_role: follower
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("cohort must match", str(resp.data))
        self.assertFalse(SpawnPlan.objects.filter(world=self.world, slug="mismatched-cohort").exists())

    def test_spawn_plan_manifest_rejects_multiple_leader_entries_for_same_cohort(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: duplicate-leaders
  name: Duplicate Leaders
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: west-leader
      order: 1
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      cohort: west-patrol
      cohort_role: leader
    - slug: east-leader
      order: 2
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      cohort: west-patrol
      cohort_role: leader
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("more than one leader entry", str(resp.data))
        self.assertFalse(SpawnPlan.objects.filter(world=self.world, slug="duplicate-leaders").exists())

    def test_spawn_plan_manifest_rejects_traits_and_affixes_together(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: confused-patrols
  name: Confused Patrols
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: dummy-patrol
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      traits:
        guaranteed: [resilient]
      affixes:
        guaranteed: [armored]
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(SpawnPlan.objects.filter(world=self.world, slug="confused-patrols").exists())

    def test_apply_spawn_plan_manifest_rejects_zone_names(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: ambiguous-zone
  name: Ambiguous Zone
spec:
  zone: {self.zone.name}
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      count: 1
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(SpawnPlan.objects.filter(world=self.world, slug="ambiguous-zone").exists())

    def test_apply_spawn_plan_manifest_replaces_entry_list(self):
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="training-grounds",
            name="Training Grounds",
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="old-entry",
            source="mobdefinition.practice-dummy",
            target_room=self.room,
            count=1,
        )
        manifest = f"""
kind: spawnplan
metadata:
  slug: training-grounds
  name: Training Grounds
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: new-entry
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      count: 1
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 200)
        plan.refresh_from_db()
        self.assertFalse(plan.entries.filter(slug="old-entry").exists())
        self.assertTrue(plan.entries.filter(slug="new-entry").exists())

    def test_spawn_plan_manifest_rejects_unknown_source(self):
        manifest = f"""
kind: spawnplan
metadata:
  slug: broken-plan
  name: Broken Plan
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: missing-source
      source: mobdefinition.missing
      target:
        room: room@{self.room.x},{self.room.y},{self.room.z}
      count: 1
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(SpawnPlan.objects.filter(world=self.world, slug="broken-plan").exists())

    def test_apply_spawn_plan_manifest_on_instance_resolves_base_world_source(self):
        instance_template = World.objects.new_world(
            name="Training Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            is_multiplayer=True,
            instance_of=self.world,
        )
        instance_zone = instance_template.zones.get()
        instance_room = instance_zone.rooms.get()
        apply_ep = reverse("builder-world-manifest-apply", args=[instance_template.pk])
        manifest = f"""
kind: spawnplan
metadata:
  slug: instance-training-grounds
  name: Instance Training Grounds
spec:
  zone: zone@{instance_zone.relative_id}
  respawn:
    mode: none
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target:
        room: room@{instance_room.x},{instance_room.y},{instance_room.z}
      count: 1
"""

        resp = self.client.post(apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(
            MobDefinition.objects.filter(
                world=instance_template,
                slug=self.mob_definition.slug,
            ).exists()
        )
        plan = SpawnPlan.objects.get(world=instance_template, slug="instance-training-grounds")
        self.assertEqual(plan.zone, instance_zone)
        entry = plan.entries.get(slug="practice-dummy")
        self.assertEqual(entry.source, "mobdefinition.practice-dummy")
        self.assertEqual(entry.target_room, instance_room)

    def test_apply_spawn_plan_manifest_accepts_path_ref_target(self):
        path = Path.objects.create(
            world=self.world,
            zone=self.zone,
            name="Patrol Loop",
        )
        PathRoom.objects.create(path=path, room=self.room)
        manifest = f"""
kind: spawnplan
metadata:
  slug: path-plan
  name: Path Plan
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target:
        path: path@{path.relative_id}
      count: 1
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        plan = SpawnPlan.objects.get(world=self.world, slug="path-plan")
        self.assertEqual(plan.entries.get().target_path, path)

    def test_apply_spawn_plan_manifest_rejects_path_names(self):
        Path.objects.create(
            world=self.world,
            zone=self.zone,
            name="Patrol Loop",
        )
        manifest = f"""
kind: spawnplan
metadata:
  slug: named-path-plan
  name: Named Path Plan
spec:
  zone: zone@{self.zone.relative_id}
  entries:
    - slug: practice-dummy
      source: mobdefinition.{self.mob_definition.slug}
      target:
        path: Patrol Loop
      count: 1
"""

        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(SpawnPlan.objects.filter(world=self.world, slug="named-path-plan").exists())

    def test_zone_spawn_plans_endpoint_lists_spawn_plans(self):
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="training-grounds",
            name="Training Grounds",
            respawn_policy={"mode": "fixed", "seconds": 60},
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="practice-dummy",
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_room=self.room,
            count=1,
        )
        list_ep = reverse("builder-zone-spawn-plans", args=[self.world.pk, self.zone.pk])

        resp = self.client.get(list_ep)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(len(resp.data["spawn_plans"]), 1)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(len(resp.data["results"]), 1)
        payload = resp.data["spawn_plans"][0]
        self.assertEqual(payload["id"], plan.id)
        self.assertEqual(payload["slug"], "training-grounds")
        self.assertEqual(payload["zone_ref"], f"zone@{self.zone.relative_id}")
        self.assertEqual(payload["num_entries"], 1)
        self.assertNotIn("yaml", payload)

    def test_zone_spawn_plan_detail_returns_manifest_yaml(self):
        plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="training-grounds",
            name="Training Grounds",
            respawn_policy={"mode": "fixed", "seconds": 60},
        )
        detail_ep = reverse(
            "builder-zone-spawn-plan-detail",
            args=[self.world.pk, self.zone.pk, plan.pk],
        )

        resp = self.client.get(detail_ep)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["id"], plan.id)
        self.assertEqual(resp.data["slug"], "training-grounds")
        self.assertIn("kind: spawnplan", resp.data["yaml"])
        self.assertIn("operation: delete", resp.data["delete_yaml"])
        manifest = yaml.safe_load(resp.data["yaml"])
        self.assertNotIn("reset", manifest["spec"])


class TestSpawnPlanRuntime(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-dummy",
            name="a practice dummy",
            mob_type=adv_consts.MOB_TYPE_CONSTRUCT,
            base_properties={"health_max": 10},
        )
        self.plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="training-grounds",
            name="Training Grounds",
            respawn_policy={"mode": "fixed", "seconds": 0},
        )
        self.entry = SpawnEntry.objects.create(
            plan=self.plan,
            slug="practice-dummy",
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_room=self.room,
            count=1,
            traits={
                "guaranteed": ["sturdy"],
                "chance": 100,
                "pool": [
                    {
                        "key": "armored",
                        "weight": 1,
                        "modifiers": {
                            "armor": 2,
                            "health_max_multiplier": 1.5,
                        },
                    }
                ],
            },
        )

    def test_world_start_runs_spawn_plans(self):
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()

        mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(mob.room, self.room)
        self.assertIsNotNone(mob.spawn_placement)
        self.assertEqual(
            mob.roll_metadata["spawn_plan"]["trait_keys"],
            ["sturdy", "armored"],
        )
        self.assertEqual(
            [trait["key"] for trait in mob.roll_metadata["spawn_plan"]["traits"]],
            ["sturdy", "armored"],
        )
        self.assertEqual(
            mob.roll_metadata["spawn_plan"]["modifiers"],
            {
                "armor": 2,
                "health_max_multiplier": 1.5,
            },
        )

        self.assertEqual(mob.armor, 2)
        self.assertEqual(mob.health_max, 15)
        self.assertEqual(mob.health, 15)
        self.assertEqual(
            [trait["key"] for trait in mob.trait_instances],
            ["sturdy", "armored"],
        )
        self.assertEqual(
            {trait["key"]: trait["source"] for trait in mob.trait_instances},
            {"sturdy": "spawn_plan", "armored": "spawn_plan"},
        )
        run = SpawnPlanRun.objects.get(spawn_world=spawn_world, plan=self.plan)
        self.assertEqual(run.placements.count(), 1)
        placement = run.placements.get()
        self.assertEqual(placement.entry_slug, self.entry.slug)
        self.assertEqual(placement.room, self.room)
        self.assertEqual(
            [trait["key"] for trait in placement.traits],
            ["sturdy", "armored"],
        )

    def _create_test_doorway(self):
        destination = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Door Reset Annex",
            x=99,
            y=0,
            z=0,
        )
        doorway = Doorway.objects.create(
            world=self.world,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=doorway,
            direction="east",
            from_room=self.room,
            to_room=destination,
            name="test gate",
        )
        return doorway

    def test_inherit_zone_without_seconds_honors_zone_none_policy(self):
        self.plan.respawn_policy = {"mode": "inherit_zone"}
        self.plan.save(update_fields=["respawn_policy"])
        self.zone.respawn_mode = "none"
        self.zone.respawn_seconds = None
        self.zone.save(update_fields=["respawn_mode", "respawn_seconds"])
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        Mob.objects.get(world=spawn_world, definition=self.mob_definition).delete()

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 0)
        self.assertFalse(
            Mob.objects.filter(
                world=spawn_world,
                definition=self.mob_definition,
            ).exists()
        )

    def test_inherit_zone_explicit_seconds_overrides_zone_none_policy(self):
        self.plan.respawn_policy = {
            "mode": "inherit_zone",
            "seconds": 0,
        }
        self.plan.save(update_fields=["respawn_policy"])
        self.zone.respawn_mode = "none"
        self.zone.respawn_seconds = None
        self.zone.save(update_fields=["respawn_mode", "respawn_seconds"])
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        Mob.objects.get(world=spawn_world, definition=self.mob_definition).delete()

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 1)
        self.assertTrue(
            Mob.objects.filter(
                world=spawn_world,
                definition=self.mob_definition,
            ).exists()
        )

    def test_door_reset_none_preserves_runtime_override(self):
        self.zone.door_reset_mode = "none"
        self.zone.door_reset_seconds = None
        self.zone.save(
            update_fields=["door_reset_mode", "door_reset_seconds"],
        )
        doorway = self._create_test_doorway()
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        door_state = DoorState.objects.create(
            world=spawn_world,
            doorway=doorway,
            state=adv_consts.DOOR_STATE_CLOSED,
            revision=4,
        )

        with CaptureQueriesContext(connection) as captured:
            first_output = run_spawn_plans_for_world(world=spawn_world)
            second_output = run_spawn_plans_for_world(world=spawn_world)

        door_state.refresh_from_db()
        self.assertEqual(door_state.state, adv_consts.DOOR_STATE_CLOSED)
        self.assertEqual(door_state.revision, 4)
        self.assertEqual(first_output["doors"], [])
        self.assertEqual(second_output["doors"], [])
        schedule_writes = [
            query["sql"]
            for query in captured.captured_queries
            if "worlds_zonedoorresetschedule" in query["sql"].lower()
            and query["sql"].lstrip().upper().startswith(
                ("INSERT", "UPDATE", "DELETE")
            )
        ]
        self.assertEqual(schedule_writes, [])
        self.assertFalse(
            ZoneDoorResetSchedule.objects.filter(
                world=spawn_world,
                zone=self.zone,
                next_reset_ts__isnull=False,
            ).exists()
        )

    def test_due_poll_rechecks_locked_zone_policy_before_resetting(self):
        doorway = self._create_test_doorway()
        spawn_world = self.world.create_spawn_world()
        run_spawn_plans_for_world(world=spawn_world, initial=True)
        door_state = DoorState.objects.create(
            world=spawn_world,
            doorway=doorway,
            state=adv_consts.DOOR_STATE_CLOSED,
            revision=6,
        )
        schedule = ZoneDoorResetSchedule.objects.get(
            world=spawn_world,
            zone=self.zone,
        )
        schedule.next_reset_ts = timezone.now() - timedelta(seconds=1)
        schedule.save(update_fields=["next_reset_ts"])
        real_now = timezone.now
        first_now = True

        def change_policy_before_lock():
            nonlocal first_now
            if first_now:
                first_now = False
                self.world.zones.filter(pk=self.zone.pk).update(
                    door_reset_mode="none",
                    door_reset_seconds=None,
                    door_reset_policy_version=(
                        self.zone.door_reset_policy_version + 1
                    ),
                )
            return real_now()

        with mock.patch(
            "spawns.loading.timezone.now",
            side_effect=change_policy_before_lock,
        ):
            output = run_spawn_plans_for_world(world=spawn_world)

        door_state.refresh_from_db()
        schedule.refresh_from_db()
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.door_reset_mode, "none")
        self.assertEqual(door_state.state, adv_consts.DOOR_STATE_CLOSED)
        self.assertEqual(door_state.revision, 6)
        self.assertLess(schedule.next_reset_ts, timezone.now())
        self.assertEqual(output["doors"], [])

    def test_fixed_door_reset_schedule_is_isolated_per_runtime_world(self):
        self.zone.door_reset_mode = "fixed"
        self.zone.door_reset_seconds = 60
        self.zone.save(
            update_fields=["door_reset_mode", "door_reset_seconds"],
        )
        doorway = self._create_test_doorway()
        first_world = self.world.create_spawn_world()
        second_world = self.world.create_spawn_world()
        WorldSmith(first_world).start()
        WorldSmith(second_world).start()
        first_door_state = DoorState.objects.create(
            world=first_world,
            doorway=doorway,
            state=adv_consts.DOOR_STATE_CLOSED,
            revision=4,
        )
        second_door_state = DoorState.objects.create(
            world=second_world,
            doorway=doorway,
            state=adv_consts.DOOR_STATE_CLOSED,
            revision=9,
        )
        first_schedule = ZoneDoorResetSchedule.objects.get(
            world=first_world,
            zone=self.zone,
        )
        second_schedule = ZoneDoorResetSchedule.objects.get(
            world=second_world,
            zone=self.zone,
        )
        first_schedule.next_reset_ts = timezone.now() - timedelta(
            seconds=1,
        )
        first_schedule.save(update_fields=["next_reset_ts"])
        second_deadline = timezone.now() + timedelta(hours=1)
        second_schedule.next_reset_ts = second_deadline
        second_schedule.save(update_fields=["next_reset_ts"])

        output = run_spawn_plans_for_world(world=first_world)

        first_door_state.refresh_from_db()
        second_door_state.refresh_from_db()
        first_schedule.refresh_from_db()
        second_schedule.refresh_from_db()
        self.assertEqual(first_door_state.state, adv_consts.DOOR_STATE_OPEN)
        self.assertEqual(first_door_state.revision, 5)
        self.assertEqual(second_door_state.state, adv_consts.DOOR_STATE_CLOSED)
        self.assertEqual(second_door_state.revision, 9)
        self.assertEqual(len(output["doors"]), 1)
        self.assertGreater(first_schedule.next_reset_ts, timezone.now())
        self.assertEqual(second_schedule.next_reset_ts, second_deadline)

    def test_cross_zone_doorway_resets_once_when_both_zones_are_due(self):
        other_zone = self.world.zones.create(name="Boundary Zone")
        other_room = Room.objects.create(
            world=self.world,
            zone=other_zone,
            name="Boundary Room",
            x=1,
            y=0,
            z=0,
        )
        doorway = Doorway.objects.create(
            world=self.world,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=doorway,
            direction="east",
            from_room=self.room,
            to_room=other_room,
            name="boundary gate",
        )
        Door.objects.create(
            doorway=doorway,
            direction="west",
            from_room=other_room,
            to_room=self.room,
            name="boundary gate",
        )
        spawn_world = self.world.create_spawn_world()
        run_spawn_plans_for_world(world=spawn_world, initial=True)
        door_state = DoorState.objects.create(
            world=spawn_world,
            doorway=doorway,
            state=adv_consts.DOOR_STATE_CLOSED,
            revision=11,
        )
        schedules = ZoneDoorResetSchedule.objects.filter(
            world=spawn_world,
            zone__in=[self.zone, other_zone],
        )
        self.assertEqual(schedules.count(), 2)
        schedules.update(next_reset_ts=timezone.now() - timedelta(seconds=1))

        output = run_spawn_plans_for_world(world=spawn_world)

        door_state.refresh_from_db()
        self.assertEqual(door_state.state, adv_consts.DOOR_STATE_OPEN)
        self.assertEqual(door_state.revision, 12)
        self.assertEqual(len(output["doors"]), 1)
        self.assertTrue(
            all(
                deadline > timezone.now()
                for deadline in schedules.values_list(
                    "next_reset_ts",
                    flat=True,
                )
            )
        )

    def test_one_sided_cross_zone_doorway_resets_when_destination_zone_is_due(self):
        other_zone = self.world.zones.create(name="One-Sided Boundary Zone")
        other_room = Room.objects.create(
            world=self.world,
            zone=other_zone,
            name="One-Sided Boundary Room",
            x=2,
            y=0,
            z=0,
        )
        doorway = Doorway.objects.create(
            world=self.world,
            default_state=adv_consts.DOOR_STATE_OPEN,
        )
        Door.objects.create(
            doorway=doorway,
            direction="east",
            from_room=self.room,
            to_room=other_room,
            name="one-sided boundary gate",
        )
        spawn_world = self.world.create_spawn_world()
        run_spawn_plans_for_world(world=spawn_world, initial=True)
        door_state = DoorState.objects.create(
            world=spawn_world,
            doorway=doorway,
            state=adv_consts.DOOR_STATE_CLOSED,
            revision=14,
        )
        now = timezone.now()
        ZoneDoorResetSchedule.objects.filter(
            world=spawn_world,
            zone=self.zone,
        ).update(next_reset_ts=now + timedelta(hours=1))
        ZoneDoorResetSchedule.objects.filter(
            world=spawn_world,
            zone=other_zone,
        ).update(next_reset_ts=now - timedelta(seconds=1))

        output = run_spawn_plans_for_world(world=spawn_world)

        door_state.refresh_from_db()
        self.assertEqual(door_state.state, adv_consts.DOOR_STATE_OPEN)
        self.assertEqual(door_state.revision, 15)
        self.assertEqual(len(output["doors"]), 1)
        self.assertEqual(output["doors"][0]["name"], "one-sided boundary gate")

    def test_fixed_door_schedules_use_one_steady_state_batch_read(self):
        for index in range(8):
            self.world.zones.create(name=f"Schedule Zone {index}")
        spawn_world = self.world.create_spawn_world()
        run_spawn_plans_for_world(world=spawn_world, initial=True)

        with CaptureQueriesContext(connection) as captured:
            run_spawn_plans_for_world(world=spawn_world)

        schedule_queries = [
            query["sql"]
            for query in captured.captured_queries
            if "worlds_zonedoorresetschedule" in query["sql"].lower()
        ]
        self.assertEqual(
            len(schedule_queries),
            1,
            "\n".join(schedule_queries),
        )
        self.assertTrue(
            schedule_queries[0].lstrip().upper().startswith("SELECT"),
            schedule_queries[0],
        )
        self.assertNotIn("FOR UPDATE", schedule_queries[0].upper())

    def test_initial_door_schedules_are_batched_in_one_transaction(self):
        for index in range(8):
            self.world.zones.create(name=f"Initial Schedule Zone {index}")
        spawn_world = self.world.create_spawn_world()

        with CaptureQueriesContext(connection) as captured:
            run_spawn_plans_for_world(world=spawn_world, initial=True)

        schedule_queries = [
            query["sql"]
            for query in captured.captured_queries
            if "worlds_zonedoorresetschedule" in query["sql"].lower()
        ]
        self.assertEqual(
            len(schedule_queries),
            2,
            "\n".join(schedule_queries),
        )
        self.assertEqual(
            sum("FOR UPDATE" in query.upper() for query in schedule_queries),
            1,
            "\n".join(schedule_queries),
        )
        zone_lock_queries = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "worlds_zone"' in query["sql"]
            and "FOR UPDATE" in query["sql"].upper()
        ]
        self.assertEqual(
            len(zone_lock_queries),
            1,
            "\n".join(zone_lock_queries),
        )

    def test_initial_door_schedules_use_bounded_transactions(self):
        for index in range(8):
            self.world.zones.create(name=f"Bounded Initial Zone {index}")
        spawn_world = self.world.create_spawn_world()

        with (
            mock.patch(
                "spawns.loading.DOOR_RESET_SCHEDULE_BATCH_SIZE",
                4,
            ),
            mock.patch(
                "spawns.spawn_plans.run_spawn_plans",
                return_value=[],
            ),
            CaptureQueriesContext(connection) as captured,
        ):
            run_spawn_plans_for_world(world=spawn_world, initial=True)

        zone_lock_queries = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "worlds_zone"' in query["sql"]
            and "FOR UPDATE" in query["sql"].upper()
        ]
        schedule_lock_queries = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "worlds_zonedoorresetschedule"' in query["sql"]
            and "FOR UPDATE" in query["sql"].upper()
        ]
        transaction_starts = [
            query["sql"]
            for query in captured.captured_queries
            if query["sql"].lstrip().upper().startswith(
                ("BEGIN", "SAVEPOINT")
            )
        ]
        self.assertEqual(len(zone_lock_queries), 3, "\n".join(zone_lock_queries))
        self.assertEqual(
            len(schedule_lock_queries),
            3,
            "\n".join(schedule_lock_queries),
        )
        self.assertEqual(
            len(transaction_starts),
            3,
            "\n".join(transaction_starts),
        )
        self.assertEqual(
            ZoneDoorResetSchedule.objects.filter(world=spawn_world).count(),
            9,
        )

    def test_initial_batches_start_intervals_after_each_batch_locks(self):
        other_zone = self.world.zones.create(name="Later Initial Zone")
        spawn_world = self.world.create_spawn_world()
        base_time = timezone.now()
        call_count = 0

        def advancing_now():
            nonlocal call_count
            current = base_time + timedelta(seconds=call_count)
            call_count += 1
            return current

        with (
            mock.patch(
                "spawns.loading.DOOR_RESET_SCHEDULE_BATCH_SIZE",
                1,
            ),
            mock.patch(
                "spawns.loading.timezone.now",
                side_effect=advancing_now,
            ),
        ):
            _initialize_door_reset_schedules(
                world=spawn_world,
                zones=[self.zone, other_zone],
            )

        first_deadline = ZoneDoorResetSchedule.objects.get(
            world=spawn_world,
            zone=self.zone,
        ).next_reset_ts
        second_deadline = ZoneDoorResetSchedule.objects.get(
            world=spawn_world,
            zone=other_zone,
        ).next_reset_ts
        self.assertGreater(second_deadline, first_deadline)

    def test_simultaneously_due_door_schedules_use_bounded_transactions(self):
        for index in range(8):
            self.world.zones.create(name=f"Due Schedule Zone {index}")
        spawn_world = self.world.create_spawn_world()
        run_spawn_plans_for_world(world=spawn_world, initial=True)
        ZoneDoorResetSchedule.objects.filter(world=spawn_world).update(
            next_reset_ts=timezone.now() - timedelta(seconds=1),
        )

        with (
            mock.patch(
                "spawns.loading.DOOR_RESET_SCHEDULE_BATCH_SIZE",
                4,
            ),
            mock.patch(
                "spawns.spawn_plans.run_spawn_plans",
                return_value=[],
            ),
            CaptureQueriesContext(connection) as captured,
        ):
            output = run_spawn_plans_for_world(world=spawn_world)

        zone_lock_queries = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "worlds_zone"' in query["sql"]
            and "FOR UPDATE" in query["sql"].upper()
        ]
        schedule_lock_queries = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "worlds_zonedoorresetschedule"' in query["sql"]
            and "FOR UPDATE" in query["sql"].upper()
        ]
        transaction_starts = [
            query["sql"]
            for query in captured.captured_queries
            if query["sql"].lstrip().upper().startswith(
                ("BEGIN", "SAVEPOINT")
            )
        ]
        door_face_queries = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "worlds_door"' in query["sql"]
        ]
        self.assertEqual(len(zone_lock_queries), 3, "\n".join(zone_lock_queries))
        self.assertEqual(
            len(schedule_lock_queries),
            3,
            "\n".join(schedule_lock_queries),
        )
        self.assertEqual(
            len(transaction_starts),
            3,
            "\n".join(transaction_starts),
        )
        self.assertEqual(len(door_face_queries), 3, "\n".join(door_face_queries))
        self.assertEqual(output["doors"], [])

    def test_runtime_rejects_cross_plan_entry_target(self):
        other_plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="other-plan",
        )
        outside_parent = SpawnEntry.objects.create(
            plan=other_plan,
            slug="practice-dummy",
            order=-1,
            target_room=self.room,
        )
        self.entry.target_room = None
        self.entry.target_entry = outside_parent
        self.entry.save(update_fields=["target_room", "target_entry"])

        with self.assertRaisesRegex(serializers.ValidationError, "outside its spawn plan"):
            _target_entry_slug(self.entry)

    def test_entries_sharing_zone_target_reuse_eligible_room_query(self):
        self.entry.target_room = None
        self.entry.target_zone = self.zone
        self.entry.save(update_fields=["target_room", "target_zone"])
        second_entry = SpawnEntry.objects.create(
            plan=self.plan,
            slug="second-dummy",
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_zone=self.zone,
            count=1,
        )
        room_choice_cache = {}

        with self.assertNumQueries(1):
            first_room, _first_state = _choose_room_for_entry(
                world=self.world,
                entry=self.entry,
                source_type="mobdefinition",
                rng=random.Random(1),
                room_choice_cache=room_choice_cache,
            )
            second_room, _second_state = _choose_room_for_entry(
                world=self.world,
                entry=second_entry,
                source_type="mobdefinition",
                rng=random.Random(2),
                room_choice_cache=room_choice_cache,
            )

        self.assertEqual(first_room, self.room)
        self.assertEqual(second_room, self.room)

    def test_world_start_merges_definition_and_spawn_entry_loot(self):
        ItemDefinition.objects.create(
            world=self.world,
            slug="training-token",
            name="a training token",
        )
        ItemDefinition.objects.create(
            world=self.world,
            slug="patrol-badge",
            name="a patrol badge",
        )
        self.mob_definition.loot = {
            "entries": [
                {
                    "slug": "definition-token",
                    "probability": 100,
                    "quantity": 1,
                    "source": "itemdefinition.training-token",
                }
            ]
        }
        self.mob_definition.save(update_fields=["loot"])
        self.entry.loot = {
            "inherit_definition": True,
            "entries": [
                {
                    "slug": "entry-badge",
                    "probability": 100,
                    "quantity": 1,
                    "source": "itemdefinition.patrol-badge",
                }
            ],
        }
        self.entry.save(update_fields=["loot"])
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()

        mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(
            [entry["slug"] for entry in mob.loot["entries"]],
            ["definition-token", "entry-badge"],
        )

    def test_world_start_resolves_path_ref_targets(self):
        path = Path.objects.create(
            world=self.world,
            zone=self.zone,
            name="Patrol Loop",
        )
        PathRoom.objects.create(path=path, room=self.room)
        self.entry.target_room = None
        self.entry.target_path = path
        self.entry.save(update_fields=["target_room", "target_path"])
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()

        mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(mob.room, self.room)
        self.assertEqual(mob.roams, path)
        placement = SpawnPlacement.objects.get(run__plan=self.plan)
        self.assertEqual(placement.room, self.room)

    def test_zone_target_excludes_no_roam_room(self):
        eligible_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="East Yard",
            x=1,
            y=0,
            z=0,
        )
        RoomFlag.objects.create(
            room=self.room,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        self.entry.target_room = None
        self.entry.target_zone = self.zone
        self.entry.count = 8
        self.entry.save(update_fields=["target_room", "target_zone", "count"])
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()

        mobs = Mob.objects.filter(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(mobs.count(), 8)
        self.assertEqual(set(mobs.values_list("room_id", flat=True)), {eligible_room.id})
        self.assertEqual(
            set(SpawnPlacement.objects.filter(run__plan=self.plan).values_list("room_id", flat=True)),
            {eligible_room.id},
        )

    def test_path_target_excludes_no_roam_room(self):
        eligible_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="East Yard",
            x=1,
            y=0,
            z=0,
        )
        path = Path.objects.create(
            world=self.world,
            zone=self.zone,
            name="Patrol Loop",
        )
        PathRoom.objects.create(path=path, room=self.room)
        PathRoom.objects.create(path=path, room=eligible_room)
        RoomFlag.objects.create(
            room=self.room,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        self.entry.target_room = None
        self.entry.target_path = path
        self.entry.count = 8
        self.entry.save(update_fields=["target_room", "target_path", "count"])
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()

        mobs = Mob.objects.filter(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(mobs.count(), 8)
        self.assertEqual(set(mobs.values_list("room_id", flat=True)), {eligible_room.id})
        self.assertEqual(
            set(SpawnPlacement.objects.filter(run__plan=self.plan).values_list("room_id", flat=True)),
            {eligible_room.id},
        )

    def test_path_target_falls_back_when_entry_room_is_no_roam(self):
        eligible_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="East Yard",
            x=1,
            y=0,
            z=0,
        )
        path = Path.objects.create(
            world=self.world,
            zone=self.zone,
            name="Patrol Loop",
            entry_room=self.room,
        )
        PathRoom.objects.create(path=path, room=self.room)
        PathRoom.objects.create(path=path, room=eligible_room)
        RoomFlag.objects.create(
            room=self.room,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        self.entry.target_room = None
        self.entry.target_path = path
        self.entry.save(update_fields=["target_room", "target_path"])
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()

        mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(mob.room, eligible_room)
        self.assertEqual(mob.spawn_placement.room, eligible_room)
        self.assertEqual(mob.roams, path)

    def test_world_start_resolves_zone_ref_targets(self):
        self.entry.target_room = None
        self.entry.target_zone = self.zone
        self.entry.save(update_fields=["target_room", "target_zone"])
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()

        mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(mob.room.zone, self.zone)
        self.assertEqual(mob.roams, self.zone)
        placement = SpawnPlacement.objects.get(run__plan=self.plan)
        self.assertEqual(placement.room.zone, self.zone)

    def test_instance_spawn_plan_runtime_resolves_base_world_source(self):
        instance_template = World.objects.new_world(
            name="Training Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            is_multiplayer=True,
            instance_of=self.world,
        )
        instance_zone = instance_template.zones.get()
        instance_room = instance_zone.rooms.get()
        plan = SpawnPlan.objects.create(
            world=instance_template,
            zone=instance_zone,
            slug="instance-training-grounds",
            name="Instance Training Grounds",
            respawn_policy={"mode": "none"},
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="practice-dummy",
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_room=instance_room,
            count=1,
        )
        spawned_instance = instance_template.create_spawn_world(
            instance_ref="training-instance",
            leader=self.player,
        )

        WorldSmith(spawned_instance).start()

        mob = Mob.objects.get(world=spawned_instance, definition=self.mob_definition)
        self.assertEqual(mob.room, instance_room)
        run = SpawnPlanRun.objects.get(spawn_world=spawned_instance, plan=plan)
        placement = run.placements.get()
        self.assertEqual(placement.source_type, "mobdefinition")
        self.assertEqual(placement.source_id, self.mob_definition.id)

    def test_nested_roaming_mob_does_not_load_with_item_in_no_roam_room(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="training-crate",
            name="a training crate",
        )
        self.entry.source = f"itemdefinition.{item_definition.slug}"
        self.entry.target_room = None
        self.entry.target_zone = self.zone
        self.entry.traits = {}
        self.entry.save(
            update_fields=["source", "target_room", "target_zone", "traits"]
        )
        SpawnEntry.objects.create(
            plan=self.plan,
            slug="crate-guard",
            order=2,
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_entry=self.entry,
            count=1,
        )
        RoomFlag.objects.create(
            room=self.room,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()

        item = Item.objects.get(
            world=spawn_world,
            definition=item_definition,
        )
        self.assertEqual(item.container, self.room)
        self.assertFalse(Mob.objects.filter(world=spawn_world).exists())

    def test_direct_room_target_ignores_no_roam_on_initial_load_and_respawn(self):
        RoomFlag.objects.create(
            room=self.room,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(mob.room, self.room)
        self.assertIsNone(mob.roams)
        mob.delete()

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 1)
        replacement = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(replacement.room, self.room)
        self.assertIsNone(replacement.roams)

    def test_stale_zone_placements_use_one_no_roam_query_and_skip_respawn(self):
        placement_count = 12
        second_placement_count = 3
        self.entry.target_room = None
        self.entry.target_zone = self.zone
        self.entry.count = placement_count
        self.entry.save(update_fields=["target_room", "target_zone", "count"])
        second_plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="training-annex",
            name="Training Annex",
            respawn_policy={"mode": "fixed", "seconds": 0},
        )
        SpawnEntry.objects.create(
            plan=second_plan,
            slug="annex-dummy",
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_zone=self.zone,
            count=second_placement_count,
        )
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        total_placements = placement_count + second_placement_count
        self.assertEqual(Mob.objects.filter(world=spawn_world).count(), total_placements)

        with CaptureQueriesContext(connection) as healthy_captured:
            healthy_output = run_spawn_plans_for_world(world=spawn_world)

        healthy_room_flag_queries = [
            query["sql"]
            for query in healthy_captured.captured_queries
            if "worlds_roomflag" in query["sql"].lower()
        ]
        self.assertEqual(sum(result["spawned"] for result in healthy_output["spawn_plans"]), 0)
        self.assertEqual(healthy_room_flag_queries, [])

        Mob.objects.filter(world=spawn_world).delete()
        no_roam_flag = RoomFlag.objects.create(
            room=self.room,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )

        with CaptureQueriesContext(connection) as captured:
            output = run_spawn_plans_for_world(world=spawn_world)

        room_flag_queries = [
            query["sql"]
            for query in captured.captured_queries
            if "worlds_roomflag" in query["sql"].lower()
        ]
        self.assertEqual(sum(result["spawned"] for result in output["spawn_plans"]), 0)
        self.assertFalse(Mob.objects.filter(world=spawn_world).exists())
        self.assertEqual(len(room_flag_queries), 1, "\n".join(room_flag_queries))
        self.assertEqual(
            SpawnPlacement.objects.filter(run__plan=self.plan).count(),
            placement_count,
        )
        self.assertEqual(
            SpawnPlacement.objects.filter(run__plan=second_plan).count(),
            second_placement_count,
        )

        no_roam_flag.delete()
        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(
            sum(result["spawned"] for result in output["spawn_plans"]),
            total_placements,
        )
        self.assertEqual(Mob.objects.filter(world=spawn_world).count(), total_placements)

    def test_zone_scoped_repop_honors_no_roam_for_cross_zone_target(self):
        target_zone = self.world.zones.create(name="Cross-Zone Target")
        target_room = Room.objects.create(
            world=self.world,
            zone=target_zone,
            name="Cross-Zone Room",
            x=9,
            y=0,
            z=0,
        )
        self.entry.target_room = None
        self.entry.target_zone = target_zone
        self.entry.save(update_fields=["target_room", "target_zone"])
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        Mob.objects.get(
            world=spawn_world,
            definition=self.mob_definition,
        ).delete()
        RoomFlag.objects.create(
            room=target_room,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )

        output = run_spawn_plans(
            world=spawn_world,
            zone_id=self.zone.id,
            repopulate=True,
        )

        self.assertEqual(output[0]["spawned"], 0)
        self.assertFalse(Mob.objects.filter(world=spawn_world).exists())

    def test_spawn_plan_runner_reconciles_missing_spawn_plan_copy(self):
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        first_mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        first_placement = first_mob.spawn_placement

        first_mob.delete()
        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 1)
        replacement = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(replacement.spawn_placement, first_placement)
        self.assertEqual(
            Mob.objects.filter(
                world=spawn_world,
                definition=self.mob_definition,
                is_pending_deletion=False,
            ).count(),
            1,
        )
        self.assertEqual(SpawnPlacement.objects.filter(run__plan=self.plan).count(), 1)

    def test_stale_live_output_cache_rechecks_before_materializing(self):
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        original = Mob.objects.get(
            world=spawn_world,
            definition=self.mob_definition,
        )
        placement = original.spawn_placement
        original.delete()
        reconcile_context = SpawnReconcileContext(
            authored_world_id=self.world.id,
            spawn_world_id=spawn_world.id,
            zone_id=self.zone.id,
        )
        self.assertFalse(
            reconcile_context.placement_has_live_output(placement.id)
        )
        replacement = Mob.objects.create(
            world=spawn_world,
            room=self.room,
            definition=self.mob_definition,
            definition_slug_snapshot=self.mob_definition.slug,
            spawn_placement=placement,
            name=self.mob_definition.name,
        )

        output = run_spawn_plans(
            world=spawn_world,
            zone_id=self.zone.id,
            repopulate=True,
            reconcile_context=reconcile_context,
        )

        self.assertEqual(output[0]["spawned"], 0)
        self.assertEqual(
            list(
                Mob.objects.filter(
                    world=spawn_world,
                    spawn_placement=placement,
                ).values_list("id", flat=True)
            ),
            [replacement.id],
        )

    def test_unknown_persisted_respawn_mode_does_not_refill_missing_spawn(self):
        self.plan.respawn_policy = {"mode": "never"}
        self.plan.save(update_fields=["respawn_policy"])
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        Mob.objects.get(world=spawn_world, definition=self.mob_definition).delete()

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 0)
        self.assertFalse(
            Mob.objects.filter(world=spawn_world, definition=self.mob_definition).exists()
        )

    def test_non_mapping_persisted_respawn_policy_does_not_refill_missing_spawn(self):
        self.plan.respawn_policy = "fixed"
        self.plan.save(update_fields=["respawn_policy"])
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        Mob.objects.get(world=spawn_world, definition=self.mob_definition).delete()

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 0)
        self.assertFalse(
            Mob.objects.filter(world=spawn_world, definition=self.mob_definition).exists()
        )

    def test_running_world_hot_loads_added_entry_without_duplicate(self):
        self.plan.respawn_policy = {"mode": "fixed", "seconds": 3600}
        self.plan.save(update_fields=["respawn_policy"])
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        run = SpawnPlanRun.objects.get(spawn_world=spawn_world, plan=self.plan)
        original_mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        original_placement = original_mob.spawn_placement
        original_hash = run.spec_hash
        archer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-archer",
            name="a practice archer",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"health_max": 10},
        )
        SpawnEntry.objects.create(
            plan=self.plan,
            slug="practice-archer",
            order=2,
            source=f"mobdefinition.{archer_definition.slug}",
            target_room=self.room,
            count=1,
        )

        output = run_spawn_plans_for_world(world=spawn_world)

        run.refresh_from_db()
        original_mob.refresh_from_db()
        self.assertEqual(output["spawn_plans"][0]["spawned"], 1)
        self.assertEqual(run.id, SpawnPlanRun.objects.get(
            spawn_world=spawn_world,
            plan=self.plan,
        ).id)
        self.assertNotEqual(run.spec_hash, original_hash)
        self.assertEqual(original_mob.spawn_placement_id, original_placement.id)
        self.assertEqual(Mob.objects.filter(world=spawn_world).count(), 2)
        self.assertEqual(
            SpawnPlacement.objects.filter(run=run, is_retired=False).count(),
            2,
        )
        self.assertFalse(
            Mob.objects.filter(world=spawn_world, spawn_placement__isnull=True).exists()
        )

        with CaptureQueriesContext(connection) as captured:
            second_output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(second_output["spawn_plans"][0]["spawned"], 0)
        self.assertEqual(Mob.objects.filter(world=spawn_world).count(), 2)
        placement_writes = [
            query["sql"]
            for query in captured.captured_queries
            if "builders_spawnplacement" in query["sql"].lower()
            and query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual(placement_writes, [])

    def test_hot_added_entry_does_not_refill_unrelated_slot_early(self):
        self.plan.respawn_policy = {"mode": "fixed", "seconds": 3600}
        self.plan.save(update_fields=["respawn_policy"])
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        Mob.objects.get(world=spawn_world, definition=self.mob_definition).delete()
        archer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-archer",
            name="a practice archer",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"health_max": 10},
        )
        SpawnEntry.objects.create(
            plan=self.plan,
            slug="practice-archer",
            order=2,
            source=f"mobdefinition.{archer_definition.slug}",
            target_room=self.room,
            count=1,
        )

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 1)
        self.assertFalse(
            Mob.objects.filter(world=spawn_world, definition=self.mob_definition).exists()
        )
        self.assertTrue(
            Mob.objects.filter(world=spawn_world, definition=archer_definition).exists()
        )

    def test_hot_added_entry_does_not_reroll_unrelated_randomized_entry(self):
        Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="East Yard",
            x=1,
            y=0,
            z=0,
        )
        self.plan.randomization = {"seed_scope": "explicit", "seed": "stable-plan"}
        self.plan.respawn_policy = {"mode": "none"}
        self.plan.save(update_fields=["randomization", "respawn_policy"])
        self.entry.target_room = None
        self.entry.target_zone = self.zone
        self.entry.count = {"min": 2, "max": 4}
        self.entry.traits = {
            "chance": 100,
            "pool": [
                {"key": "armored", "weight": 1, "modifiers": {"armor": 2}},
                {"key": "swift", "weight": 1},
            ],
        }
        self.entry.save(
            update_fields=["target_room", "target_zone", "count", "traits"]
        )
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        run = SpawnPlanRun.objects.get(spawn_world=spawn_world, plan=self.plan)
        before = list(
            run.placements.filter(entry_slug=self.entry.slug).order_by("slot_index").values(
                "id",
                "slot_index",
                "room_id",
                "source_type",
                "source_id",
                "traits",
                "modifiers",
                "state",
            )
        )
        archer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-archer",
            name="a practice archer",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"health_max": 10},
        )
        SpawnEntry.objects.create(
            plan=self.plan,
            slug="practice-archer",
            order=-1,
            source=f"mobdefinition.{archer_definition.slug}",
            target_room=self.room,
            count=1,
        )

        output = run_spawn_plans_for_world(world=spawn_world)

        after = list(
            run.placements.filter(entry_slug=self.entry.slug).order_by("slot_index").values(
                "id",
                "slot_index",
                "room_id",
                "source_type",
                "source_id",
                "traits",
                "modifiers",
                "state",
            )
        )
        self.assertEqual(output["spawn_plans"][0]["spawned"], 1)
        self.assertEqual(after, before)

    def test_count_increase_preserves_existing_randomized_slots_and_run_seed(self):
        Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="East Yard",
            x=1,
            y=0,
            z=0,
        )
        alternate_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-archer",
            name="a practice archer",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"health_max": 10},
        )
        self.plan.randomization = {"seed_scope": "explicit", "seed": "stable-plan"}
        self.plan.respawn_policy = {"mode": "none"}
        self.plan.save(update_fields=["randomization", "respawn_policy"])
        self.entry.source = {
            "pool": [
                {"ref": f"mobdefinition.{self.mob_definition.slug}", "weight": 1},
                {"ref": f"mobdefinition.{alternate_definition.slug}", "weight": 1},
            ],
        }
        self.entry.target_room = None
        self.entry.target_zone = self.zone
        self.entry.count = 2
        self.entry.traits = {
            "chance": 100,
            "pool": [
                {"key": "armored", "weight": 1, "modifiers": {"armor": 2}},
                {"key": "swift", "weight": 1},
            ],
        }
        self.entry.save(
            update_fields=[
                "source",
                "target_room",
                "target_zone",
                "count",
                "traits",
            ]
        )
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        run = SpawnPlanRun.objects.get(spawn_world=spawn_world, plan=self.plan)
        original_seed = run.seed
        before = list(
            run.placements.filter(entry_slug=self.entry.slug).order_by("slot_index").values(
                "id",
                "slot_index",
                "room_id",
                "source_type",
                "source_id",
                "traits",
                "modifiers",
                "state",
            )
        )
        Mob.objects.get(
            world=spawn_world,
            spawn_placement_id=before[0]["id"],
        ).delete()

        self.entry.count = 3
        self.entry.save(update_fields=["count"])
        output = run_spawn_plans_for_world(world=spawn_world)

        run.refresh_from_db()
        after = list(
            run.placements.filter(
                entry_slug=self.entry.slug,
                is_retired=False,
            ).order_by("slot_index").values(
                "id",
                "slot_index",
                "room_id",
                "source_type",
                "source_id",
                "traits",
                "modifiers",
                "state",
            )
        )
        self.assertEqual(output["spawn_plans"][0]["spawned"], 1)
        self.assertEqual(run.seed, original_seed)
        self.assertEqual(after[:2], before)
        self.assertEqual(len(after), 3)
        self.assertFalse(
            Mob.objects.filter(
                world=spawn_world,
                spawn_placement_id=before[0]["id"],
            ).exists()
        )
        self.assertTrue(
            Mob.objects.filter(
                world=spawn_world,
                spawn_placement_id=after[2]["id"],
            ).exists()
        )

    def test_hot_added_entry_materializes_once_with_respawn_none(self):
        self.plan.respawn_policy = {"mode": "none"}
        self.plan.save(update_fields=["respawn_policy"])
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        archer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-archer",
            name="a practice archer",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"health_max": 10},
        )
        SpawnEntry.objects.create(
            plan=self.plan,
            slug="practice-archer",
            order=2,
            source=f"mobdefinition.{archer_definition.slug}",
            target_room=self.room,
            count=1,
        )

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 1)
        archer = Mob.objects.get(world=spawn_world, definition=archer_definition)
        archer.delete()

        second_output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(second_output["spawn_plans"][0]["spawned"], 0)
        self.assertFalse(
            Mob.objects.filter(world=spawn_world, definition=archer_definition).exists()
        )

    def test_hot_source_change_waits_for_live_output_to_leave(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="training-token",
            name="a training token",
        )
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        placement_id = mob.spawn_placement_id
        self.entry.source = f"itemdefinition.{item_definition.slug}"
        self.entry.traits = {}
        self.entry.save(update_fields=["source", "traits"])

        output = run_spawn_plans_for_world(world=spawn_world)

        mob.refresh_from_db()
        self.assertEqual(output["spawn_plans"][0]["spawned"], 0)
        self.assertEqual(mob.spawn_placement_id, placement_id)
        self.assertFalse(
            Item.objects.filter(world=spawn_world, definition=item_definition).exists()
        )
        mob.delete()

        second_output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(second_output["spawn_plans"][0]["spawned"], 1)
        item = Item.objects.get(world=spawn_world, definition=item_definition)
        self.assertEqual(item.spawn_placement_id, placement_id)

    def test_hot_entry_changes_apply_to_next_mob_for_same_slot(self):
        east_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="East Yard",
            x=1,
            y=0,
            z=0,
        )
        archer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-archer",
            name="a practice archer",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"health_max": 10},
        )
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        placement_id = mob.spawn_placement_id
        self.entry.source = f"mobdefinition.{archer_definition.slug}"
        self.entry.target_room = east_room
        self.entry.traits = {
            "guaranteed": [
                {"key": "reinforced", "modifiers": {"armor": 3}},
            ],
        }
        self.entry.save(update_fields=["source", "target_room", "traits"])

        output = run_spawn_plans_for_world(world=spawn_world)

        mob.refresh_from_db()
        self.assertEqual(output["spawn_plans"][0]["spawned"], 0)
        self.assertEqual(mob.definition, self.mob_definition)
        self.assertEqual(mob.room, self.room)
        self.assertEqual(mob.spawn_placement_id, placement_id)
        mob.delete()

        run_spawn_plans_for_world(world=spawn_world)

        replacement = Mob.objects.get(world=spawn_world, definition=archer_definition)
        self.assertEqual(replacement.spawn_placement_id, placement_id)
        self.assertEqual(replacement.room, east_room)
        self.assertEqual(replacement.armor, 3)
        self.assertEqual(
            replacement.roll_metadata["spawn_plan"]["trait_keys"],
            ["reinforced"],
        )

    def test_hot_removed_entry_retires_and_can_reactivate_live_slot(self):
        archer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-archer",
            name="a practice archer",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"health_max": 10},
        )
        archer_entry = SpawnEntry.objects.create(
            plan=self.plan,
            slug="practice-archer",
            order=2,
            source=f"mobdefinition.{archer_definition.slug}",
            target_room=self.room,
            count=1,
        )
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        archer = Mob.objects.get(world=spawn_world, definition=archer_definition)
        archer_placement_id = archer.spawn_placement_id
        archer_entry.is_active = False
        archer_entry.save(update_fields=["is_active"])

        run_spawn_plans_for_world(world=spawn_world)

        archer.refresh_from_db()
        retired = SpawnPlacement.objects.get(pk=archer_placement_id)
        self.assertTrue(retired.is_retired)
        self.assertEqual(archer.spawn_placement_id, retired.id)
        archer_entry.is_active = True
        archer_entry.save(update_fields=["is_active"])

        output = run_spawn_plans_for_world(world=spawn_world)

        archer.refresh_from_db()
        retired.refresh_from_db()
        self.assertEqual(output["spawn_plans"][0]["spawned"], 0)
        self.assertFalse(retired.is_retired)
        self.assertEqual(archer.spawn_placement_id, archer_placement_id)
        self.assertEqual(
            Mob.objects.filter(world=spawn_world, definition=archer_definition).count(),
            1,
        )

        archer_entry.is_active = False
        archer_entry.save(update_fields=["is_active"])
        run_spawn_plans_for_world(world=spawn_world)
        archer.delete()

        second_output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(second_output["spawn_plans"][0]["spawned"], 0)
        self.assertFalse(
            Mob.objects.filter(world=spawn_world, definition=archer_definition).exists()
        )

    def test_hot_count_decrease_and_increase_reuses_live_slots(self):
        self.entry.count = 3
        self.entry.save(update_fields=["count"])
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        run = SpawnPlanRun.objects.get(spawn_world=spawn_world, plan=self.plan)
        original_placement_ids = set(
            run.placements.values_list("id", flat=True)
        )
        self.assertEqual(Mob.objects.filter(world=spawn_world).count(), 3)
        self.entry.count = 1
        self.entry.save(update_fields=["count"])

        run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(run.placements.filter(is_retired=False).count(), 1)
        self.assertEqual(run.placements.filter(is_retired=True).count(), 2)
        self.assertEqual(Mob.objects.filter(world=spawn_world).count(), 3)
        self.entry.count = 3
        self.entry.save(update_fields=["count"])

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 0)
        self.assertEqual(run.placements.filter(is_retired=False).count(), 3)
        self.assertEqual(run.placements.filter(is_retired=True).count(), 0)
        self.assertEqual(
            set(run.placements.values_list("id", flat=True)),
            original_placement_ids,
        )
        self.assertEqual(Mob.objects.filter(world=spawn_world).count(), 3)

    def test_hot_nested_count_increase_preserves_each_parent(self):
        crate_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="training-crate",
            name="a training crate",
            item_type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        token_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="training-token",
            name="a training token",
        )
        self.entry.source = f"itemdefinition.{crate_definition.slug}"
        self.entry.count = 2
        self.entry.traits = {}
        self.entry.save(update_fields=["source", "count", "traits"])
        child_entry = SpawnEntry.objects.create(
            plan=self.plan,
            slug="crate-token",
            order=2,
            source=f"itemdefinition.{token_definition.slug}",
            target_entry=self.entry,
            count=1,
        )
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()
        crates = list(
            Item.objects.filter(
                world=spawn_world,
                definition=crate_definition,
            ).order_by("id")
        )
        self.assertEqual(len(crates), 2)
        self.assertEqual(
            [crate.inventory.filter(definition=token_definition).count() for crate in crates],
            [1, 1],
        )
        child_entry.count = 2
        child_entry.save(update_fields=["count"])

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 2)
        self.assertEqual(
            [crate.inventory.filter(definition=token_definition).count() for crate in crates],
            [2, 2],
        )
        child_placements = SpawnPlacement.objects.filter(
            run__spawn_world=spawn_world,
            entry_slug=child_entry.slug,
            is_retired=False,
        )
        self.assertEqual(child_placements.count(), 4)
        self.assertEqual(
            set(child_placements.values_list("parent_slot_index", flat=True)),
            {0, 1},
        )

    def test_active_instance_keeps_spawn_plan_snapshot_after_template_edit(self):
        instance_template = World.objects.new_world(
            name="Snapshot Instance",
            author=self.user,
            config=WorldConfig.objects.create(),
            is_multiplayer=True,
            instance_of=self.world,
        )
        instance_zone = instance_template.zones.get()
        instance_room = instance_zone.rooms.get()
        plan = SpawnPlan.objects.create(
            world=instance_template,
            zone=instance_zone,
            slug="snapshot-population",
            respawn_policy={"mode": "fixed", "seconds": 0},
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="practice-dummy",
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_room=instance_room,
            count=1,
        )
        spawned_instance = instance_template.create_spawn_world(
            instance_ref="snapshot-one",
            leader=self.player,
        )
        WorldSmith(spawned_instance).start()
        run = SpawnPlanRun.objects.get(spawn_world=spawned_instance, plan=plan)
        original_hash = run.spec_hash
        archer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="instance-archer",
            name="an instance archer",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"health_max": 10},
        )
        SpawnEntry.objects.create(
            plan=plan,
            slug="instance-archer",
            order=2,
            source=f"mobdefinition.{archer_definition.slug}",
            target_room=instance_room,
            count=1,
        )

        run_spawn_plans_for_world(world=spawned_instance)

        run.refresh_from_db()
        self.assertEqual(run.spec_hash, original_hash)
        self.assertFalse(
            Mob.objects.filter(world=spawned_instance, definition=archer_definition).exists()
        )
        self.assertEqual(run.placements.filter(is_retired=False).count(), 1)
        Mob.objects.get(
            world=spawned_instance,
            definition=self.mob_definition,
        ).delete()

        second_output = run_spawn_plans_for_world(world=spawned_instance)

        self.assertTrue(second_output["spawn_plans"][0]["skipped"])
        self.assertFalse(Mob.objects.filter(world=spawned_instance).exists())

    def test_spawn_plan_cohort_roams_and_refills_together(self):
        self.world.config.default_roam_chance = 100
        self.world.config.save(update_fields=["default_roam_chance"])
        destination = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="East Yard",
            x=1,
            y=0,
            z=0,
        )
        self.room.east = destination
        self.room.save(update_fields=["east"])
        path = Path.objects.create(
            world=self.world,
            zone=self.zone,
            name="Training Patrol Loop",
            entry_room=self.room,
        )
        PathRoom.objects.create(path=path, room=self.room)
        PathRoom.objects.create(path=path, room=destination)
        archer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-archer",
            name="a practice archer",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={"health_max": 10},
        )
        self.entry.target_room = None
        self.entry.target_path = path
        self.entry.placement = {
            "cohort": "west-patrol",
            "cohort_role": "leader",
            "cohort_policy": "refill_missing",
        }
        self.entry.save(update_fields=["target_room", "target_path", "placement"])
        SpawnEntry.objects.create(
            plan=self.plan,
            slug="practice-archer",
            order=2,
            source=f"mobdefinition.{archer_definition.slug}",
            target_entry=self.entry,
            count=1,
            placement={
                "cohort": "west-patrol",
                "cohort_role": "follower",
                "cohort_policy": "refill_missing",
            },
        )
        spawn_world = self.world.create_spawn_world()
        WorldSmith(spawn_world).start()

        mobs = list(Mob.objects.filter(world=spawn_world).order_by("id"))
        self.assertEqual(len(mobs), 2)
        self.assertEqual({mob.room_id for mob in mobs}, {self.room.id})
        self.assertEqual(len({mob.group_id for mob in mobs}), 1)
        self.assertTrue(all(mob.roams == path for mob in mobs))

        roamed = run_mob_roaming()

        mobs = list(Mob.objects.filter(world=spawn_world).order_by("id"))
        self.assertEqual(roamed, 2)
        self.assertEqual({mob.room_id for mob in mobs}, {destination.id})
        group_id = mobs[0].group_id
        follower = next(mob for mob in mobs if mob.definition_id == archer_definition.id)
        no_roam_flag = RoomFlag.objects.create(
            room=destination,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        follower.delete()

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 0)
        mobs = list(Mob.objects.filter(world=spawn_world).order_by("id"))
        self.assertEqual(len(mobs), 1)
        self.assertEqual(mobs[0].room_id, destination.id)

        no_roam_flag.delete()
        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 1)
        mobs = list(Mob.objects.filter(world=spawn_world).order_by("id"))
        self.assertEqual(len(mobs), 2)
        self.assertEqual({mob.room_id for mob in mobs}, {destination.id})
        self.assertEqual({mob.group_id for mob in mobs}, {group_id})

        origin_no_roam_flag = RoomFlag.objects.create(
            room=self.room,
            code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
        for mob in mobs:
            mob.delete()
        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 0)
        self.assertFalse(Mob.objects.filter(world=spawn_world).exists())

        origin_no_roam_flag.delete()
        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 2)
        mobs = list(Mob.objects.filter(world=spawn_world).order_by("id"))
        self.assertEqual(len(mobs), 2)
        self.assertEqual({mob.room_id for mob in mobs}, {self.room.id})


class TestSpawnPlanExport(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.export_ep = reverse("builder-world-export", args=[self.world.pk])
        self.mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="practice-dummy",
            name="a practice dummy",
            mob_type=adv_consts.MOB_TYPE_CONSTRUCT,
            base_properties={"health_max": 10},
        )
        self.plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.zone,
            slug="training-grounds",
            name="Training Grounds",
            respawn_policy={"mode": "fixed", "seconds": 0},
        )
        SpawnEntry.objects.create(
            plan=self.plan,
            slug="practice-dummy",
            source=f"mobdefinition.{self.mob_definition.slug}",
            target_room=self.room,
            count=1,
            traits={
                "guaranteed": [
                    {
                        "key": "armored",
                        "modifiers": {"armor": 2},
                    }
                ]
            },
        )

    def test_world_export_includes_spawn_plan_manifest(self):
        resp = self.client.get(self.export_ep)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["summary"]["spawn_plans"], 1)
        docs = [doc for doc in yaml.safe_load_all(resp.data["yaml"]) if doc is not None]
        spawn_doc = next(doc for doc in docs if doc["kind"] == "spawnplan")
        self.assertEqual(spawn_doc["metadata"]["slug"], "training-grounds")
        self.assertEqual(spawn_doc["spec"]["zone"], f"zone@{self.zone.relative_id}")
        self.assertEqual(spawn_doc["spec"]["entries"][0]["source"], "mobdefinition.practice-dummy")
        self.assertEqual(
            spawn_doc["spec"]["entries"][0]["target"],
            f"room@{self.room.relative_id}",
        )
        self.assertEqual(
            spawn_doc["spec"]["entries"][0]["traits"]["guaranteed"][0]["key"],
            "armored",
        )
        self.assertNotIn("affixes", spawn_doc["spec"]["entries"][0])

    def test_world_export_includes_spawn_plan_cohort_fields(self):
        entry = self.plan.entries.get()
        entry.placement = {
            "cohort": "west-patrol",
            "cohort_role": "leader",
            "cohort_policy": "refill_missing",
        }
        entry.save(update_fields=["placement"])

        resp = self.client.get(self.export_ep)

        self.assertEqual(resp.status_code, 200, resp.data)
        docs = [doc for doc in yaml.safe_load_all(resp.data["yaml"]) if doc is not None]
        spawn_doc = next(doc for doc in docs if doc["kind"] == "spawnplan")
        entry_doc = spawn_doc["spec"]["entries"][0]
        self.assertEqual(entry_doc["cohort"], "west-patrol")
        self.assertEqual(entry_doc["cohort_role"], "leader")
        self.assertEqual(entry_doc["cohort_policy"], "refill_missing")
        self.assertNotIn("placement", entry_doc)
