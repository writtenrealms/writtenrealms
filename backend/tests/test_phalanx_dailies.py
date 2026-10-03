"""Exercise the optional, untracked Phalanx authoring package in a test DB.

Set PHALANX_DAILIES_DIR to the package directory when running in Docker.
The ordinary suite skips these world-specific tests when it is unavailable.
"""

import os
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import yaml
from django.urls import reverse

from builders.models import CraftingProfile, Currency, MobDefinition, MerchantStockSlot
from quests.models import QuestInstance, QuestTemplate
from quests.services.engine import (
    QuestRuntimeError, accept_template, abandon_instance, can_start_template,
    choose_for_instance, get_step, progress_active_instance_for_event,
)
from quests.services.predicates import evaluate_condition
from spawns.merchants import buy_item, list_merchant_stock
from spawns.models import Mob, MerchantStockEntry
from spawns.wallet import balance_map
from tests.base import WorldTestCase
from worlds.models import Room, Zone


PACKAGE_DIR = Path(os.environ.get(
    "PHALANX_DAILIES_DIR",
    str(Path(__file__).resolve().parents[2] / "docs/phalanx/dailies"),
))
QUEST_SLUGS = (
    "athens-clay-for-the-wheel", "athens-potters-batch",
    "athens-keeping-the-forge-fed", "athens-clean-linen", "athens-fit-for-the-line",
)
# Existing room identities and coordinates from the local Athens authoring map.
# The production database is never used by this fixture.
ROOMS = {
    44: ("Agora Southeast Stoa", -5, 3, 5),
    79: ("Palaestra Courtyard", -7, 9, 6),
    80: ("Wrestling Area", -6, 9, 6),
    81: ("Locker and Storage Room", -6, 8, 6),
    82: ("Exercise Room", -5, 9, 6),
    114: ("A Clay-Washing Yard", -8, 11, 6),
    115: ("Kiln Court", -7, 11, 6),
    116: ("Potter's Stall", -6, 11, 6),
    117: ("Potter's House", -5, 11, 6),
    118: ("Lane by the Clay-Washing Yard", -8, 12, 6),
    119: ("Workshop Lane Junction", -7, 12, 6),
    120: ("South Residential Lane", -6, 12, 6),
    127: ("A Public Fountain", -3, 13, 6),
    133: ("Kerameikos Market", -3, 15, 6),
    134: ("Fuel and Clay Stalls", -4, 15, 6),
    154: ("Armorer's Workshop", -8, 13, 6),
    164: ("Court of the Paean", -4, 4, 5),
    165: ("The Iatreion", -3, 4, 5),
}


@skipUnless(PACKAGE_DIR.is_dir(), "Optional Phalanx dailies manifests are not installed")
class TestPhalanxDailies(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.apply_ep = reverse("builder-world-manifest-apply", args=[self.world.pk])
        zones = {
            relative_id: Zone.objects.create(
                world=self.world, relative_id=relative_id, name=name,
            )
            for relative_id, name in ((5, "Athens - Agora"), (6, "Athens - Kerameikos"))
        }
        for relative_id, (name, x, y, zone_id) in sorted(ROOMS.items()):
            Room.objects.create_with_imported_relative_id(
                world=self.world, zone=zones[zone_id], relative_id=relative_id,
                name=name, x=x, y=y, z=50,
            )
        # An unrelated connection/service must survive narrow room patches.
        self.workshop = self.world.rooms.get(relative_id=154)
        self.workshop.crafting_profile = CraftingProfile.objects.create(
            world=self.world, slug="camp-workshop", name="Armorer's Workshop",
        )
        self.workshop.save(update_fields=["crafting_profile"])
        lane = self.world.rooms.get(relative_id=120)
        lane.west = self.world.rooms.get(relative_id=119)
        lane.save(update_fields=["west"])
        # Demeas and his placement are existing Phalanx prerequisites.
        self.demeas = MobDefinition.objects.create(
            world=self.world, slug="demeas-the-hoplomachos", name="Demeas the Hoplomachos",
            attackable=False,
        )
        self.documents = []
        self._ingest()
        self.currency = Currency.objects.get(world=self.world, code="obol")
        self.demeas.spawn(self.world.rooms.get(relative_id=80), self.spawn_world)
        # Authoring imports describe spawn plans; spawn them explicitly here
        # because test transactions do not execute the live scheduling worker.
        for document in self.documents:
            if document.get("kind") != "spawnplan":
                continue
            for entry in document["spec"].get("entries", []):
                source = entry.get("source", "")
                if not source.startswith("mobdefinition."):
                    continue
                definition = MobDefinition.objects.get(world=self.world, slug=source.split(".", 1)[1])
                room = self.world.rooms.get(relative_id=int(entry["target"].split("@")[1]))
                if not Mob.objects.filter(world=self.spawn_world, definition=definition, room=room).exists():
                    definition.spawn(room, self.spawn_world)

    def _ingest(self):
        for path in sorted(PACKAGE_DIR.glob("[0-9]*.yaml")):
            content = path.read_text()
            documents = list(yaml.safe_load_all(content))
            # Scope the Phalanx-specific files to this disposable fixture world.
            for document in documents:
                metadata = (document or {}).get("metadata", {})
                if "world" in metadata:
                    self.assertEqual(metadata["world"], "world.23")
                    metadata["world"] = f"world.{self.world.pk}"
            response = self.client.post(self.apply_ep, {"manifest": yaml.safe_dump_all(documents)}, format="json")
            self.assertIn(response.status_code, (200, 201), (path.name, response.data))
            self.documents.extend(document for document in documents if document)

    def _move(self, relative_id, player=None):
        player = player or self.player
        player.room = self.world.rooms.get(relative_id=relative_id)
        player.save(update_fields=["room"])

    def _at_giver(self, template, player=None):
        player = player or self.player
        source = template.discovery_policy["sources"][0]
        slug = source["mob_definition"].split(".", 1)[-1]
        giver = Mob.objects.get(world=self.spawn_world, definition__slug=slug)
        self._move(giver.room.relative_id, player)
        return giver

    def _finish(self, template, player=None):
        """Drive actual guarded choices and the authored final talk objective."""
        player = player or self.player
        self._at_giver(template, player)
        attempt = accept_template(player, template).quest_instance
        for _ in range(25):
            attempt.refresh_from_db()
            if attempt.status == "resolved":
                self.assertEqual(attempt.resolution, "complete")
                return attempt
            step = get_step(template, attempt.current_step_id)
            if step["kind"] == "storylet":
                selected = None
                for relative_id in ROOMS:
                    self._move(relative_id, player)
                    for choice in step.get("choices", []):
                        if evaluate_condition(choice.get("if"), player=player, template=template, quest_instance=attempt):
                            selected = choice
                            break
                    if selected:
                        break
                self.assertIsNotNone(selected, (template.slug, step["id"]))
                choose_for_instance(player, str(attempt.pk), selected["id"])
            else:
                self.assertEqual(step["kind"], "objective", (template.slug, step))
                self.assertEqual(len(step["objectives"]), 1)
                tracker = step["objectives"][0]["tracker"]
                self.assertEqual(tracker["event"], "cmd.talk.success")
                # The handoff may target a different artisan than the giver.
                matched = False
                for mob in Mob.objects.filter(world=self.spawn_world).select_related("room", "definition"):
                    self._move(mob.room.relative_id, player)
                    event_data = {"target": {"definition_id": mob.definition_id, "id": mob.pk}, "actor": {"key": player.key}}
                    if evaluate_condition(tracker.get("where"), player=player, template=template, quest_instance=attempt, event_data=event_data):
                        progress_active_instance_for_event(attempt, player=player, event_type="cmd.talk.success", event_data=event_data)
                        matched = True
                        break
                self.assertTrue(matched, (template.slug, step["id"]))
        self.fail(f"Quest did not resolve: {template.slug}")

    def test_first_circuit_repeat_circuit_and_all_t1_purchases(self):
        first_day = datetime(2026, 10, 2, 14, tzinfo=dt_timezone.utc)
        templates = list(QuestTemplate.objects.filter(world=self.world, slug__in=QUEST_SLUGS).order_by("slug"))
        self.assertEqual(len(templates), 5)
        with patch("django.utils.timezone.now", return_value=first_day):
            for template in templates:
                self._finish(template)
                self.assertFalse(can_start_template(self.player, template))
            self.assertEqual(balance_map(self.player)["obol"], 800)
            purchased = set()
            for room in self.world.rooms.filter(merchant_profile__isnull=False):
                self._move(room.relative_id)
                stock = list_merchant_stock(self.player, None)
                self.assertEqual(stock["merchant"]["id"], room.pk)
                for entry in MerchantStockEntry.objects.filter(runtime__room=room, runtime__world=self.spawn_world, status="available").select_related("item__definition"):
                    slug = entry.item.definition.slug
                    if slug in purchased:
                        continue
                    buy_item(self.player, None, f"merchant_stock_entry.{entry.pk}")
                    purchased.add(slug)
            self.assertEqual(len(purchased), 10)
            self.assertEqual(balance_map(self.player)["obol"], 20)
        with patch("django.utils.timezone.now", return_value=first_day + timedelta(days=1)):
            for template in templates:
                self.assertTrue(can_start_template(self.player, template))
                self._finish(template)
            self.assertEqual(balance_map(self.player)["obol"], 220)

    def test_work_is_private_location_guarded_and_abandon_preserves_first_bonus(self):
        template = QuestTemplate.objects.get(world=self.world, slug=QUEST_SLUGS[0])
        self._at_giver(template)
        attempt = accept_template(self.player, template).quest_instance
        step = get_step(template, attempt.current_step_id)
        choice = step["choices"][0]
        self._move(165)
        with self.assertRaises(QuestRuntimeError):
            choose_for_instance(self.player, str(attempt.pk), choice["id"])
        attempt.refresh_from_db()
        self.assertEqual(attempt.current_step_id, step["id"])
        abandon_instance(self.player, str(attempt.pk))
        self._finish(template)
        self.assertEqual(balance_map(self.player)["obol"], 100)
        second = self.create_player("Second newcomer")
        self._finish(template, second)
        self.assertEqual(balance_map(second)["obol"], 100)

    def test_reimport_preserves_identity_history_and_existing_services(self):
        template = QuestTemplate.objects.get(world=self.world, slug=QUEST_SLUGS[0])
        self._finish(template)
        before = dict(QuestTemplate.objects.filter(world=self.world).values_list("slug", "pk"))
        self._ingest()
        self.assertEqual(dict(QuestTemplate.objects.filter(world=self.world).values_list("slug", "pk")), before)
        self.assertTrue(QuestInstance.objects.filter(player=self.player, template=template, resolution="complete").exists())
        template.refresh_from_db()
        self.assertFalse(can_start_template(self.player, template))
        self.workshop.refresh_from_db()
        self.assertEqual(self.workshop.crafting_profile.slug, "camp-workshop")
        lane = self.world.rooms.get(relative_id=120)
        self.assertEqual(lane.west.relative_id, 119)
        self.assertEqual(lane.east.relative_id, 222)
        self.assertEqual(lane.east.west_id, lane.pk)
        slots = MerchantStockSlot.objects.filter(profile__world=self.world)
        self.assertEqual(slots.count(), 10)
