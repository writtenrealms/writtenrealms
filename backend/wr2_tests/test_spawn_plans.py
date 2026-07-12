import yaml

from django.urls import reverse

from builders.models import ItemDefinition, MobDefinition, Path, PathRoom, SpawnEntry, SpawnPlan, SpawnPlacement, SpawnPlanRun
from config import constants as adv_consts
from spawns.loading import run_spawn_plans_for_world
from spawns.models import Mob
from spawns.tasks import run_mob_roaming
from tests.base import WorldTestCase
from worlds.models import Room, World, WorldConfig
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
        self.assertEqual(entry.target["room"], f"room@{self.room.x},{self.room.y},{self.room.z}")
        self.assertEqual(entry.count, 1)

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

    def test_apply_spawn_plan_manifest_accepts_transition_metadata_and_source_pool(self):
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
        self.assertEqual(entry.target["room_ref"], f"room.{self.room.id}")
        self.assertEqual(entry.target["name"], self.room.name)
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
        self.assertEqual(follower.target["entry"], "patrol-leaders")
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
        self.assertIn("earlier active entry", str(resp.data))
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
            target={"room": f"room@{self.room.x},{self.room.y},{self.room.z}"},
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
        self.assertEqual(entry.target["room"], f"room@{instance_room.x},{instance_room.y},{instance_room.z}")

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
        self.assertEqual(plan.entries.get().target["path"], f"path@{path.relative_id}")

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
            target={"room": f"room@{self.room.x},{self.room.y},{self.room.z}"},
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
            target={"room": f"room@{self.room.x},{self.room.y},{self.room.z}"},
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
        self.assertEqual([trait["key"] for trait in placement.traits], ["sturdy", "armored"])

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
        self.entry.target = {"path": f"path@{path.relative_id}"}
        self.entry.save(update_fields=["target"])
        spawn_world = self.world.create_spawn_world()

        WorldSmith(spawn_world).start()

        mob = Mob.objects.get(world=spawn_world, definition=self.mob_definition)
        self.assertEqual(mob.room, self.room)
        self.assertEqual(mob.roams, path)
        placement = SpawnPlacement.objects.get(run__plan=self.plan)
        self.assertEqual(placement.room, self.room)

    def test_world_start_resolves_zone_ref_targets(self):
        self.entry.target = {"zone": f"zone@{self.zone.relative_id}"}
        self.entry.save(update_fields=["target"])
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
            target={"room": f"room@{instance_room.x},{instance_room.y},{instance_room.z}"},
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
        self.entry.target = {"path": f"path@{path.relative_id}"}
        self.entry.placement = {
            "cohort": "west-patrol",
            "cohort_role": "leader",
            "cohort_policy": "refill_missing",
        }
        self.entry.save(update_fields=["target", "placement"])
        SpawnEntry.objects.create(
            plan=self.plan,
            slug="practice-archer",
            order=2,
            source=f"mobdefinition.{archer_definition.slug}",
            target={"entry": self.entry.slug},
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
        follower.delete()

        output = run_spawn_plans_for_world(world=spawn_world)

        self.assertEqual(output["spawn_plans"][0]["spawned"], 1)
        mobs = list(Mob.objects.filter(world=spawn_world).order_by("id"))
        self.assertEqual(len(mobs), 2)
        self.assertEqual({mob.room_id for mob in mobs}, {destination.id})
        self.assertEqual({mob.group_id for mob in mobs}, {group_id})

        for mob in mobs:
            mob.delete()
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
            target={"room": f"room@{self.room.x},{self.room.y},{self.room.z}"},
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
