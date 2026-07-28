import uuid

from django.db import connection
from django.test.utils import CaptureQueriesContext

from builders.models import FACTION_TYPE_CORE, Faction
from config import constants as adv_consts
from core.death_routing import (
    DEATH_ROUTING_SOURCE_BASE_WORLD,
    DEATH_ROUTING_SOURCE_LOCAL,
    DeathRoutingValidationError,
    clear_compiled_plan_cache,
    compile_death_routing_policy,
    load_compiled_plan,
    replace_compiled_policy,
    resolve_death_destination,
)
from spawns.actions.combat import apply_player_death
from spawns.models import CharacterState, DeathResolutionReceipt, Item, Player
from tests.base import WorldTestCase
from tests.utils import apply_basic_stat_system
from worlds.models import (
    InstanceParticipant,
    Room,
    World,
    WorldConfig,
    Zone,
)


class TestDeterministicDeathRoutingRuntime(WorldTestCase):

    def setUp(self):
        clear_compiled_plan_cache()
        super().setUp()
        self.addCleanup(clear_compiled_plan_cache)
        apply_basic_stat_system(self.world)
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=["is_multiplayer"])
        self._next_room_x = 20

    def _create_room(self, world, name, *, zone=None):
        room = Room.objects.create(
            name=name,
            world=world,
            zone=zone or world.zones.first(),
            x=self._next_room_x,
            y=0,
            z=0,
        )
        self._next_room_x += 1
        return room

    def _route(self, when, destination):
        return {
            "when": when,
            "destination": f"room.{destination.id}",
        }

    def _state_route(self, key, value, destination):
        return self._route(
            {"eq": [f"state.character.{key}", value]},
            destination,
        )

    def _always_route(self, destination):
        return self._route({"always": True}, destination)

    def _install_policy(self, world, routes):
        compilation = compile_death_routing_policy(
            world=world,
            policy={"routes": routes},
        )
        replace_compiled_policy(
            world=world,
            config=world.config,
            compilation=compilation,
        )
        world.config.refresh_from_db()
        clear_compiled_plan_cache()
        return world.config

    def _set_character_state(self, player, data):
        CharacterState.objects.update_or_create(
            player=player,
            defaults={"data": data},
        )

    def _create_core_faction(self, code):
        return Faction.objects.create(
            world=self.world,
            code=code,
            name=code.title(),
            type=FACTION_TYPE_CORE,
            playable=True,
        )

    def _create_instance_template(
        self,
        *,
        routing_source=DEATH_ROUTING_SOURCE_LOCAL,
    ):
        instance_config = WorldConfig.objects.create(
            death_routing_source=routing_source,
        )
        instance_template = World.objects.new_world(
            name="The Deterministic Crossing",
            author=self.user,
            config=instance_config,
            is_multiplayer=True,
            instance_of=self.world,
        )
        apply_basic_stat_system(instance_template)
        return instance_template

    def _enter_instance(self, instance_template, *, player=None):
        player = player or self.player
        return World.enter_instance(
            player=player,
            transfer_to_id=instance_template.config.starting_room_id,
            transfer_from_id=player.room_id,
        )

    def test_unset_state_routes_first_death_and_later_state_selects_room(self):
        choice_room = self._create_room(self.world, "The Crossing")
        option_rooms = {
            option: self._create_room(self.world, f"The {option.title()} Hall")
            for option in ("ember", "tide", "stone", "wind")
        }
        self._install_policy(
            self.world,
            [
                self._state_route("afterlife_path", None, choice_room),
                *[
                    self._state_route(
                        "afterlife_path",
                        option,
                        destination,
                    )
                    for option, destination in option_rooms.items()
                ],
            ],
        )

        first_token = uuid.uuid4()
        first_player, _first_events = apply_player_death(
            player=self.player,
            death_token=first_token,
            forced=True,
        )

        self.assertEqual(first_player.room_id, choice_room.id)
        first_receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=first_token,
        )
        self.assertEqual(first_receipt.decision_reason, "ordered_route")
        self.assertEqual(first_receipt.matched_route_position, 0)

        self._set_character_state(
            self.player,
            {"afterlife_path": "stone"},
        )
        second_token = uuid.uuid4()
        second_player, _second_events = apply_player_death(
            player=self.player,
            death_token=second_token,
            forced=True,
        )

        self.assertEqual(second_player.room_id, option_rooms["stone"].id)
        second_receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=second_token,
        )
        self.assertEqual(second_receipt.decision_reason, "ordered_route")
        self.assertEqual(second_receipt.matched_route_position, 3)

    def test_authored_order_wins_when_rules_overlap(self):
        broad_room = self._create_room(self.world, "The Broad Hall")
        specific_room = self._create_room(self.world, "The Specific Hall")
        fail_safe = self._create_room(self.world, "The Last Hall")
        humans = self._create_core_faction("human")
        self.player.core_faction = humans
        self.player.archetype = adv_consts.ARCHETYPE_WARRIOR
        self.player.save(update_fields=["core_faction", "archetype"])
        broad = {
            "eq": ["player.archetype", adv_consts.ARCHETYPE_WARRIOR],
        }
        specific = {
            "all": [
                broad,
                {"eq": ["player.core_faction", "human"]},
            ],
        }
        self._install_policy(
            self.world,
            [
                self._route(broad, broad_room),
                self._route(specific, specific_room),
                self._always_route(fail_safe),
            ],
        )

        broad_result, _events = apply_player_death(
            player=self.player,
            death_token=uuid.uuid4(),
            forced=True,
        )

        self.assertEqual(broad_result.room_id, broad_room.id)

        self._install_policy(
            self.world,
            [
                self._route(specific, specific_room),
                self._route(broad, broad_room),
                self._always_route(fail_safe),
            ],
        )
        specific_token = uuid.uuid4()
        specific_result, _events = apply_player_death(
            player=self.player,
            death_token=specific_token,
            forced=True,
        )

        self.assertEqual(specific_result.room_id, specific_room.id)
        receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=specific_token,
        )
        self.assertEqual(receipt.matched_route_position, 0)

    def test_any_and_not_conditions_resolve_from_compiled_plan(self):
        any_room = self._create_room(self.world, "The Either Hall")
        not_room = self._create_room(self.world, "The Unbanished Hall")
        fallback_room = self._create_room(self.world, "The Banished Hall")
        config = self._install_policy(
            self.world,
            [
                self._route(
                    {
                        "any": [
                            {
                                "eq": [
                                    "state.character.afterlife_path",
                                    "ember",
                                ],
                            },
                            {
                                "eq": [
                                    "player.archetype",
                                    adv_consts.ARCHETYPE_WARRIOR,
                                ],
                            },
                        ],
                    },
                    any_room,
                ),
                self._route(
                    {
                        "not": {
                            "eq": [
                                "state.character.banished",
                                True,
                            ],
                        },
                    },
                    not_room,
                ),
                self._always_route(fallback_room),
            ],
        )
        plan = load_compiled_plan(config)

        any_from_state = resolve_death_destination(
            plan,
            core_faction_id=None,
            archetype=None,
            player_level=1,
            character_state={
                "afterlife_path": "ember",
                "banished": True,
            },
            origin_zone_id=self.zone.id,
        )
        any_from_archetype = resolve_death_destination(
            plan,
            core_faction_id=None,
            archetype=adv_consts.ARCHETYPE_WARRIOR,
            player_level=1,
            character_state={
                "afterlife_path": "tide",
                "banished": True,
            },
            origin_zone_id=self.zone.id,
        )
        not_match = resolve_death_destination(
            plan,
            core_faction_id=None,
            archetype=None,
            player_level=1,
            character_state={
                "afterlife_path": "tide",
                "banished": False,
            },
            origin_zone_id=self.zone.id,
        )
        fallback = resolve_death_destination(
            plan,
            core_faction_id=None,
            archetype=None,
            player_level=1,
            character_state={
                "afterlife_path": "tide",
                "banished": True,
            },
            origin_zone_id=self.zone.id,
        )

        self.assertEqual(any_from_state.room_id, any_room.id)
        self.assertEqual(any_from_state.matched_route_position, 0)
        self.assertEqual(any_from_archetype.room_id, any_room.id)
        self.assertEqual(any_from_archetype.matched_route_position, 0)
        self.assertEqual(not_match.room_id, not_room.id)
        self.assertEqual(not_match.matched_route_position, 1)
        self.assertEqual(fallback.room_id, fallback_room.id)
        self.assertEqual(fallback.matched_route_position, 2)

    def test_state_only_core_faction_group_shares_one_destination(self):
        shared_room = self._create_room(self.world, "The Shared Hall")
        default_room = self._create_room(self.world, "The Default Hall")
        humans = self._create_core_faction("human")
        orcs = self._create_core_faction("orc")
        self._install_policy(
            self.world,
            [
                self._route(
                    {
                        "in": [
                            "player.core_faction",
                            ["human", "orc"],
                        ],
                    },
                    shared_room,
                ),
                self._always_route(default_room),
            ],
        )
        orc_player = self.create_player(
            "Orc",
            user=self.create_user("orc@example.com"),
        )
        self.player.core_faction = humans
        self.player.save(update_fields=["core_faction"])
        orc_player.core_faction = orcs
        orc_player.save(update_fields=["core_faction"])

        human_token = uuid.uuid4()
        orc_token = uuid.uuid4()
        human_result, _events = apply_player_death(
            player=self.player,
            death_token=human_token,
            forced=True,
        )
        orc_result, _events = apply_player_death(
            player=orc_player,
            death_token=orc_token,
            forced=True,
        )

        self.assertEqual(human_result.room_id, shared_room.id)
        self.assertEqual(orc_result.room_id, shared_room.id)
        human_receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=human_token,
        )
        orc_receipt = DeathResolutionReceipt.objects.get(
            player=orc_player,
            death_token=orc_token,
        )
        self.assertEqual(human_receipt.matched_route_position, 0)
        self.assertEqual(orc_receipt.matched_route_position, 0)
        self.assertEqual(
            human_receipt.result["routing_inputs"]["core_faction_id"],
            humans.id,
        )
        self.assertEqual(
            orc_receipt.result["routing_inputs"]["core_faction_id"],
            orcs.id,
        )

    def test_archetype_routes_from_locked_player_scalar(self):
        mage_room = self._create_room(self.world, "The Mage Hall")
        default_room = self._create_room(self.world, "The Common Hall")
        self._install_policy(
            self.world,
            [
                self._route(
                    {
                        "eq": [
                            "player.archetype",
                        adv_consts.ARCHETYPE_WARRIOR,
                        ],
                    },
                    mage_room,
                ),
                self._always_route(default_room),
            ],
        )
        self.player.archetype = adv_consts.ARCHETYPE_WARRIOR
        self.player.save(update_fields=["archetype"])
        death_token = uuid.uuid4()

        result, _events = apply_player_death(
            player=self.player,
            death_token=death_token,
            forced=True,
        )

        receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=death_token,
        )
        self.assertEqual(result.room_id, mage_room.id)
        self.assertEqual(receipt.matched_route_position, 0)
        self.assertEqual(
            receipt.result["routing_inputs"]["archetype"],
            adv_consts.ARCHETYPE_WARRIOR,
        )

    def test_level_routes_from_locked_player_scalar_without_state_lock(self):
        exact_room = self._create_room(self.world, "The Fifth-Level Hall")
        grouped_room = self._create_room(self.world, "The Sixth Circle")
        lower_room = self._create_room(self.world, "The Lower Hall")
        upper_room = self._create_room(self.world, "The Upper Hall")
        config = self._install_policy(
            self.world,
            [
                self._route(
                    {"eq": ["player.level", 5]},
                    exact_room,
                ),
                self._route(
                    {"in": ["player.level", [6, 7]]},
                    grouped_room,
                ),
                self._route(
                    {"lte": ["player.level", 9]},
                    lower_room,
                ),
                self._route(
                    {"gte": ["player.level", 10]},
                    upper_room,
                ),
            ],
        )
        plan = load_compiled_plan(config)
        self.assertEqual(plan.required_state_paths, ())

        cases = [
            (5, exact_room, 0),
            (6, grouped_room, 1),
            (9, lower_room, 2),
            (10, upper_room, 3),
            (25, upper_room, 3),
            (None, self.world.config.death_room, None),
        ]
        with self.assertNumQueries(0):
            for level, expected_room, expected_position in cases:
                resolution = resolve_death_destination(
                    plan,
                    core_faction_id=None,
                    archetype=None,
                    player_level=level,
                    character_state={},
                    origin_zone_id=self.zone.id,
                )
                self.assertEqual(resolution.room_id, expected_room.id)
                self.assertEqual(
                    resolution.matched_route_position,
                    expected_position,
                )

        # Leave the caller's model stale to prove the locked database row is
        # authoritative for the death-routing decision.
        self.assertNotEqual(self.player.level, 10)
        Player.objects.filter(pk=self.player.pk).update(level=10)
        death_token = uuid.uuid4()
        with CaptureQueriesContext(connection) as captured:
            result, _events = apply_player_death(
                player=self.player,
                death_token=death_token,
                forced=True,
            )

        state_queries = [
            query["sql"]
            for query in captured
            if "spawns_characterstate" in query["sql"].lower()
        ]
        receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=death_token,
        )
        self.assertEqual(result.room_id, upper_room.id)
        self.assertEqual(receipt.matched_route_position, 3)
        self.assertEqual(
            receipt.result["routing_inputs"]["level"],
            10,
        )
        self.assertEqual(len(state_queries), 1, state_queries)
        self.assertNotIn("FOR UPDATE", state_queries[0].upper())

    def test_multiple_typed_state_keys_use_one_locked_state_read(self):
        string_impostor_room = self._create_room(
            self.world,
            "The String Impostor Hall",
        )
        typed_room = self._create_room(self.world, "The Typed State Hall")
        default_room = self._create_room(self.world, "The State Default Hall")
        self._install_policy(
            self.world,
            [
                self._route(
                    {
                        "all": [
                            {
                                "eq": [
                                    "state.character.oath_broken",
                                    "true",
                                ],
                            },
                            {
                                "eq": [
                                    "state.character.deaths_witnessed",
                                    "3",
                                ],
                            },
                        ],
                    },
                    string_impostor_room,
                ),
                self._route(
                    {
                        "all": [
                            {
                                "eq": [
                                    "state.character.oath_broken",
                                    True,
                                ],
                            },
                            {
                                "eq": [
                                    "state.character.deaths_witnessed",
                                    3,
                                ],
                            },
                            {
                                "eq": [
                                    "state.character.afterlife_path",
                                    "ember",
                                ],
                            },
                        ],
                    },
                    typed_room,
                ),
                self._always_route(default_room),
            ],
        )
        self._set_character_state(
            self.player,
            {
                "oath_broken": True,
                "deaths_witnessed": 3,
                "afterlife_path": "ember",
            },
        )
        death_token = uuid.uuid4()

        with CaptureQueriesContext(connection) as captured:
            result, _events = apply_player_death(
                player=self.player,
                death_token=death_token,
                forced=True,
            )

        state_queries = [
            query["sql"]
            for query in captured
            if "spawns_characterstate" in query["sql"].lower()
        ]
        receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=death_token,
        )
        self.assertEqual(result.room_id, typed_room.id)
        self.assertEqual(receipt.matched_route_position, 1)
        self.assertEqual(len(state_queries), 1, state_queries)

    def test_origin_zone_uses_authoritative_locked_room(self):
        graveyard_zone = Zone.objects.create(
            world=self.world,
            name="The Graveyard",
        )
        graveyard_room = self._create_room(
            self.world,
            "The Graveyard Gate",
            zone=graveyard_zone,
        )
        zone_destination = self._create_room(
            self.world,
            "The Graveyard Death Hall",
        )
        default_room = self._create_room(self.world, "The Other Death Hall")
        self._install_policy(
            self.world,
            [
                self._route(
                    {
                        "eq": [
                            "zone.id",
                            f"zone@{graveyard_zone.relative_id}",
                        ],
                    },
                    zone_destination,
                ),
                self._always_route(default_room),
            ],
        )
        self.player.room = graveyard_room
        self.player.save(update_fields=["room"])
        death_token = uuid.uuid4()

        result, _events = apply_player_death(
            player=self.player,
            origin_room=self.room,
            death_token=death_token,
            forced=True,
        )

        receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=death_token,
        )
        self.assertEqual(result.room_id, zone_destination.id)
        self.assertEqual(receipt.origin_room_id, graveyard_room.id)
        self.assertEqual(receipt.matched_route_position, 0)
        self.assertEqual(
            receipt.result["routing_inputs"]["origin_zone_id"],
            graveyard_zone.id,
        )

    def test_maximum_route_plan_resolves_warm_without_queries(self):
        destination = self._create_room(self.world, "The Last Route Hall")
        config = self._install_policy(
            self.world,
            [
                self._state_route(
                    "routing_value",
                    f"value_{position}",
                    destination,
                )
                for position in range(32)
            ],
        )
        plan = load_compiled_plan(config)

        with self.assertNumQueries(0):
            for _ in range(1000):
                resolution = resolve_death_destination(
                    plan,
                    core_faction_id=None,
                    archetype=adv_consts.ARCHETYPE_WARRIOR,
                    player_level=1,
                    character_state={"routing_value": "value_31"},
                    origin_zone_id=self.zone.id,
                )

        self.assertEqual(len(plan.routes), 32)
        self.assertEqual(resolution.room_id, destination.id)
        self.assertEqual(resolution.matched_route_position, 31)

    def test_compiler_rejects_malformed_dynamic_and_query_backed_conditions(self):
        destination = self._create_room(self.world, "The Invalid Route Hall")
        valid_route = self._state_route(
            "afterlife_path",
            "ember",
            destination,
        )
        cases = [
            (
                "malformed operands",
                {
                    "routes": [
                        self._route(
                            {
                                "eq": [
                                    "state.character.afterlife_path",
                                ],
                            },
                            destination,
                        ),
                    ],
                },
                "two-item list",
            ),
            (
                "dynamic right-hand value",
                {
                    "routes": [
                        self._state_route(
                            "afterlife_path",
                            "{player.archetype}",
                            destination,
                        ),
                    ],
                },
                "dynamic right-hand path",
            ),
            (
                "query-backed operator",
                {
                    "routes": [
                        self._route(
                            {
                                "item_present": {
                                    "location": "room",
                                    "item": 1,
                                },
                            },
                            destination,
                        ),
                    ],
                },
                "only supports always, all, any, not, eq, in, gte, and lte",
            ),
            (
                "non-final always",
                {
                    "routes": [
                        self._always_route(destination),
                        valid_route,
                    ],
                },
                "only allowed as true on the final route",
            ),
        ]

        for label, policy, error_pattern in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    DeathRoutingValidationError,
                    error_pattern,
                ):
                    compile_death_routing_policy(
                        world=self.world,
                        policy=policy,
                    )

    def test_compiler_rejects_unknown_archetype(self):
        destination = self._create_room(self.world, "The Unknown Class Hall")

        with self.assertRaisesRegex(
            DeathRoutingValidationError,
            "does not resolve to a base-world class profile",
        ):
            compile_death_routing_policy(
                world=self.world,
                policy={
                    "routes": [
                        self._route(
                            {
                                "eq": [
                                    "player.archetype",
                                    "chronomancer",
                                ],
                            },
                            destination,
                        ),
                    ],
                },
            )

    def test_compiler_rejects_invalid_level_conditions(self):
        destination = self._create_room(self.world, "The Level Error Hall")
        cases = [
            ("boolean", {"gte": ["player.level", True]}),
            ("float", {"gte": ["player.level", 10.5]}),
            ("string", {"gte": ["player.level", "10"]}),
            ("null", {"gte": ["player.level", None]}),
            ("zero", {"gte": ["player.level", 0]}),
            (
                "unsafe integer",
                {"gte": ["player.level", 9007199254740992]},
            ),
            (
                "dynamic value",
                {"gte": ["player.level", "{player.level}"]},
            ),
            (
                "invalid in member",
                {"in": ["player.level", [5, "6"]]},
            ),
        ]

        for label, condition in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    DeathRoutingValidationError,
                    "positive integer within the safe integer range",
                ):
                    compile_death_routing_policy(
                        world=self.world,
                        policy={
                            "routes": [
                                self._route(condition, destination),
                            ],
                        },
                    )

        with self.assertRaisesRegex(
            DeathRoutingValidationError,
            "gte is only supported for player.level",
        ):
            compile_death_routing_policy(
                world=self.world,
                policy={
                    "routes": [
                        self._route(
                            {
                                "gte": [
                                    "state.character.deaths_witnessed",
                                    10,
                                ],
                            },
                            destination,
                        ),
                    ],
                },
            )

    def test_compiler_enforces_route_value_policy_and_depth_bounds(self):
        destination = self._create_room(self.world, "The Bounded Route Hall")
        state_route = self._state_route(
            "afterlife_path",
            "ember",
            destination,
        )
        oversized_selector = [
            f"value_{index}"
            for index in range(33)
        ]
        oversized_literal_policy = {
            "routes": [
                self._route(
                    {
                        "in": [
                            f"state.character.route_{route_index}",
                            [
                                f"value_{route_index}_{value_index}"
                                for value_index in range(32)
                            ],
                        ],
                    },
                    destination,
                )
                for route_index in range(9)
            ],
        }
        over_nested_condition = {
            "eq": [
                "state.character.afterlife_path",
                "ember",
            ],
        }
        for _depth in range(17):
            over_nested_condition = {"not": over_nested_condition}

        cases = [
            (
                "route count",
                {"routes": [state_route for _index in range(33)]},
                "at most 32 routes",
            ),
            (
                "selector values",
                {
                    "routes": [
                        self._route(
                            {
                                "in": [
                                    "state.character.afterlife_path",
                                    oversized_selector,
                                ],
                            },
                            destination,
                        ),
                    ],
                },
                "at most 32 values",
            ),
            (
                "policy literal count",
                oversized_literal_policy,
                "policy limit of 256 literal values",
            ),
            (
                "condition depth",
                {
                    "routes": [
                        self._route(
                            over_nested_condition,
                            destination,
                        ),
                    ],
                },
                "maximum condition nesting depth of 16",
            ),
        ]

        for label, policy, error_pattern in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    DeathRoutingValidationError,
                    error_pattern,
                ):
                    compile_death_routing_policy(
                        world=self.world,
                        policy=policy,
                    )

    def test_duplicate_death_token_applies_penalty_and_sequences_once(self):
        destination = self._create_room(self.world, "The Death Hall")
        self._install_policy(
            self.world,
            [self._always_route(destination)],
        )
        self.world.config.death_mode = adv_consts.DEATH_MODE_LOSE_INV
        self.world.config.save(update_fields=["death_mode"])
        carried_item = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            name="a keepsake",
            type=adv_consts.ITEM_TYPE_INERT,
        )
        original_death_sequence = self.player.death_sequence
        original_location_sequence = self.player.location_sequence
        death_token = uuid.uuid4()
        self.player.health = 17
        self.player.energy = 13
        self.player.stamina = 11
        self.player.save(
            update_fields=["health", "energy", "stamina"],
        )

        with CaptureQueriesContext(connection) as captured:
            first_player, first_events = apply_player_death(
                player=self.player,
                death_token=death_token,
                forced=True,
            )
        first_player.refresh_from_db()
        self.assertEqual(
            (
                first_player.health,
                first_player.energy,
                first_player.stamina,
            ),
            (1, 1, 1),
        )
        first_player.health = 2
        first_player.energy = 3
        first_player.stamina = 4
        first_player.save(
            update_fields=["health", "energy", "stamina"],
        )
        retry_player, retry_events = apply_player_death(
            player=self.player,
            death_token=death_token,
            forced=True,
        )

        retry_player.refresh_from_db()
        carried_item.refresh_from_db()
        corpses = Item.objects.filter(
            world=self.spawn_world,
            type=adv_consts.ITEM_TYPE_CORPSE,
        )
        receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=death_token,
        )
        state_queries = [
            query["sql"]
            for query in captured
            if "spawns_characterstate" in query["sql"].lower()
        ]
        self.assertFalse(
            any("FOR UPDATE" in query.upper() for query in state_queries),
            state_queries,
        )
        self.assertTrue(first_events)
        self.assertEqual(retry_events, [])
        self.assertEqual(
            (
                retry_player.health,
                retry_player.energy,
                retry_player.stamina,
            ),
            (2, 3, 4),
        )
        self.assertEqual(
            first_player.death_sequence,
            original_death_sequence + 1,
        )
        self.assertEqual(
            retry_player.death_sequence,
            original_death_sequence + 1,
        )
        self.assertEqual(
            first_player.location_sequence,
            original_location_sequence + 1,
        )
        self.assertEqual(
            retry_player.location_sequence,
            original_location_sequence + 1,
        )
        self.assertEqual(corpses.count(), 1)
        self.assertEqual(carried_item.container_id, corpses.get().id)
        self.assertEqual(receipt.corpse_id, corpses.get().id)
        self.assertEqual(receipt.matched_route_position, 0)
        self.assertEqual(
            DeathResolutionReceipt.objects.filter(
                player=self.player,
                death_token=death_token,
            ).count(),
            1,
        )

    def test_instance_default_local_routing_keeps_participant_active(self):
        instance_template = self._create_instance_template()
        local_destination = self._create_room(
            instance_template,
            "The Local Death Hall",
        )
        self._install_policy(
            instance_template,
            [self._always_route(local_destination)],
        )
        spawned_instance = self._enter_instance(instance_template)
        participant = spawned_instance.instance_run.participants.get(
            player=self.player,
        )
        death_token = uuid.uuid4()
        self.player.health = 17
        self.player.energy = 13
        self.player.stamina = 11
        self.player.save(
            update_fields=["health", "energy", "stamina"],
        )

        result, _events = apply_player_death(
            player=self.player,
            death_token=death_token,
            forced=True,
        )

        participant.refresh_from_db()
        self.assertEqual(result.world_id, spawned_instance.id)
        self.assertEqual(result.room_id, local_destination.id)
        self.assertEqual(
            (result.health, result.energy, result.stamina),
            (1, 1, 1),
        )
        self.assertIsNone(participant.exited_at)
        self.assertIsNone(participant.exit_reason)
        self.assertEqual(
            participant.return_runtime_world_id,
            self.spawn_world.id,
        )
        receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=death_token,
        )
        self.assertEqual(receipt.routing_source, DEATH_ROUTING_SOURCE_LOCAL)
        self.assertEqual(receipt.destination_world_id, spawned_instance.id)
        self.assertEqual(receipt.matched_route_position, 0)

    def test_base_delegation_uses_recorded_runtime_and_origin_penalty(self):
        base_destination = self._create_room(
            self.world,
            "The Base World Death Hall",
        )
        self._install_policy(
            self.world,
            [self._always_route(base_destination)],
        )
        alternate_runtime = World.objects.create(
            name="Alternate Base Runtime",
            config=self.world.config,
            context=self.world,
            is_multiplayer=True,
        )
        self.player.world = alternate_runtime
        self.player.room = self.room
        self.player.save(update_fields=["world", "room"])
        surviving_item = Item.objects.create(
            world=alternate_runtime,
            container=self.player,
            name="a soulbound keepsake",
            type=adv_consts.ITEM_TYPE_INERT,
        )
        dropped_item = Item.objects.create(
            world=alternate_runtime,
            container=self.player.equipment,
            name="an iron sword",
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
        )
        self.player.equipment.weapon = dropped_item
        self.player.equipment.save(update_fields=["weapon"])

        instance_template = self._create_instance_template(
            routing_source=DEATH_ROUTING_SOURCE_BASE_WORLD,
        )
        instance_template.config.death_mode = adv_consts.DEATH_MODE_LOSE_EQ
        instance_template.config.save(update_fields=["death_mode"])
        spawned_instance = self._enter_instance(instance_template)
        participant = spawned_instance.instance_run.participants.get(
            player=self.player,
        )
        instance_origin_room = self.player.room
        death_token = uuid.uuid4()
        self.player.health = 17
        self.player.energy = 13
        self.player.stamina = 11
        self.player.save(
            update_fields=["health", "energy", "stamina"],
        )

        result, _events = apply_player_death(
            player=self.player,
            death_token=death_token,
            forced=True,
        )

        participant.refresh_from_db()
        result.refresh_from_db()
        result.equipment.refresh_from_db()
        surviving_item.refresh_from_db()
        dropped_item.refresh_from_db()
        receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=death_token,
        )
        corpse = Item.objects.get(pk=receipt.corpse_id)

        self.assertEqual(result.world_id, alternate_runtime.id)
        self.assertEqual(result.room_id, base_destination.id)
        self.assertEqual(
            (result.health, result.energy, result.stamina),
            (1, 1, 1),
        )
        self.assertIsNotNone(participant.exited_at)
        self.assertEqual(
            participant.exit_reason,
            InstanceParticipant.EXIT_REASON_DEATH_DELEGATED,
        )
        self.assertIsNone(participant.return_runtime_world_id)
        self.assertEqual(surviving_item.world_id, alternate_runtime.id)
        self.assertEqual(surviving_item.container, result)
        self.assertIsNone(result.equipment.weapon_id)
        self.assertEqual(corpse.world_id, spawned_instance.id)
        self.assertEqual(corpse.container_id, instance_origin_room.id)
        self.assertEqual(dropped_item.world_id, spawned_instance.id)
        self.assertEqual(dropped_item.container, corpse)
        self.assertEqual(
            receipt.routing_source,
            DEATH_ROUTING_SOURCE_BASE_WORLD,
        )
        self.assertEqual(receipt.origin_world_id, spawned_instance.id)
        self.assertEqual(receipt.destination_world_id, alternate_runtime.id)
        self.assertEqual(receipt.matched_route_position, 0)
        self.assertEqual(
            receipt.result["routing_inputs"]["origin_zone_id"],
            instance_origin_room.zone_id,
        )
        self.assertEqual(
            receipt.penalty["mode"],
            adv_consts.DEATH_MODE_LOSE_EQ,
        )

    def test_base_delegation_evaluates_base_conditional_routes(self):
        base_zone_room = self._create_room(
            self.world,
            "The Base Zone Death Hall",
        )
        conditional_room = self._create_room(
            self.world,
            "The Conditional Base Death Hall",
        )
        fallback_room = self._create_room(
            self.world,
            "The Base Default Death Hall",
        )
        humans = self._create_core_faction("human")
        self._install_policy(
            self.world,
            [
                self._route(
                    {
                        "eq": [
                            "zone.id",
                            f"zone@{self.zone.relative_id}",
                        ],
                    },
                    base_zone_room,
                ),
                self._route(
                    {
                        "all": [
                            {
                                "eq": [
                                    "player.core_faction",
                                    "human",
                                ],
                            },
                            {
                                "eq": [
                                    "player.archetype",
                                    adv_consts.ARCHETYPE_WARRIOR,
                                ],
                            },
                            {
                                "gte": [
                                    "player.level",
                                    10,
                                ],
                            },
                            {
                                "eq": [
                                    "state.character.afterlife_path",
                                    "ember",
                                ],
                            },
                        ],
                    },
                    conditional_room,
                ),
                self._always_route(fallback_room),
            ],
        )
        self.player.core_faction = humans
        self.player.archetype = adv_consts.ARCHETYPE_WARRIOR
        self.player.level = 12
        self.player.save(
            update_fields=["core_faction", "archetype", "level"],
        )
        self._set_character_state(
            self.player,
            {"afterlife_path": "ember"},
        )
        instance_template = self._create_instance_template(
            routing_source=DEATH_ROUTING_SOURCE_BASE_WORLD,
        )
        spawned_instance = self._enter_instance(instance_template)
        instance_origin_zone_id = self.player.room.zone_id
        death_token = uuid.uuid4()

        result, _events = apply_player_death(
            player=self.player,
            death_token=death_token,
            forced=True,
        )

        receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=death_token,
        )
        self.assertNotEqual(instance_origin_zone_id, self.zone.id)
        self.assertEqual(result.world_id, self.spawn_world.id)
        self.assertEqual(result.room_id, conditional_room.id)
        self.assertEqual(
            receipt.routing_source,
            DEATH_ROUTING_SOURCE_BASE_WORLD,
        )
        self.assertEqual(receipt.origin_world_id, spawned_instance.id)
        self.assertEqual(receipt.destination_world_id, self.spawn_world.id)
        self.assertEqual(receipt.matched_route_position, 1)
        self.assertEqual(receipt.core_faction_id, humans.id)
        self.assertEqual(
            receipt.result["routing_inputs"]["archetype"],
            adv_consts.ARCHETYPE_WARRIOR,
        )
        self.assertEqual(
            receipt.result["routing_inputs"]["level"],
            12,
        )
        self.assertEqual(
            receipt.result["routing_inputs"]["origin_zone_id"],
            instance_origin_zone_id,
        )

    def test_invalid_return_linkage_falls_back_locally_and_stays_active(self):
        base_destination = self._create_room(
            self.world,
            "The Unreachable Base Death Hall",
        )
        self._install_policy(
            self.world,
            [self._always_route(base_destination)],
        )
        instance_template = self._create_instance_template(
            routing_source=DEATH_ROUTING_SOURCE_BASE_WORLD,
        )
        local_fail_safe = self._create_room(
            instance_template,
            "The Local Fail-safe",
        )
        instance_template.config.death_room = local_fail_safe
        instance_template.config.save(update_fields=["death_room"])
        spawned_instance = self._enter_instance(instance_template)
        participant = spawned_instance.instance_run.participants.get(
            player=self.player,
        )
        foreign_config = WorldConfig.objects.create()
        foreign_world = World.objects.new_world(
            name="An Unrelated World",
            author=self.user,
            config=foreign_config,
            is_multiplayer=True,
        )
        foreign_runtime = foreign_world.create_spawn_world()
        participant.return_runtime_world = foreign_runtime
        participant.save(update_fields=["return_runtime_world"])
        death_token = uuid.uuid4()

        result, _events = apply_player_death(
            player=self.player,
            death_token=death_token,
            forced=True,
        )

        participant.refresh_from_db()
        receipt = DeathResolutionReceipt.objects.get(
            player=self.player,
            death_token=death_token,
        )
        self.assertEqual(result.world_id, spawned_instance.id)
        self.assertEqual(result.room_id, local_fail_safe.id)
        self.assertIsNone(participant.exited_at)
        self.assertIsNone(participant.exit_reason)
        self.assertEqual(
            participant.return_runtime_world_id,
            foreign_runtime.id,
        )
        self.assertEqual(
            receipt.routing_source,
            DEATH_ROUTING_SOURCE_BASE_WORLD,
        )
        self.assertEqual(receipt.decision_reason, "transport_fallback")
        self.assertEqual(receipt.fallback_reason, "invalid_return_runtime")
        self.assertIsNone(receipt.matched_route_position)
        self.assertEqual(receipt.destination_world_id, spawned_instance.id)
