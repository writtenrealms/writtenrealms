import yaml

from rest_framework.reverse import reverse

from builders.models import (
    FACTION_ASSIGNMENT_SOURCE_MOB_DEFINITION,
    FACTION_TYPE_CORE,
    FACTION_TYPE_REPUTATION,
    Faction,
    MobDefinition,
)
from quests.services.effects import apply_quest_effects
from spawns.models import Player
from tests.base import WorldTestCase
from worlds.models import Room


class QuestEffectStub:
    local_state = {}


class TestFactionManifests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.apply_ep = reverse("builder-world-manifest-apply", args=[self.world.pk])
        self.export_ep = reverse("builder-world-export", args=[self.world.pk])

    def test_apply_faction_manifests_and_export_documents(self):
        faction_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name="Orc Camp",
            x=1,
            y=0,
            z=0,
        )
        core_manifest = f"""
kind: faction
metadata:
  world: world.{self.world.id}
  code: orc
  name: Orc
spec:
  type: core
  description: Orcish clans from the eastern hills.
  playable: true
  starting_room: room@{faction_room.x},{faction_room.y},{faction_room.z}
  default_languages:
    - orcish
"""
        resp = self.client.post(self.apply_ep, {"manifest": core_manifest}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["kind"], "faction")
        self.assertEqual(resp.data["faction"]["manifest"]["spec"]["type"], "core")

        orc = Faction.objects.get(world=self.world, code="orc")
        self.assertEqual(orc.type, FACTION_TYPE_CORE)
        self.assertTrue(orc.playable)
        self.assertTrue(orc.is_core)
        self.assertEqual(orc.starting_room, faction_room)
        self.assertEqual(orc.default_languages, ["orcish"])

        reputation_manifest = f"""
kind: faction
metadata:
  world: world.{self.world.id}
  code: ashwick
  name: Ashwick
spec:
  type: reputation
  description: The town council and its watch.
  ranks:
    - standing: -100
      name: Hated
    - standing: 0
      name: Neutral
    - standing: 100
      name: Trusted
"""
        resp = self.client.post(self.apply_ep, {"manifest": reputation_manifest}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

        ashwick = Faction.objects.get(world=self.world, code="ashwick")
        self.assertEqual(ashwick.type, FACTION_TYPE_REPUTATION)
        self.assertFalse(ashwick.playable)
        self.assertEqual(
            list(ashwick.ranks.order_by("standing").values_list("standing", "name")),
            [(-100, "Hated"), (0, "Neutral"), (100, "Trusted")],
        )

        export_resp = self.client.get(self.export_ep)
        self.assertEqual(export_resp.status_code, 200, export_resp.data)
        self.assertEqual(export_resp.data["summary"]["factions"], 2)
        docs = [doc for doc in yaml.safe_load_all(export_resp.data["yaml"]) if doc]
        faction_docs = [doc for doc in docs if doc["kind"] == "faction"]
        self.assertEqual([doc["metadata"]["code"] for doc in faction_docs], ["orc", "ashwick"])
        self.assertNotIn("is_core", faction_docs[0]["spec"])
        self.assertEqual(faction_docs[0]["spec"]["starting_room"], "room@1,0,0")

    def test_mob_definition_manifest_assigns_and_spawns_factions(self):
        Faction.objects.create(
            world=self.world,
            code="orc",
            name="Orc",
            type=FACTION_TYPE_CORE,
            playable=True,
        )
        Faction.objects.create(
            world=self.world,
            code="ashwick",
            name="Ashwick",
            type=FACTION_TYPE_REPUTATION,
        )

        manifest = f"""
kind: mobdefinition
metadata:
  world: world.{self.world.id}
  slug: orc-raider
  name: an orc raider
spec:
  type: humanoid
  aggression: normal
  health_max: 24
  factions:
    core: orc
    reputation:
      ashwick: -75
"""
        resp = self.client.post(self.apply_ep, {"manifest": manifest}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

        definition = MobDefinition.objects.get(world=self.world, slug="orc-raider")
        self.assertEqual(
            definition.faction_assignments.get(faction__code="orc").source,
            FACTION_ASSIGNMENT_SOURCE_MOB_DEFINITION,
        )
        self.assertEqual(
            definition.faction_assignments.get(faction__code="ashwick").value,
            -75,
        )
        self.assertEqual(
            resp.data["mob_definition"]["manifest"]["spec"]["factions"],
            {"core": "orc", "reputation": {"ashwick": -75}},
        )

        mob = definition.spawn(self.room, self.spawn_world)
        self.assertEqual(mob.faction_assignments.get(faction__code="orc").source, "mob_definition")
        self.assertEqual(mob.faction_assignments.get(faction__code="ashwick").value, -75)
        self.assertEqual(mob.factions["core"], "orc")
        self.assertEqual(mob.factions["ashwick"], -75)


class TestFactionPlayerCreation(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.char_ep = reverse("lobby-world-chars", args=[self.world.pk])

    def test_player_creation_policy_assigns_default_and_rejects_invalid_choice(self):
        Faction.objects.create(
            world=self.world,
            code="human",
            name="Human",
            type=FACTION_TYPE_CORE,
            playable=True,
        )
        Faction.objects.create(
            world=self.world,
            code="orc",
            name="Orc",
            type=FACTION_TYPE_CORE,
            playable=True,
        )
        self.world.config.player_creation = {
            "core_faction": {
                "mode": "choose_required",
                "default": "human",
                "options": ["human", "orc"],
            },
        }
        self.world.config.save(update_fields=["player_creation"])

        resp = self.client.post(self.char_ep, {"name": "Mara"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        player = Player.objects.select_related("core_faction").get(
            pk=resp.data["id"],
        )
        self.assertEqual(player.core_faction.code, "human")
        self.assertFalse(
            player.faction_assignments.filter(
                faction__type=FACTION_TYPE_CORE,
            ).exists()
        )

        resp = self.client.post(
            self.char_ep,
            {"name": "Tarn", "faction": "dwarf"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)


class TestFactionQuestEffects(WorldTestCase):
    def test_adjust_reputation_effect_updates_player_assignment(self):
        faction = Faction.objects.create(
            world=self.world,
            code="ashwick",
            name="Ashwick",
            type=FACTION_TYPE_REPUTATION,
        )

        result = apply_quest_effects(
            QuestEffectStub(),
            [{"type": "adjust_reputation", "faction": "ashwick", "amount": 5}],
            player=self.player,
        )
        assignment = self.player.faction_assignments.get(faction=faction)
        self.assertEqual(assignment.value, 5)
        self.assertEqual(result.reward_summaries, ["+5 Ashwick reputation"])

        apply_quest_effects(
            QuestEffectStub(),
            [{"type": "adjust_reputation", "faction": "ashwick", "amount": -2}],
            player=self.player,
        )
        assignment.refresh_from_db()
        self.assertEqual(assignment.value, 3)
