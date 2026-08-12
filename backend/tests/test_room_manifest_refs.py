from rest_framework import serializers

from builders import manifests as builder_manifests
from builders import world_export as builder_world_export
from builders.instance_templates import create_instance_template
from builders.models import ItemDefinition, MobDefinition
from config import constants as adv_consts
from core.condition_dsl import ConditionContext, evaluate_condition
from core.death_routing import (
    DeathRoutingValidationError,
    compile_death_routing_policy,
)
from quests.entity_refs import resolve_room_ref_id
from tests.base import WorldTestCase
from worlds.models import Room, World, WorldConfig
from worlds.room_refs import (
    ParsedBaseWorldRoomReference,
    ParsedRoomReference,
    RoomReferenceError,
    canonicalize_base_world_room_reference,
    canonicalize_command_room_references_in_text,
    canonicalize_room_reference,
    canonicalize_room_references_in_text,
    direct_base_world_for_room_reference,
    format_base_world_room_manifest_ref,
    format_room_manifest_ref,
    legacy_room_coordinate_ref,
    parse_base_world_room_reference,
    parse_room_reference,
    resolve_base_world_room_reference,
    resolve_room_reference,
)


class TestRoomManifestReferences(WorldTestCase):
    def _create_instance_template(self):
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        return create_instance_template(
            base_world=self.world,
            author=self.user,
            name="Scoped Reference Instance",
            instance_slug="scoped-reference-instance",
        )

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
        self.assertIsNone(parse_room_reference("room@²"))
        self.assertIsNone(parse_room_reference(f"room@{'9' * 5000}"))
        self.assertIsNone(parse_room_reference(f"room.{'9' * 5000}"))

    def test_base_world_parser_accepts_only_positive_portable_references(self):
        self.assertEqual(
            parse_base_world_room_reference("WORLD@BASE/ROOM@17"),
            ParsedBaseWorldRoomReference(relative_id=17),
        )
        self.assertIsNone(
            parse_base_world_room_reference("world@base/room@0")
        )
        self.assertIsNone(
            parse_base_world_room_reference("world@base/room.17")
        )
        self.assertIsNone(
            parse_base_world_room_reference("world@base/room@1,2,3")
        )

    def test_base_world_reference_resolves_from_template_and_runtime_context(self):
        template = self._create_instance_template()
        instance_room = template.config.starting_room
        self.assertEqual(instance_room.relative_id, self.room.relative_id)
        reference = format_base_world_room_manifest_ref(self.room)

        self.assertEqual(
            direct_base_world_for_room_reference(template),
            self.world,
        )
        self.assertEqual(
            resolve_base_world_room_reference(template, reference),
            self.room,
        )
        self.assertNotEqual(
            resolve_base_world_room_reference(template, reference),
            instance_room,
        )
        self.assertEqual(
            canonicalize_base_world_room_reference(template, reference),
            reference,
        )

        runtime_world = template.create_spawn_world()
        self.assertEqual(
            direct_base_world_for_room_reference(runtime_world),
            self.world,
        )
        self.assertEqual(
            resolve_base_world_room_reference(runtime_world, reference),
            self.room,
        )
        self.assertIsNone(
            resolve_base_world_room_reference(self.world, reference)
        )

    def test_exitinstance_command_canonicalizes_base_and_local_refs_separately(self):
        template = self._create_instance_template()
        instance_room = template.config.starting_room
        base_reference = format_base_world_room_manifest_ref(self.room)
        local_database_reference = f"room.{instance_room.id}"
        text = (
            f"/transfer self {local_database_reference}\n"
            "/cmd room -- /exitinstance {{ actor_key }} "
            f"{base_reference.upper()}"
        )

        canonical = canonicalize_command_room_references_in_text(
            template,
            text,
            strict=True,
        )

        self.assertEqual(
            canonical,
            f"/transfer self room@{instance_room.relative_id}\n"
            "/cmd room -- /exitinstance {{ actor_key }} "
            f"{base_reference}",
        )

    def test_scoped_room_token_is_not_reinterpreted_for_other_commands(self):
        template = self._create_instance_template()
        for text in (
            "/echo -- WORLD@BASE/ROOM@999999",
            "exitinstance self WORLD@BASE/ROOM@999999",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    canonicalize_command_room_references_in_text(
                        template,
                        text,
                        strict=True,
                    ),
                    text,
                )
                self.assertEqual(
                    canonicalize_room_references_in_text(
                        template,
                        text,
                        strict=True,
                    ),
                    text,
                )

    def test_exitinstance_command_rejects_invalid_base_destinations(self):
        template = self._create_instance_template()
        invalid_commands = (
            "/exitinstance self world@base/room@999999",
            "/exitinstance self world@base/room@-1",
            f"/exitinstance self world@base/room.{self.room.id}",
            "/exitinstance self world@base/room@0,0,0",
        )

        for command in invalid_commands:
            with self.subTest(command=command):
                with self.assertRaises(RoomReferenceError):
                    canonicalize_command_room_references_in_text(
                        template,
                        command,
                        strict=True,
                    )

        with self.assertRaises(RoomReferenceError):
            canonicalize_command_room_references_in_text(
                self.world,
                "/exitinstance self world@base/room@1",
                strict=True,
            )

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

    def test_quest_wrapper_resolves_only_canonical_room_refs(self):
        canonical_ref = format_room_manifest_ref(self.room)

        self.assertEqual(
            resolve_room_ref_id(world=self.world, value=canonical_ref),
            self.room.id,
        )
        for legacy_value in (
            self.room.id,
            str(self.room.id),
            f"room.{self.room.id}",
            legacy_room_coordinate_ref(self.room),
        ):
            with self.subTest(legacy_value=legacy_value):
                self.assertIsNone(
                    resolve_room_ref_id(
                        world=self.world,
                        value=legacy_value,
                    )
                )

    def test_import_normalizer_preserves_typed_aliases_and_rejects_bare_collision(self):
        other_world = World.objects.new_world(
            name="Sequence Padding",
            author=self.user,
            config=WorldConfig.objects.create(),
        )
        self.assertIsNotNone(other_world.config.starting_room_id)
        database_room = self.room.create_at("east")
        relative_room = self.create_imported_room(
            relative_id=database_room.id,
            x=200,
            name="Colliding Relative Room",
        )
        self.assertNotEqual(database_room, relative_room)

        explicit_alias = {
            "kind": "trigger",
            "spec": {
                "conditions": {
                    "eq": ["actor.room_id", f"room.{database_room.id}"],
                },
            },
        }
        normalized = (
            builder_world_export.normalize_manifest_room_references_for_import(
                world=self.world,
                manifest=explicit_alias,
            )
        )
        self.assertEqual(
            normalized["spec"]["conditions"]["eq"][1],
            format_room_manifest_ref(database_room),
        )
        coordinate_alias = {
            "kind": "trigger",
            "spec": {
                "conditions": {
                    "eq": [
                        "event.destination_room.ref",
                        legacy_room_coordinate_ref(database_room),
                    ],
                },
            },
        }
        normalized_coordinate = (
            builder_world_export.normalize_manifest_room_references_for_import(
                world=self.world,
                manifest=coordinate_alias,
            )
        )
        self.assertEqual(
            normalized_coordinate["spec"]["conditions"]["eq"][1],
            format_room_manifest_ref(database_room),
        )
        self.assertEqual(
            resolve_room_ref_id(
                world=self.world,
                value=f"room@{database_room.id}",
            ),
            relative_room.id,
        )

        bare_numeric = {
            "kind": "trigger",
            "spec": {
                "conditions": {
                    "eq": ["actor.room_id", database_room.id],
                },
            },
        }
        with self.assertRaisesRegex(serializers.ValidationError, "Bare numeric"):
            builder_world_export.normalize_manifest_room_references_for_import(
                world=self.world,
                manifest=bare_numeric,
            )

    def test_legacy_export_repair_rejects_unbounded_or_unicode_numeric_ids(self):
        for invalid_value in ("²", "9" * 5000):
            with self.subTest(invalid_value=invalid_value[:32]):
                with self.assertRaisesRegex(
                    serializers.ValidationError,
                    "positive 64-bit integers",
                ):
                    builder_world_export._canonicalize_room_ref(
                        invalid_value,
                        world=self.world,
                        allow_bare_database_id=True,
                    )

    def test_death_routing_requires_canonical_room_destination(self):
        compilation = compile_death_routing_policy(
            world=self.world,
            policy={
                "routes": [{
                    "when": {"always": True},
                    "destination": format_room_manifest_ref(self.room),
                }],
            },
        )
        self.assertEqual(
            compilation.routes[0].destination_room_id,
            self.room.id,
        )

        for noncanonical in (
            self.room.id,
            str(self.room.id),
            f"room.{self.room.id}",
            legacy_room_coordinate_ref(self.room),
        ):
            with self.subTest(noncanonical=noncanonical):
                with self.assertRaisesRegex(
                    DeathRoutingValidationError,
                    "canonical 'room@<relative_id>'",
                ):
                    compile_death_routing_policy(
                        world=self.world,
                        policy={
                            "routes": [{
                                "when": {"always": True},
                                "destination": noncanonical,
                            }],
                        },
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
