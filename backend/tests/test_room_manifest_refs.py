from rest_framework import serializers

from builders import manifests as builder_manifests
from builders import world_export as builder_world_export
from builders.models import ItemDefinition, MobDefinition
from config import constants as adv_consts
from core.condition_dsl import ConditionContext, evaluate_condition
from quests.entity_refs import resolve_room_ref_id
from tests.base import WorldTestCase
from worlds.models import Room, World, WorldConfig
from worlds.room_refs import (
    ParsedRoomReference,
    RoomReferenceError,
    canonicalize_room_reference,
    canonicalize_room_references_in_text,
    format_room_manifest_ref,
    legacy_room_coordinate_ref,
    parse_room_reference,
    resolve_room_reference,
)


class TestRoomManifestReferences(WorldTestCase):
    def test_parser_classifies_canonical_and_legacy_references(self):
        self.assertEqual(
            parse_room_reference("room@17"),
            ParsedRoomReference(kind="relative_id", relative_id=17),
        )
        self.assertEqual(
            parse_room_reference("room@-2, 4, 0"),
            ParsedRoomReference(kind="coordinates", x=-2, y=4, z=0),
        )
        self.assertEqual(
            parse_room_reference("room.91"),
            ParsedRoomReference(kind="database_id", database_id=91),
        )
        self.assertIsNone(parse_room_reference("17"))
        self.assertIsNone(parse_room_reference("room-name"))
        self.assertIsNone(parse_room_reference(True))

    def test_canonical_reference_survives_room_movement(self):
        canonical_ref = format_room_manifest_ref(self.room)
        old_coordinate_ref = legacy_room_coordinate_ref(self.room)

        self.assertEqual(
            resolve_room_reference(self.world, canonical_ref),
            self.room,
        )
        self.assertEqual(
            resolve_room_reference(self.world, old_coordinate_ref),
            self.room,
        )

        self.room.x += 40
        self.room.y += 30
        self.room.save(update_fields=["x", "y"])

        self.assertEqual(
            resolve_room_reference(self.world, canonical_ref),
            self.room,
        )
        self.assertIsNone(
            resolve_room_reference(self.world, old_coordinate_ref),
        )

    def test_canonical_reference_is_portable_between_world_databases(self):
        portable_ref = format_room_manifest_ref(self.room)
        imported_config = WorldConfig.objects.create()
        imported_world = World.objects.new_world(
            name="Imported world",
            author=self.user,
            config=imported_config,
        )
        imported_room = imported_world.rooms.get(
            relative_id=self.room.relative_id,
        )

        self.assertNotEqual(imported_room.id, self.room.id)
        self.assertEqual(
            resolve_room_reference(imported_world, portable_ref),
            imported_room,
        )

    def test_legacy_database_reference_is_world_scoped(self):
        database_ref = f"room.{self.room.id}"

        self.assertEqual(
            resolve_room_reference(self.world, database_ref),
            self.room,
        )

        other_config = WorldConfig.objects.create()
        other_world = World.objects.new_world(
            name="Other world",
            author=self.user,
            config=other_config,
        )
        self.assertIsNone(
            resolve_room_reference(other_world, database_ref),
        )

    def test_canonicalization_converts_both_legacy_aliases(self):
        canonical_ref = format_room_manifest_ref(self.room)

        self.assertEqual(
            canonicalize_room_reference(
                self.world,
                legacy_room_coordinate_ref(self.room),
            ),
            canonical_ref,
        )
        self.assertEqual(
            canonicalize_room_reference(
                self.world,
                f"room.{self.room.id}",
            ),
            canonical_ref,
        )
        self.assertIsNone(
            canonicalize_room_reference(self.world, "room.999999999"),
        )

    def test_semantic_text_canonicalization_is_batched_and_non_destructive(self):
        canonical_ref = format_room_manifest_ref(self.room)
        coordinate_ref = legacy_room_coordinate_ref(self.room)
        database_ref = f"room.{self.room.id}"
        text = (
            f"/transfer self {coordinate_ref}; "
            f"/cmd room -- /transfer player {database_ref}; "
            f"leave room.999999999, pseudo-{database_ref}, and "
            f"{coordinate_ref},9 unchanged"
        )

        with self.assertNumQueries(2):
            canonicalized = canonicalize_room_references_in_text(
                self.world,
                text,
            )

        self.assertEqual(canonicalized.count(canonical_ref), 2)
        self.assertIn("room.999999999", canonicalized)
        self.assertIn(f"pseudo-{database_ref}", canonicalized)
        self.assertIn(f"{coordinate_ref},9", canonicalized)
        with self.assertRaises(RoomReferenceError):
            canonicalize_room_references_in_text(
                self.world,
                text,
                strict=True,
            )

    def test_room_reference_cache_loads_names_without_per_room_queries(self):
        last_room = None
        for index in range(10):
            last_room = Room.objects.create(
                world=self.world,
                zone=self.zone,
                name=f"Cache Room {index}",
                x=index + 1,
                y=0,
                z=0,
            )

        with self.assertNumQueries(1):
            cache = builder_world_export._build_room_ref_cache(self.world)

        self.assertEqual(
            cache[("name", "Cache Room 9")],
            f"room@{last_room.relative_id}",
        )

    def test_request_local_room_object_cache_avoids_reference_queries(self):
        rooms = list(Room.objects.filter(world=self.world))
        object_cache = (
            builder_world_export.build_room_reference_object_cache(rooms)
        )

        with builder_world_export.use_room_reference_object_caches({
            self.world.id: object_cache,
        }):
            with self.assertNumQueries(0):
                resolved = [
                    resolve_room_reference(
                        self.world,
                        format_room_manifest_ref(self.room),
                    ),
                    resolve_room_reference(
                        self.world,
                        legacy_room_coordinate_ref(self.room),
                    ),
                    resolve_room_reference(
                        self.world,
                        f"room.{self.room.id}",
                    ),
                ]

        self.assertEqual(resolved, [self.room, self.room, self.room])

    def test_quest_wrapper_resolves_canonical_and_legacy_bare_database_ids(self):
        canonical_ref = format_room_manifest_ref(self.room)

        self.assertEqual(
            resolve_room_ref_id(world=self.world, value=canonical_ref),
            self.room.id,
        )
        self.assertEqual(
            resolve_room_ref_id(world=self.world, value=str(self.room.id)),
            self.room.id,
        )

    def test_spawn_room_resolution_accepts_canonical_reference(self):
        canonical_ref = format_room_manifest_ref(self.room)

        self.assertEqual(
            builder_world_export._resolve_spawn_plan_room(
                world=self.world,
                value=canonical_ref,
                field_name="target",
            ),
            self.room,
        )

    def test_condition_comparison_resolves_canonical_reference(self):
        canonical_ref = format_room_manifest_ref(self.room)
        context = ConditionContext(actor=self.player)

        self.assertTrue(
            evaluate_condition(
                {"eq": ["actor.room_id", canonical_ref]},
                context=context,
            )
        )

    def test_transfer_destination_resolves_canonical_reference(self):
        from spawns.actions.builder import TransferAction

        destination = self.room.create_at("east")
        canonical_ref = format_room_manifest_ref(destination)

        resolved = TransferAction()._resolve_destination(
            issuer_room=self.room,
            runtime_world=self.spawn_world,
            selector=canonical_ref,
        )

        self.assertEqual(resolved, destination)

    def test_export_rejects_cross_world_room_relations_instead_of_aliasing(self):
        other_config = WorldConfig.objects.create()
        other_world = World.objects.new_world(
            name="Other world",
            author=self.user,
            config=other_config,
        )
        foreign_room = other_world.rooms.get()
        self.world.zones.filter(pk=self.zone.pk).update(
            center_id=foreign_room.id,
        )

        with self.assertRaises(serializers.ValidationError) as raised:
            builder_world_export.serialize_world_documents(self.world)

        self.assertIn("outside this world", str(raised.exception))

    def test_export_rejects_cross_world_world_config_room(self):
        other_config = WorldConfig.objects.create()
        other_world = World.objects.new_world(
            name="Other configured world",
            author=self.user,
            config=other_config,
        )
        self.world.config.starting_room = other_world.rooms.get()
        self.world.config.save(update_fields=["starting_room"])

        with self.assertRaises(serializers.ValidationError) as raised:
            builder_world_export.serialize_world_documents(self.world)

        self.assertIn("World starting room", str(raised.exception))
        self.assertIn("outside this world", str(raised.exception))

    def test_export_canonicalizes_definition_commands_without_rewriting_prose(self):
        database_ref = f"room.{self.room.id}"
        canonical_ref = format_room_manifest_ref(self.room)
        item = ItemDefinition.objects.create(
            world=self.world,
            slug="recall-token",
            name="Recall Token",
            description=f"The maker's mark reads {database_ref}.",
            base_properties={
                "on_use_cmd": f"/transfer self {database_ref}",
            },
        )
        mob = MobDefinition.objects.create(
            world=self.world,
            slug="ferryman",
            name="Ferryman",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={
                "combat_script": (
                    f"/transfer {{ actor_key }} {database_ref}"
                ),
            },
        )

        documents = builder_world_export.serialize_world_documents(self.world)
        item_document = next(
            document
            for document in documents
            if document["kind"] == "itemdefinition"
            and document["metadata"]["slug"] == item.slug
        )
        mob_document = next(
            document
            for document in documents
            if document["kind"] == "mobdefinition"
            and document["metadata"]["slug"] == mob.slug
        )

        self.assertEqual(
            item_document["spec"]["on_use_cmd"],
            f"/transfer self {canonical_ref}",
        )
        self.assertEqual(
            item_document["spec"]["description"],
            f"The maker's mark reads {database_ref}.",
        )
        self.assertEqual(
            mob_document["spec"]["combat_script"],
            f"/transfer {{ actor_key }} {canonical_ref}",
        )

        item_payload = builder_manifests.serialize_item_definition_payload(item)
        mob_payload = builder_manifests.serialize_mob_definition_payload(mob)
        self.assertEqual(
            item_payload["manifest"]["spec"]["on_use_cmd"],
            f"/transfer self {canonical_ref}",
        )
        self.assertEqual(
            item_payload["manifest"]["spec"]["description"],
            f"The maker's mark reads {database_ref}.",
        )
        self.assertEqual(
            mob_payload["manifest"]["spec"]["combat_script"],
            f"/transfer {{ actor_key }} {canonical_ref}",
        )
