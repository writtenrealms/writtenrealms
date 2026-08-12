import random

import yaml

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import serializers

from builders import world_export as builder_world_export
from builders.models import ItemDefinition, MobDefinition, Path, PathRoom, SpawnEntry, SpawnPlan, SpawnPlacement, SpawnPlanRun
from config import constants as adv_consts
from spawns.loading import run_spawn_plans_for_world
from spawns.models import Item, Mob
from spawns.spawn_plans import (
    SpawnReconcileContext,
    _choose_room_for_entry,
    _target_entry_slug,
    run_spawn_plans,
)
from spawns.tasks import run_mob_roaming
from tests.base import WorldTestCase
from worlds.models import Room, RoomFlag, World, WorldConfig
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
