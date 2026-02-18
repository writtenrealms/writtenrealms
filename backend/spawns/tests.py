from datetime import datetime, timedelta
import json
import mock

from config import constants as adv_consts
from backend.core.drops import generate_equipment

from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from django.utils import timezone

from rest_framework.reverse import reverse

from config import constants as api_consts
from backend.config.exceptions import ServiceError
from core.db import get_redis_db
from builders.models import (
    ItemTemplate,
    ItemTemplateInventory,
    MobTemplate,
    MobTemplateInventory,
    TransformationTemplate,
    Loader,
    Quest,
    Objective,
    Reward,
    Rule,
    RoomCommandCheck,
    Path,
    PathRoom,
    Faction,
    FactionAssignment,
    Procession,
    Skill)
from spawns import serializers as spawns_serializers
from spawns.extraction import APIExtractor
from spawns.loading import LoaderRun
from spawns.models import (
    Alias,
    Player,
    Item,
    Equipment,
    Mob,
    RoomCommandCheckState,
    PlayerQuest,
    PlayerTrophy,
    PlayerConfig)
from spawns.services import WorldGate
from system.models import IntroConfig
from tests.base import WorldTestCase
from users.models import User
from worlds.models import World, Room, WorldConfig
from worlds.services import WorldSmith

"""
Animation notes:

* world info
    - first world frame
    - zones
    - rooms
        - command checks
        - get triggers
        - room checks


* loaders:
    - mobs
        - quests
        - mob eq
    - items
* players
    - items
    - player eq
"""


class APIExtractionTests(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.spawn_world = self.world.create_spawn_world()
        self.player = Player.objects.create(
            world=self.spawn_world,
            name='Player',
            room=self.room,
            user=self.user)


class APIExtractionSaveItemsTests(APIExtractionTests):

    def test_inv_extraction(self):
        "Simplest form of extraction, an item to inventory"
        rock = Item.objects.create(
            name='a rock',
            world=self.spawn_world,
            container=self.room)

        api_extractor = APIExtractor(
            self.spawn_world,
            [{
                    'model': 'item',
                    'id': str(rock.id),
                    'container_type': 'player',
                    'container_id': str(self.player.id),
                }])
        api_extractor.save_items(self.player)
        rock.refresh_from_db()

        self.assertEqual(rock.container, self.player)

    def test_equip_weapon(self):

        # Start with a sword in the room
        sword = Item.objects.create(
            name='a sword',
            world=self.spawn_world,
            container=self.room)

        # Load a sword in the equipment (implying a get sword;wi sword)
        # Load a rock in inventory (implying get rock)
        api_extractor = APIExtractor(
            self.spawn_world,
            [
                {
                    'model': 'item',
                    'id': str(sword.id),
                    'container_type': 'player',
                    'container_id': str(self.player.id),
                },
                {
                    "id": str(self.player.id),
                    "model": "equipment",
                    "weapon": str(sword.id),
                    "offhand": None,
                    "head": None,
                    "body": None,
                    "arms": None,
                    "hands": None,
                    "waist": None,
                    "legs": None,
                    "feet": None,
                    "shoulders": None
                },
            ])
        api_extractor.save_items(self.player)
        api_extractor.simple_save('equipment')
        sword.refresh_from_db()
        self.assertEqual(sword.container, self.player.equipment)
        self.player.equipment.refresh_from_db()
        self.assertEqual(self.player.equipment.weapon, sword)

    def test_item_removal(self):
        rock = Item.objects.create(
            name='a rock',
            world=self.spawn_world,
            container=self.player)
        api_extractor = APIExtractor(self.spawn_world, [])
        api_extractor.save_items(self.player)
        rock.refresh_from_db()
        self.assertTrue(rock.is_pending_deletion)

    def test_create_corpse_extraction(self):
        "Seeing a corpse reference should delete its associated mob"
        soldier = Mob.objects.create(world=self.world, room=self.room)
        soldier_corpse = Item.objects.create(
            world=self.world,
            container=soldier,
            name='the corpse of a soldier')

        api_extractor = APIExtractor(
            self.spawn_world,
            [
                {
                    'id': str(soldier_corpse.id),
                    'model': 'item',
                    'type': 'corpse',
                    'name': 'the corpse of a soldier',
                    'corpse_id': str(soldier.id),
                    'container_type': 'room',
                    'container_id': str(self.player.room.id),
                },
            ])
        api_extractor.save_items(self.player)

        soldier.refresh_from_db()
        self.assertTrue(soldier.is_pending_deletion)

        item = Item.objects.get()
        self.assertEqual(item.name, 'the corpse of a soldier')

    def test_mpw_container_drop(self):
        bag = Item.objects.create(
            name='a bag',
            world=self.spawn_world,
            container=self.player)
        rock = Item.objects.create(
            name='a rock',
            world=self.spawn_world,
            container=bag)
        api_extractor = APIExtractor(self.spawn_world, [])
        api_extractor.save_items(self.player)
        bag.refresh_from_db()
        rock.refresh_from_db()
        self.assertTrue(bag.is_pending_deletion)
        self.assertTrue(rock.is_pending_deletion)

    def test_spw_container_purge(self):
        chest = Item.objects.create(
            name='a chest',
            world=self.spawn_world,
            container=self.room)
        rock = Item.objects.create(
            name='a rock',
            world=self.spawn_world,
            container=chest)

        api_extractor = APIExtractor(self.spawn_world, [])
        api_extractor.save_items(self.spawn_world)
        chest.refresh_from_db()
        rock.refresh_from_db()
        self.assertTrue(chest.is_pending_deletion)
        self.assertTrue(rock.is_pending_deletion)

    def test_get_item_from_container_then_drop_item_then_purge(self):
        """
        Player was carrying a bag with a rock. Gets the rock, drops the bag,
        and purges.
        """
        bag = Item.objects.create(
            name='a bag',
            world=self.spawn_world,
            container=self.player)
        rock = Item.objects.create(
            name='a rock',
            world=self.spawn_world,
            container=bag)

        api_extractor = APIExtractor(self.spawn_world, [
            {
                'model': 'item',
                'id': str(rock.id),
                'container_type': 'player',
                'container_id': str(self.player.id)
            }
        ])
        api_extractor.save_items(self.spawn_world)
        bag.refresh_from_db()
        rock.refresh_from_db()
        self.assertTrue(bag.is_pending_deletion)
        self.assertEqual(rock.container, self.player)

    def test_completion_from_bag(self):
        """
        Regression tests for completing a quest with items in a bag and then
        exiting the game immediately.
        """
        bag = Item.objects.create(
            name='a bag',
            world=self.spawn_world,
            container=self.player)
        rock = Item.objects.create(
            name='a rock',
            world=self.spawn_world,
            container=bag)

        self.assertTrue(rock in bag.inventory.all())

        api_extractor = APIExtractor(
            self.spawn_world,
            [{
                'model': 'item',
                'id': str(bag.id),
                'container_type': 'player',
                'container_id': str(self.player.id),
            }])
        api_extractor.save_items(self.player)

        rock.refresh_from_db()
        self.assertTrue(rock.is_pending_deletion)


class APIExtractionPersistentItemTests(APIExtractionTests):

    def test_persistent_container_extraction(self):
        rock = Item.objects.create(
            name='a rock',
            world=self.spawn_world,
            container=self.room)

        chest = Item.objects.create(
            name='a chest',
            world=self.spawn_world,
            container=self.room,
            is_persistent=True)

        api_extractor = APIExtractor(
            self.spawn_world,
            [
                {
                    'model': 'item',
                    'id': str(rock.id),
                    'container_type': 'item',
                    'container_id': str(chest.id),
                },
                {
                    'model': 'item',
                    'id': str(chest.id),
                    'container_type': 'room',
                    'container_id': str(self.room.id),
                }
            ])
        api_extractor.extract_persistent_items()
        rock.refresh_from_db()
        chest.refresh_from_db()
        self.assertEqual(chest.container, self.room)
        self.assertFalse(chest.is_pending_deletion)
        self.assertTrue(chest.is_persistent)
        self.assertEqual(rock.container, chest)
        self.assertFalse(rock.is_pending_deletion)

    def test_persistent_container_purge(self):
        chest = Item.objects.create(
            name='a chest',
            world=self.spawn_world,
            container=self.room,
            is_persistent=True)
        rock = Item.objects.create(
            name='a rock',
            world=self.spawn_world,
            container=chest)
        api_extractor = APIExtractor(self.spawn_world, [])
        api_extractor.extract_persistent_items()
        chest.refresh_from_db()
        rock.refresh_from_db()
        self.assertTrue(chest.is_pending_deletion)
        self.assertTrue(rock.is_pending_deletion)


class APIExtractionPlayerTests(APIExtractionTests):

    def test_viewed_rooms_extraction(self):
        room = Room.objects.create(
            world=self.world,
            x=1, y=0, z=0,
            name='New Room',
            zone=self.zone)
        self.assertEqual(self.player.viewed_rooms.count(), 0)
        self.player.viewed_rooms.add(self.room)
        self.assertEqual(self.player.viewed_rooms.count(), 1)

        api_extractor = APIExtractor(
            self.spawn_world,
            [{
                'model': 'viewed_rooms',
                'player_id': self.player.id,
                'room_ids': [str(room.id)]
            }])
        api_extractor.save_viewed_rooms(self.player)
        self.assertEqual(self.player.viewed_rooms.count(), 2)

    def test_factions_extraction(self):
        human_faction = Faction.objects.create(
            is_core=True,
            code='human',
            name='Human',
            world=self.world)
        templar_faction = Faction.objects.create(
            is_core=False,
            code='templar',
            name='Templar',
            world=self.world)
        illuminati_faction = Faction.objects.create(
            is_core=False,
            code='illuminati',
            name='Illuminati',
            world=self.world)
        # Give human core faction. We'll try to increase that standing
        # but since core factions can't be altered, it will do nothing
        FactionAssignment.objects.create(
            faction=human_faction,
            member_type=ContentType.objects.get_for_model(self.player),
            member_id=self.player.id,
            value=1)
        # Give templar chosen faction. We'll increase that.
        FactionAssignment.objects.create(
            faction=templar_faction,
            member_type=ContentType.objects.get_for_model(self.player),
            member_id=self.player.id,
            value=1)

        # We also decrease Illuminati faction, which was not previously
        # on there.
        api_extractor = APIExtractor(
            self.spawn_world,
            [{
                'model': 'factions',
                'player_id': self.player.id,
                'factions': {
                    'core': 'human',
                    'human': 3,
                    'templar': 5,
                    'illuminati': -2
                }
            }])
        api_extractor.save_factions(self.player)
        # Human standing is unchanged
        self.assertEqual(
            self.player.faction_assignments.get(
                faction__code='human').value,
            1)
        # Templars has gone up
        self.assertEqual(
            self.player.faction_assignments.get(
                faction__code='templar').value,
            5)
        # Illuminati is a new entry
        self.assertEqual(
            self.player.faction_assignments.get(
                faction__code='illuminati').value,
            -2)

    def test_skills_extraction(self):
        api_extractor = APIExtractor(
            self.spawn_world,
            [
                {
                    "model": "skills",
                    "player_id": self.player.id,
                    "skills": {
                        "flex": {
                            "1": "barrier",
                            "2": "innervate",
                            "3": "combust"
                        },
                        "feat": {
                            "1": "linguist",
                            "2": "freeze",
                            "3": "kaboom"
                        }
                    }
                }
            ])
        api_extractor.save_skills(self.player)
        # Check that the flex skills all line up
        self.assertEqual(
            self.player.flex_skills.get(code='barrier').number, 1)
        self.assertEqual(
            self.player.flex_skills.get(number=2).code, 'innervate')
        self.assertEqual(self.player.flex_skills.count(), 3)
        # Check that the feats all line up
        self.assertEqual(
            self.player.feats.get(code='linguist').number, 1)
        self.assertEqual(
            self.player.feats.get(number=2).code, 'freeze')
        self.assertEqual(self.player.feats.count(), 3)

    def test_trophy_extraction(self):
        # A soldier which had been previously killed once
        soldier_template = MobTemplate.objects.create(
            name='a soldier',
            world=self.world)
        PlayerTrophy.objects.create(
            player=self.player,
            mob_template=soldier_template)
        # A sergeant which not previously been killed
        sergeant_template = MobTemplate.objects.create(
            name='a sergeant',
            world=self.world)

        trophy = {}
        trophy[soldier_template.id] = 2
        api_extractor = APIExtractor(
            self.spawn_world,
            [{
                'model': 'trophy',
                'player_id': self.player.id,
                'trophy': trophy,
            }])
        api_extractor.save_trophy(self.player)
        self.assertEqual(
            self.player.trophy_entries.filter(
                mob_template=soldier_template).count(),
            2)

    def test_aliases_extraction(self):
        # Create one new alias and update another

        alias = Alias.objects.create(
            player=self.player,
            match='x',
            replacement='kill guard')

        api_extractor = APIExtractor(
            self.spawn_world,
            [
                {
                    "model": "aliases",
                    "player_id": "283",
                    "aliases": {
                        "lc": {
                            "id": alias.id,
                            "match": "x",
                            "replacement": "kill soldier"
                        },
                        "j": {
                            "match": "j",
                            "replacement": "where"
                        }
                    }
                }
            ])
        api_extractor.save_aliases(self.player)
        self.assertEqual(self.player.aliases.count(), 2)


class APIExtractionSinglePlayerWorldTests(APIExtractionTests):

    def test_command_checks_extraction(self):
        """
        Start with 4 command checks, 2 with no states and 2 with a passed
        state. One null becomes passed and one passed is not in the extraction
        data. At the end, we expect 1 null and 3 passed (but all recorded)
        """

        # Null, will create a new record with None passed_ts
        cmd_check_1 = RoomCommandCheck.objects.create(
            room=self.room,
            allow_commands='cmd.get',
            check=adv_consts.ROOM_CHECK_IN_INV,
            argument='item_template.1',
            failure_msg="You can't do anything until you get the thing!",
            track_state=True)
        # Null, will be set to passed
        cmd_check_2 = RoomCommandCheck.objects.create(
            room=self.room,
            allow_commands='cmd.get',
            check=adv_consts.ROOM_CHECK_IN_INV,
            argument='item_template.2',
            failure_msg="You can't do anything until you get the thing!",
            track_state=True)
        # Passed, won't change
        cmd_check_3 = RoomCommandCheck.objects.create(
            room=self.room,
            allow_commands='cmd.get',
            check=adv_consts.ROOM_CHECK_IN_INV,
            argument='item_template.3',
            failure_msg="You can't do anything until you get the thing!",
            track_state=True)
        RoomCommandCheckState.objects.create(
            world=self.spawn_world,
            cmd_check=cmd_check_3,
            passed_ts=timezone.now())
        # Passed, won't be mentioned and still won't change
        cmd_check_4 = RoomCommandCheck.objects.create(
            room=self.room,
            allow_commands='cmd.get',
            check=adv_consts.ROOM_CHECK_IN_INV,
            argument='item_template.4',
            failure_msg="You can't do anything until you get the thing!",
            track_state=True)
        RoomCommandCheckState.objects.create(
            world=self.spawn_world,
            cmd_check=cmd_check_4,
            passed_ts=timezone.now())

        api_extractor = APIExtractor(
            self.spawn_world,
            [
                {
                    "id": str(cmd_check_1.id),
                    "model": "room_cmd_check",
                    "state": None
                },
                {
                    "id": str(cmd_check_2.id),
                    "model": "room_cmd_check",
                    "state": 'passed'
                },
                {
                    "id": str(cmd_check_3.id),
                    "model": "room_cmd_check",
                    "state": 'passed'
                },
            ])
        api_extractor.save_command_checks()
        check_states = self.spawn_world.world_check_states.all()
        self.assertEqual(check_states.count(), 4)
        self.assertIsNone(check_states.get(cmd_check=cmd_check_1).passed_ts)
        self.assertIsNotNone(check_states.get(cmd_check=cmd_check_3).passed_ts)

    @mock.patch('spawns.extraction.api_consts')
    def test_mobs_extraction(self, mock_api_consts):
        mock_api_consts.MOB_DELETION_DELAY = 0

        new_room = Room.objects.create(
            world=self.world,
            x=1, y=0, z=0,
            name='New Room',
            zone=self.zone)
        # Will end up unchanged
        mob1 = Mob.objects.create(world=self.spawn_world, room=self.room)
        # Will end up moved
        mob2 = Mob.objects.create(world=self.spawn_world, room=self.room)
        # Will end up deleted
        mob3 = Mob.objects.create(world=self.spawn_world, room=self.room)

        api_extractor = APIExtractor(
            self.spawn_world,
            [
                {
                    "id": str(mob1.id),
                    "model": "mob",
                    "room": str(self.room.id),
                    "health": 1,
                    "stamina": 100,
                    "mana": 14
                },
                {
                    "id": str(mob2.id),
                    "model": "mob",
                    "room": str(new_room.id),
                    "health": 1,
                    "stamina": 100,
                    "mana": 14
                },
            ])
        api_extractor.save_mobs()
        self.assertEqual(
            self.spawn_world.mobs.filter(is_pending_deletion=False).count(),
            2)

        mob3.refresh_from_db()
        self.assertTrue(mob3.is_pending_deletion)

        mob2.refresh_from_db()
        self.assertEqual(mob2.room, new_room)


class TestLoaders(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.spawn_world = self.world.create_spawn_world()

    def test_basic_usage(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)

        loader_run = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=False)

        output = loader_run.execute()
        self.assertFalse(loader_run.executed)

        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room)

        output = loader_run.execute()
        self.assertTrue(loader_run.executed)
        self.assertEqual(len(output.keys()), 1)
        self.assertEqual(len(output[rule.id]), 1)

        # Trying to re-run an executed loader raises an error
        with self.assertRaises(RuntimeError):
            loader_run.execute()

    def test_inherit_zone_wait(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=True)

        loader_run = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=False,
            should_zone_reset=True)

        output = loader_run.execute()
        self.assertFalse(loader_run.executed)

        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room)

        output = loader_run.execute()
        self.assertTrue(loader_run.executed)
        self.assertEqual(len(output.keys()), 1)
        self.assertEqual(len(output[rule.id]), 1)

    def test_load_item(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room)

        # Not checking to not trigger Game World lookups
        spawns = loader.run(self.spawn_world, check=False)

        self.assertEqual(len(spawns), 1) # only one rule
        rule_spawns = spawns[rule.id]
        self.assertEqual(len(rule_spawns), 1) # only 1 spawn
        item = rule_spawns[0]
        self.assertEqual(item.template, item_template)

    def test_load_mob_in_room(self):
        HEALTH_MAX = 10
        mob_template = MobTemplate.objects.create(
            world=self.world,
            health_max=HEALTH_MAX)
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.room)

        # Not checking to not trigger Game World lookups
        spawns = loader.run(self.spawn_world, check=False)

        self.assertEqual(len(spawns), 1) # only one rule
        rule_spawns = spawns[rule.id]
        self.assertEqual(len(rule_spawns), 1) # only 1 spawn
        mob = rule_spawns[0]
        self.assertEqual(mob.template, mob_template)
        self.assertEqual(mob.world, self.spawn_world)
        self.assertEqual(mob.health, HEALTH_MAX)
        # Check that we're tracking the rule
        self.assertEqual(mob.rule, rule)
        # And since we're loading to a room, roams is None
        self.assertIsNone(mob.roams)

    def test_load_mob_in_zone(self):
        "Tests that loading into a zone sets roaming to zone"
        mob_template = MobTemplate.objects.create(world=self.world)

        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            respawn_wait=0)
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.zone)

        spawns = loader.run(self.spawn_world, check=False)
        mob = spawns[rule.id][0]
        # Mobs that load in a zone are set to roam that zone.
        self.assertEqual(mob.roams, self.zone)

    def test_load_mob_in_path(self):
        mob_template = MobTemplate.objects.create(world=self.world)

        path = Path.objects.create(name='path', world=self.world)

        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            respawn_wait=0)
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=path)

        # If no room is in the path, no loading happens
        self.assertEqual(path.rooms.count(), 0)
        spawns = loader.run(self.spawn_world, check=False)
        self.assertEqual(len(spawns[rule.id]), 0)

        # Add a room so we have an actual path
        PathRoom.objects.create(room=self.room, path=path)

        spawns = loader.run(self.spawn_world, check=False)
        mob = spawns[rule.id][0]
        # Mobs that load in a zone are set to roam that zone.
        self.assertEqual(mob.roams, path)

    def test_reload_to_room(self):
        mob_template = MobTemplate.objects.create(world=self.world)
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            respawn_wait=0)
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.room,
            num_copies=2)

        output = loader.run(self.spawn_world, check=False)
        self.assertEqual(len(output[rule.id]), 2)

        # This time we're going to supply population data to the loader,
        # indicating that there is already one copy of this mob loaded
        # (but not two). So one more run should only load one.
        # So that we can initiate the population data, we're going to use the
        # LoaderRun object directly rather than Loader.run.
        population_data = {'rules': {}}
        population_data['rules'][rule.id] = [output[rule.id][0].id]
        loader_run = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=True,
            population_data=population_data)

        output = loader_run.execute()
        self.assertEqual(len(output[rule.id]), 1)

    def test_reload_to_zone(self):
        mob_template = MobTemplate.objects.create(world=self.world)
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            respawn_wait=0)
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.zone,
            num_copies=2)

        output = loader.run(self.spawn_world, check=False)
        self.assertEqual(len(output[rule.id]), 2)

        # This time we're going to supply population data to the loader,
        # indicating that there is already one copy of this mob loaded
        # (but not two). So one more run should only load one.
        # So that we can initiate the population data, we're going to use the
        # LoaderRun object directly rather than Loader.run.
        population_data = {'rules': {}}
        population_data['rules'][rule.id] = [output[rule.id][0].id]
        loader_run = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=True,
            population_data=population_data)

        output = loader_run.execute()
        self.assertEqual(len(output[rule.id]), 1)

    def test_reload_mob_in_zone(self):
        mob_template = MobTemplate.objects.create(world=self.world)
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            respawn_wait=0)
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.zone,
            num_copies=2)

        output = loader.run(self.spawn_world, check=False)
        self.assertEqual(len(output[rule.id]), 2)

        # This time we're going to supply population data to the loader,
        # indicating that there is already one copy of this mob loaded
        # (but not two). So one more run should only load one.
        # So that we can initiate the population data, we're going to use the
        # LoaderRun object directly rather than Loader.run.
        population_data = {'rules': {}}
        population_data['rules'][rule.id] = [output[rule.id][0].id]
        loader_run = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=True,
            population_data=population_data)

        output = loader_run.execute()
        self.assertEqual(len(output[rule.id]), 1)

    def test_load_mob_with_inventory_item(self):
        mob_template = MobTemplate.objects.create(world=self.world)
        item_template = ItemTemplate.objects.create(world=self.world)
        MobTemplateInventory.objects.create(
            item_template=item_template,
            container=mob_template,
            num_copies=2)
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.room)

        spawns = loader.load(self.spawn_world, check=False)
        self.assertEqual(len(spawns), 1)
        rule_spawns = spawns[rule.id]
        mob = rule_spawns[0]
        self.assertEqual(mob.inventory.count(), 3)
        mob_inventory = mob.inventory.all()
        self.assertEqual(mob_inventory[0].template, item_template)
        self.assertEqual(mob_inventory[1].template, item_template)
        self.assertEqual(mob_inventory[2].template, None)

    def test_nested_loads(self):
        mob_template = MobTemplate.objects.create(world=self.world)
        bag_template = ItemTemplate.objects.create(
            world=self.world,
            name='a bag',
            type=adv_consts.ITEM_TYPE_CONTAINER)
        apple_template = ItemTemplate.objects.create(
            world=self.world,
            name='an apple',
            type=adv_consts.ITEM_TYPE_CONSUMABLE)
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)

        rule1 = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.room,
            num_copies=2)
        rule2 = Rule.objects.create(
            loader=loader,
            template=bag_template,
            target=rule1)
        rule3 = Rule.objects.create(
            loader=loader,
            template=apple_template,
            target=rule2,
            num_copies=3)

        # Not checking to not trigger Game World lookups
        output = loader.run(self.spawn_world, check=False)

        mobs = output[rule1.pk]
        self.assertEqual(len(mobs), 2)
        self.assertEqual(mobs[0].template, mob_template)

        mob_inventory = mobs[1].inventory.all()
        self.assertEqual(len(mob_inventory), 2) # 1 corpse, 1 bag
        self.assertEqual(mob_inventory[1].template, bag_template)

        bag_inventory = mob_inventory[1].inventory.all()
        self.assertEqual(len(bag_inventory), 3)
        self.assertEqual(bag_inventory[2].template, apple_template)

    def test_nested_items(self):
        """
        Tests loaders with items nested both via rule and via template
        inventory.
        """
        # Load a bag that always loads with an apple
        bag_template = ItemTemplate.objects.create(
            world=self.world,
            name='a bag',
            type=adv_consts.ITEM_TYPE_CONTAINER)
        apple_template = ItemTemplate.objects.create(
            world=self.world,
            name='an apple',
            type=adv_consts.ITEM_TYPE_CONSUMABLE)
        ItemTemplateInventory.objects.create(
            item_template=apple_template,
            container=bag_template)

         # Add a rock to the load
        rock_template = ItemTemplate.objects.create(
            world=self.world,
            name='an apple',
            type=adv_consts.ITEM_TYPE_CONSUMABLE)

        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)

        rule1 = Rule.objects.create(
            loader=loader,
            template=bag_template,
            target=self.room,
            num_copies=1)
        rule2 = Rule.objects.create(
            loader=loader,
            template=rock_template,
            target=rule1)

         # Loader returning 2 things, the bag and the rock (but not the apple)
        output = loader.run(self.spawn_world, check=False)
        self.assertEqual(len(output), 2)
        self.assertEqual(len(output[rule1.pk]), 1)
        bag = output[rule1.pk][0]
        self.assertEqual(bag.template, bag_template)
        self.assertEqual(len(output[rule2.pk]), 1)
        rock = output[rule2.pk][0]
        self.assertEqual(rock.template, rock_template)
        self.assertEqual(bag.inventory.first().template, apple_template)
        self.assertEqual(bag.inventory.last().template, rock_template)

    def test_reloading_item(self):
        """
        Tests running a loader for two items in a room where there is one of
        them.
        """
        item_template = ItemTemplate.objects.create(
            world=self.world, name='a rock')
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room,
            num_copies=2)

        # runner = LoaderRun(loader, self.spawn_world, check=True)
        # if not isinstance(runner.rdb, TestDB):
        #     raise RuntimeError("Non-test DB being used in a test")

        # Get one item in
        item = item_template.spawn(self.room, self.spawn_world, rule=rule)
        self.assertEqual(Item.objects.count(), 1)

        # For the actual run, pass in the population data
        population_data = {'rules': {}}
        population_data['rules'][rule.id] = [item.id]
        runner = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=True,
            population_data=population_data)

        # One more added got added, since 1 was already there.
        output = runner.execute()
        self.assertEqual(Item.objects.count(), 2)
        spawns = output[1]
        self.assertEqual(len(spawns), 1)
        self.assertEqual(spawns[0].template, item_template)

    def test_reloading_item_in_room(self):
        """
        Tests a room that's supposed to have two items, has one removed and
        the loader is rerun.
        """
        item_template = ItemTemplate.objects.create(
            world=self.world, name='a rock')
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            respawn_wait=0)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room,
            num_copies=2)

        output = LoaderRun(loader, self.spawn_world, check=False).execute()
        self.assertEqual(len(output[rule.pk]), 2)
        self.assertEqual(len(self.room.inventory.all()), 2)

        self.room.inventory.all()[1].delete()

        population_data = {'rules': {}}
        population_data['rules'][rule.id] = [output[rule.id][0].id]
        runner = LoaderRun(
            loader,
            self.spawn_world,
            check=True,
            population_data=population_data)
        output = runner.execute()
        self.assertEqual(len(output[rule.pk]), 1)
        self.assertEqual(len(self.room.inventory.all()), 2)

    def test_respawn_wait_and_forcing(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            respawn_wait=60)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room)

        # First run loads an item
        runner = LoaderRun(loader, self.spawn_world, check=False)
        output = runner.execute()
        self.assertEqual(len(output), 1)
        self.assertEqual(Item.objects.count(), 1)

        # Second immediate run does not
        runner = LoaderRun(loader, self.spawn_world, check=False)
        output = runner.execute()
        self.assertEqual(len(output), 0)
        self.assertEqual(Item.objects.count(), 1)

        # Third run does because we force
        runner = LoaderRun(loader, self.spawn_world, check=False)
        output = runner.execute(force=True)
        self.assertEqual(len(output), 1)
        self.assertEqual(Item.objects.count(), 2)

        # If set set the last processing ts in the past, we get another item
        loader.last_processing_ts = (
            loader.last_processing_ts - timedelta(days=1))
        loader.save()
        runner = LoaderRun(loader, self.spawn_world, check=False)
        output = runner.execute()
        self.assertEqual(len(output), 1)
        self.assertEqual(Item.objects.count(), 3)

    @mock.patch('spawns.loading.LoaderRun.get_num_from_templates_in_room')
    def test_max_target_all_count(self, mock_get_num):
        spawn_world = self.world.create_spawn_world()

        item_template = ItemTemplate.objects.create(world=self.world)
        # Make sure the loader is worldwide
        loader = Loader.objects.create(world=self.world,
                                       zone=self.zone,
                                       respawn_wait=0,
                                       inherit_zone_wait=False)
        # Make sure the room targets nothing (hence the world)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            num_copies=1)

        # Make sure there is already one item in the room
        item_template.spawn(self.room, spawn_world)
        self.assertEqual(self.room.inventory.count(), 1)

        mock_get_num.return_value = 1

        # running the loader doesn't cause the count to increase
        loader.run(spawn_world)
        self.assertEqual(self.room.inventory.count(), 1)

    def test_load_mob_template_with_inventory(self):
        mob_template = MobTemplate.objects.create(world=self.world)
        item_template = ItemTemplate.objects.create(world=self.world)
        MobTemplateInventory.objects.create(
            container=mob_template,
            item_template=item_template)

        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)
        rule = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.room)

        # Not checking to not trigger Game World lookups
        spawns = loader.load(self.spawn_world, check=False)

        self.assertEqual(len(spawns), 1)
        mob = spawns[rule.pk][0] # Get the first spawn
        self.assertEqual(mob.template, mob_template)
        self.assertEqual(mob.inventory.all()[0].template, item_template)
        # Second is the corpse

    def test_loader_condition(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False,
            conditions='fact_check foo bar')
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room)

        loader_run = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=False)
        output = loader_run.execute()
        # Loader was executed but no items were loaded because the
        # condition was false.
        self.assertTrue(loader_run.executed)
        self.assertEqual(len(output.keys()), 0)

        self.spawn_world.facts = json.dumps({'foo': 'bar'})
        self.spawn_world.save()
        loader_run = LoaderRun(
            loader=loader,
            world=self.spawn_world,
            check=False)
        output = loader_run.execute()
        self.assertTrue(loader_run.executed)
        self.assertEqual(len(output.keys()), 1)

    # Instance tests

    def test_load_item_in_instance(self):
        instance_context = self.create_instance()
        instance = World.enter_instance(
            player=self.player,
            transfer_to_id=self.instance_room.id,
            transfer_from_id=self.room.id)

        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')
        loader = Loader.objects.create(
            world=instance_context,
            zone=self.zone,
            inherit_zone_wait=False)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room)

        # Not checking to not trigger Game World lookups
        spawns = loader.run(self.spawn_world, check=False)

        self.assertEqual(len(spawns), 1) # only one rule
        rule_spawns = spawns[rule.id]
        self.assertEqual(len(rule_spawns), 1) # only 1 spawn
        item = rule_spawns[0]
        self.assertEqual(item.template, item_template)


class SpawnRewardTests(WorldTestCase):

    def test_spawn_item_reward(self):
        spawn_world = self.world.create_spawn_world()

        mob_template = MobTemplate.objects.create(world=self.world)
        sword_template = ItemTemplate.objects.create(world=self.world,
                                                     name='a sword')
        quest = Quest.objects.create(world=self.world,
                                     mob_template=mob_template)
        from django.contrib.contenttypes.models import ContentType
        reward = Reward.objects.create(
            quest=quest,
            type=adv_consts.REWARD_TYPE_ITEM,
            profile_type=ContentType.objects.get(model='itemtemplate'),
            profile_id=sword_template.id)
        mob = mob_template.spawn(target=self.room, spawn_world=spawn_world)

        self.make_system_user()
        self.client.force_authenticate(self.user)
        player = Player.objects.create(
            world=spawn_world,
            room=self.room,
            user=self.user,
            name='John',
            in_game=True)

        ep = reverse('spawn-rewards', args=[reward.pk])
        resp = self.client.post(ep, {'player_id': player.id})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.data['animation_data']), 1)
        data = resp.data['animation_data'][0]

        item = Item.objects.get(pk=data['id'])
        self.assertEqual(item.template, sword_template)


class TestDeletions(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.spawn_world = self.world.create_spawn_world()
        self.player = Player.objects.create(
            world=self.spawn_world,
            room=self.room,
            user=self.user,
            name='John')
        self.item = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            name='a rock')

    def test_delete_equipment(self):
        "Tests that deleting players and mobs deletes their respective eq"
        eq = self.player.equipment
        self.player.delete()
        with self.assertRaises(Equipment.DoesNotExist):
            Equipment.objects.get(pk=eq.pk)

        mob = Mob.objects.create(world=self.spawn_world, room=self.room)
        eq = mob.equipment
        mob.delete()
        with self.assertRaises(Equipment.DoesNotExist):
            Equipment.objects.get(pk=eq.pk)

    def test_delete_item_in_inventory(self):
        "Ensure deleting an item in a player's inv doesn't remove the player"
        self.item.delete()
        player = Player.objects.get(pk=self.player.pk)

    def test_equipped_item(self):
        self.player.equipment.weapon = self.item
        self.player.equipment.save()
        eq = self.player.equipment
        self.item.delete()
        eq = Equipment.objects.get(pk=eq.pk)

    def test_delete_item_in_room(self):
        self.item.container = self.room
        self.item.save()
        self.item.delete()
        room = Room.objects.get(pk=self.room.pk)

    def test_delete_spw_after_moving_player(self):
        # Bug I ran into with spw reset
        self.assertFalse(self.spawn_world.is_multiplayer)

        player = self.player
        other_room = player.room.create_at('east')
        player.viewed_rooms.add(other_room)

        original_world = self.spawn_world
        new_world = self.spawn_world.context.create_spawn_world()

        player.world = new_world
        player.room = new_world.config.starting_room
        player.save()
        player.initialize(reset=True)

        original_world.delete()

        player = Player.objects.get(pk=player.pk)

    def test_delete_mob_with_inventory(self):
        """
        Regression test. Because mobs that load with equipment
        may get looted, and because we clean up mobs from the API
        that have died, items being synced during players extraction
        sometimes run into reference errors on the API side.
        """
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name='a soldier')
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='an apple')
        MobTemplateInventory.objects.create(
            container=mob_template,
            item_template=item_template)

        mob = mob_template.spawn(target=self.room,
                                 spawn_world=self.spawn_world)

        corpse = mob.inventory.filter(
            type=adv_consts.ITEM_TYPE_CORPSE)
        # items_pks = mob.inventory.values_list('pk', flat=True)
        # self.assertEqual(len(items_pks), 2)

        prior_items_num = Item.objects.count()
        mob.delete()
        self.assertEqual(Item.objects.count(), prior_items_num)

    def test_delete_mob_with_equipment(self):
        "Equipment flavor of the above test"
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name='a soldier')

        mob = mob_template.spawn(target=self.room,
                                 spawn_world=self.spawn_world)

        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a helmet')
        helmet = item_template.spawn(
            target=mob,
            spawn_world=self.spawn_world)

        mob.equipment.equip(helmet, 'head')

        prior_items_num = Item.objects.count()
        mob.delete()
        self.assertEqual(Item.objects.count(), prior_items_num)


class TestCorpseSpawnSerialization(WorldTestCase):
    """
    Test for bug in single player worlds where corpses aren't animating
    properly.
    """

    def test_corpse_serialization(self):
        spawn_world = self.world.create_spawn_world()

        item = Item.objects.create(
            name='the corpse of a spider',
            type='corpse',
            world=spawn_world,
            container=self.room)

        data = spawns_serializers.AnimateItemSerializer(item).data
        self.assertEqual(data['name'], 'the corpse of a spider')


class TestOneButtonPlay(WorldTestCase):

    def setUp(self):
        super().setUp()
        IntroConfig.objects.create(world=self.world)

    def test_play(self):
        resp = self.client.get(reverse('logged-in-user'))
        self.assertEqual(resp.status_code, 401)

        resp = self.client.post(reverse('game-play'), {})
        self.assertEqual(resp.status_code, 201)

        player = Player.objects.get(pk=resp.data['player']['id'])
        self.assertEqual(resp.data['player']['key'], player.key)
        self.assertEqual(player.world.context, self.world)

        token = resp.data['token']
        self.assertEqual(len(token.split('.')), 3)

        # Make sure that they can access a protected resource
        self.client.credentials(HTTP_AUTHORIZATION='JWT %s' % token)
        resp = self.client.get(reverse('logged-in-user'))
        self.assertEqual(resp.status_code, 200)


class TestGameLookup(WorldTestCase):

    def setUp(self):
        super().setUp()

        self.spawn_world = self.world.create_spawn_world()

        self.player = Player.objects.create(
            name='John', level=1, experience=1,
            room=self.room, world=self.spawn_world, user=self.user)

        self.item = Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            name='a rock')

        self.client.force_authenticate(self.user)
        self.endpoint = reverse('game-lookup', args=[self.item.key])

    # Success tests

    def test_lookup_item(self):
        """
        Successful case w/ integration. The test happens via player.lookup
        rather than the endpoint because the endpoint initializes a new test
        RDB, which means that it will be wiped and the test data will be gone
        by the time the test is run. With the lookup function, we can pass
        in the rdb.
        """
        self.player.in_game = True
        self.player.save()

        data = self.player.game_lookup(self.item.key)
        self.assertEqual(data['name'], 'a rock')

    def test_lookup_mob(self):
        self.player.in_game = True
        self.player.save()

        mob_template = MobTemplate.objects.create(
            world=self.world,
            name='a soldier')
        mob = mob_template.spawn(self.room, self.spawn_world)

        data = self.player.game_lookup(mob.key)
        self.assertEqual(data['name'], 'a soldier')

    # Failure tests

    def test_player_not_in_world_error(self):
        resp = self.client.get(self.endpoint, HTTP_X_PLAYER_ID=self.player.id)
        self.assertEqual(resp.status_code, 403)

    def test_player_not_users(self):
        "Tests that the player has to belong to a user"
        user2 = User.objects.create_user('john@example.com', 'p')
        self.player.user = user2
        self.player.save()

        resp = self.client.get(self.endpoint, HTTP_X_PLAYER_ID=self.player.id)
        self.assertEqual(resp.status_code, 403)

    def test_not_found(self):
        self.player.in_game = True
        self.player.save()
        # Test for invalid type
        endpoint = reverse('game-lookup', args=['room.whatever'])
        resp = self.client.get(endpoint, HTTP_X_PLAYER_ID=self.player.id)
        self.assertEqual(resp.status_code, 404)
        # Test for invalid id
        endpoint = reverse('game-lookup', args=['item.dne'])
        resp = self.client.get(endpoint, HTTP_X_PLAYER_ID=self.player.id)
        self.assertEqual(resp.status_code, 404)


class TestLoadTemplate(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.make_system_user()
        self.client.force_authenticate(self.user)
        # self.spawn_world = self.world.create_spawn_world()
        # self.player = Player.objects.create(
        #     name='John', level=1, experience=1,
        #     room=self.room, world=self.spawn_world, user=self.user)

    def test_load_item(self):
        rock_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')

        resp = self.client.post(reverse('load-template'), {
            'world_id': self.spawn_world.id,
            'template_type': 'item',
            'template_id': rock_template.id,
            'actor_type': 'player',
            'actor_id': self.user.id,
            'player': self.player.id,
            'room': self.player.room.id,
        })
        self.assertEqual(resp.status_code, 201)

        # An item got created
        item = Item.objects.first()

        # Make sure the animation data return is good
        data = resp.data
        #self.assertEqual(data[0]['key'], self.spawn_world.key)
        #self.assertEqual(data[1]['key'], item.key)
        self.assertEqual(data['key'], item.key)

    def test_load_mob(self):
        wolf_template = MobTemplate.objects.create(
            world=self.world, name='a wolf')

        resp = self.client.post(reverse('load-template'), {
            'world_id': self.spawn_world.id,
            'template_type': 'mob',
            'template_id': wolf_template.id,
            'actor_type': 'player',
            'actor_id': self.user.id,
            'player': self.player.id,
            'room': self.player.room.id,
        })
        self.assertEqual(resp.status_code, 201)

        # a mob got created
        mob = Mob.objects.first()
        self.assertEqual(mob.key, mob.key)

    # Failure tests

    def test_load_from_wrong_world(self):
        new_world = World.objects.create()
        rock_template = ItemTemplate.objects.create(
            world=new_world, name='a rock')

        resp = self.client.post(reverse('load-template'), {
            'world_id': new_world.id,
            'template_type': 'item',
            'template_id': rock_template.id,
            'actor_type': 'player',
            'actor_id': self.user.id,
            'player': self.player.id,
            'room': self.player.room.id,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['non_field_errors'][0],
                         'Template does not belong to this world')

    # Instance tests

    def test_load_item_in_instance(self):
        self.create_instance()
        instance = World.enter_instance(
            player=self.player,
            transfer_to_id=self.instance_room.id,
            transfer_from_id=self.room.id)

        rock_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')

        resp = self.client.post(reverse('load-template'), {
            'world_id': instance.id,
            'template_type': 'item',
            'template_id': rock_template.id,
            'actor_type': 'player',
            'actor_id': self.user.id,
            'player': self.player.id,
            'room': self.player.room.id,
        })
        self.assertEqual(resp.status_code, 201)

        # An item got created
        item = Item.objects.first()

        # Make sure the animation data return is good
        data = resp.data
        self.assertEqual(data['key'], item.key)

    def test_load_mob_in_instance(self):
        self.create_instance()
        instance = World.enter_instance(
            player=self.player,
            transfer_to_id=self.instance_room.id,
            transfer_from_id=self.room.id)

        wolf_template = MobTemplate.objects.create(
            world=self.world, name='a wolf')

        resp = self.client.post(reverse('load-template'), {
            'world_id': instance.id,
            'template_type': 'mob',
            'template_id': wolf_template.id,
            'actor_type': 'player',
            'actor_id': self.user.id,
            'player': self.player.id,
            'room': self.player.room.id,
        })
        self.assertEqual(resp.status_code, 201)

        # a mob got created
        mob = Mob.objects.first()
        self.assertEqual(mob.key, mob.key)


class TestRandomDrops(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.make_system_user()
        self.client.force_authenticate(self.user)
        self.spawn_world = self.world.create_spawn_world()
        self.ep = reverse('generate-drops')

    def test_generate_random_drop(self):
        # Run through a bunch of generation and make sure nothing errors out
        eq_types = list(adv_consts.EQUIPMENT_TYPES)
        eq_types.remove(adv_consts.EQUIPMENT_TYPE_ACCESSORY)
        for eq_type in eq_types:
            for level in range(1, 21):
                for quality in adv_consts.ITEM_QUALITIES:
                    for i in range(1, 101):
                        generate_equipment(
                            level=level,
                            quality=quality,
                            eq_type=eq_type)

    def test_failures(self):
        resp = self.client.post(self.ep, {})
        self.assertEqual(resp.status_code, 400)

    def test_load_into_dead_mob(self):
        """
        When loading an item into a mob, we have to create an item that
        does not have a container, because the corpse it will be loaded
        into will not exist at the
        """

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name='a soldier')

        resp = self.client.post(self.ep, {
            'level': 1,
            'quality': adv_consts.ITEM_QUALITY_IMBUED,
            'world': mob.world.id,
        }, format='json')
        self.assertEqual(resp.status_code, 201)

        data = resp.data
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['key'], mob.world.key)
        self.assertIsNone(data[1]['in_container'])
        self.assertEqual(data[1]['quality'], adv_consts.ITEM_QUALITY_IMBUED)
        self.assertEqual(data[1]['type'], adv_consts.ITEM_TYPE_EQUIPPABLE)


class TestTemplateTransformation(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.spawn_world = self.world.create_spawn_world()

    def test_transformed_mob_animation(self):
        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            inherit_zone_wait=False)
        mob_template = MobTemplate.objects.create(
            world=self.world,
            regen_rate=4)
        transformation_template = TransformationTemplate.objects.create(
            transformation_type=api_consts.TRANSFORMATION_TYPE_ATTR,
            arg1='regen_rate',
            arg2='1')

        rule1 = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.room)

        rule2 = Rule.objects.create(
            loader=loader,
            template=transformation_template,
            target=rule1)

        output = loader.run(world=self.spawn_world, check=False)
        mob = output[rule1.pk][0]

        mob_data = spawns_serializers.AnimateMobSerializer(mob).data
        self.assertEqual(mob_data['regen_rate'], '1')


class TestItemBoost(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.spawn_world = self.world.create_spawn_world()

    def test_item_boost(self):
        item = Item.objects.create(
            world=self.spawn_world,
            health_max=100)
        item.boost()
        self.assertEqual(item.health_max, 120)


class TestPlayerConfig(WorldTestCase):

    def setUp(self):
        super().setUp()
        spawn_world = self.world.create_spawn_world()
        self.player = Player.objects.create(
            world=spawn_world,
            name='Player',
            room=self.room,
            user=self.user)
        self.client.force_authenticate(self.user)

        # Default config
        self.assertEqual(self.player.config.id, 1)

        self.player.in_game = True
        self.player.save()

        self.headers = {'HTTP_X_PLAYER_ID': self.player.id}

    def test_test_config(self):

        # New player setting their config for the first time

        self.assertEqual(self.player.config.id, 1)

        resp = self.client.post(reverse('game-player-config'), {
            'room_brief': True,
            'combat_brief': True
        }, **self.headers)
        self.assertEqual(resp.status_code, 201)

        self.player.refresh_from_db()
        self.assertEqual(self.player.config.id, 2) # new config was created
        self.assertTrue(self.player.config.room_brief)
        self.assertTrue(self.player.config.combat_brief)

        # Change once-set config
        resp = self.client.post(reverse('game-player-config'), {
            'room_brief': False,
            'combat_brief': True
        }, **self.headers)
        self.assertEqual(resp.status_code, 201)
        self.player.refresh_from_db()
        self.assertTrue(self.player.config.combat_brief)
        self.assertEqual(self.player.config.id, 2)


# Service Tests

class EnterWorldTests(WorldTestCase):

    @mock.patch('config.game_settings.IS_CLUSTER', False)
    def test_enter_spw(self):
        self.assertFalse(self.spawn_world.is_multiplayer)
        WorldGate(world=self.spawn_world, player=self.player).enter()
        self.assertTrue(self.player.in_game)

    @mock.patch('config.game_settings.IS_CLUSTER', False)
    def test_enter_mpw(self):
        self.world.is_multiplayer = True
        self.world.save()
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save()
        WorldGate(world=self.spawn_world, player=self.player).enter()
        self.assertTrue(self.player.in_game)

    def test_cannot_enter_storing_spw(self):
        self.spawn_world.lifecycle = api_consts.WORLD_STATE_STORING
        self.spawn_world.save()
        with self.assertRaises(ServiceError) as context:
            WorldGate(world=self.spawn_world, player=self.player).enter()
        self.assertEqual(str(context.exception),
                        "World cannot be entered in 'storing' state.")

    @mock.patch('config.game_settings.IS_CLUSTER', False)
    def test_cannot_log_in_multiple_chars_to_mpw(self):
        self.world.is_multiplayer = True
        self.world.save()
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save()

        # Enter with 1st player
        WorldGate(world=self.spawn_world, player=self.player).enter()

        # Create 2nd player and attempt to also enter the world
        player2 = self.create_player('Joe2')
        with self.assertRaises(ServiceError) as context:
            WorldGate(world=self.spawn_world, player=player2).enter()
        self.assertEqual(str(context.exception),
                         "You are logged on another character.")

        # Also test that linked users are taken into account for login
        self.user.link_id = 1
        self.user.save()
        player2.in_game = False
        player2.save()
        second_account = User.objects.create_user(
            'second@example.com', 'p',
            link_id=1)
        player3 = Player.objects.create(
            world=self.spawn_world,
            name='Third',
            room=self.room,
            user=second_account,
            in_game=True,
            last_action_ts=timezone.now())

        with self.assertRaises(ServiceError) as context:
            WorldGate(world=self.spawn_world, player=player3).enter()
        self.assertEqual(str(context.exception),
                         "You are logged on another character.")


class ExitWorldTests(WorldTestCase):

    @mock.patch('config.game_settings.IS_CLUSTER', False)
    def test_exit_mpw(self):
        self.world.is_multiplayer = True
        self.world.save()
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save()

        rdb = self.spawn_world.rdb

        WorldGate(world=self.spawn_world, player=self.player).enter()
        game_player = rdb.fetch(self.player.key)

        # Exiting the world saves the player's data
        game_player.experience = 2
        WorldGate(world=self.spawn_world, player=self.player).exit()
        self.player.refresh_from_db()
        self.assertEqual(self.player.experience, 2)
        self.assertFalse(self.player.in_game)

        # The world remains running
        self.spawn_world.refresh_from_db()
        self.assertEqual(self.spawn_world.lifecycle, api_consts.WORLD_STATE_RUNNING)

        # A timing has been added to delete the player in the game world
        timing = json.loads(rdb.redis.zrange('timings', 0, -1)[0])
        self.assertEqual(timing['type'], 'timing.delete')

    @mock.patch('config.game_settings.IS_CLUSTER', False)
    def test_exit_spw(self):
        self.assertFalse(self.spawn_world.is_multiplayer)
        WorldGate(world=self.spawn_world, player=self.player).enter()
        game_player = self.spawn_world.rdb.fetch(self.player.key)

        # Exiting the world saves the player's data
        game_player.experience = 2
        WorldGate(world=self.spawn_world, player=self.player).exit()
        self.player.refresh_from_db()
        self.assertEqual(self.player.experience, 2)
        self.assertFalse(self.player.in_game)

        # The world is stored
        self.spawn_world.refresh_from_db()
        self.assertEqual(self.spawn_world.lifecycle, api_consts.WORLD_STATE_STORED)

        # The game world no longer exists
        self.assertIsNone(self.spawn_world.game_world)


class InstanceExtractionTests(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.create_instance()

    def test_faction_extract(self):

        templars = Faction.objects.create(
            world=self.world,
            name='Templar',
            code='templar')

        FactionAssignment.objects.create(
            member=self.player,
            faction=templars,
            value=1)

        instance = World.enter_instance(
            player=self.player,
            transfer_to_id=self.instance_room.id,
            transfer_from_id=self.room.id)

        extraction_data = [
            {
                "id": "128",
                "model": "player",
                "key": "player.128",
                "room": self.room.id,
                "experience": 450003,
                "level": 19,
                "health": 200234,
                "mana": 16,
                "stamina": 100,
                "gold": 100,
                "glory": 0,
                "medals": 0,
                "title": "The Builder",
                "mute_list": None,
                "channels": "chat",
                "is_invisible": True,
                "last_action_ts": "2024-08-07T23:40:49.425178"
            },
            {
                "model": "factions",
                "player_id": "128",
                "factions": {
                    "core": "human",
                    "templar": 10
                }
            },
        ]

        from spawns.extraction import APIExtractor
        extractor = APIExtractor(world=instance,
                                 extraction_data=extraction_data)
        extractor.extract_player(self.player)

        assignment = FactionAssignment.objects.get(
            member_id=self.player.id,
            member_type=ContentType.objects.get(model='player'),
            faction=templars)
        self.assertEqual(assignment.value, 10)
