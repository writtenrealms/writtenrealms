import mock

from config import constants as adv_consts

from rest_framework.test import APITestCase
from rest_framework.reverse import reverse

from config import constants as api_consts
from builders.models import Faction, ItemDefinition
from spawns.models import Player
from tests.base import WorldTestCase
from users.models import User
from worlds.models import World, Room


class TestCreatePlayerCharacter(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.endpoint = reverse('lobby-world-chars', args=[self.world.pk])

    def test_create_spw_char(self):
        "Tests a user creating a single player world character"
        # Minimal creation
        resp = self.client.post(self.endpoint, {'name': 'John'})
        self.assertEqual(resp.status_code, 201)

        player = self.user.characters.get(name='John')
        self.assertEqual(player.name, 'John')
        self.assertEqual(player.title, '')
        self.assertEqual(player.user, self.user)

        world = player.world
        self.assertEqual(world.name, 'An Island')
        self.assertEqual(world.context, self.world)

        self.assertEqual(world.lifecycle, api_consts.WORLD_STATE_NEW)

    def test_uniqueness(self):
        "Tests that character names are unique in SPW (but not in MPW)"

        self.world.is_public = True
        self.world.save()

        spawn_world = self.world.create_spawn_world()

        # IN SPW, can repeat

        player = Player.objects.create(
            name='John',
            user=self.user,
            world=spawn_world,
            room=self.room)

        resp = self.client.post(self.endpoint, {'name': 'John'})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            Player.objects.filter(
                world__context_id=self.world.id,
                name='John').count(),
            2)

        # In MPW, cannot repeat

        mpw_world = World.objects.new_world(name='MPW', is_multiplayer=True,
            author=self.user)
        spawn_world = mpw_world.create_spawn_world()
        player = Player.objects.create(
            name='John',
            user=self.user,
            world=spawn_world,
            room=self.room)

        resp = self.client.post(
            reverse('lobby-world-chars', args=[mpw_world.pk]),
            {'name': 'John'})
        self.assertEqual(resp.status_code, 400)

    def test_mpw_name_normalization(self):
        "Test that only one word is kept for a name, capitalized"
        self.world.is_multiplayer = True
        self.world.save()
        resp = self.client.post(self.endpoint, {'name': 'jOhn Smith'})
        self.assertEqual(resp.status_code, 201)
        player = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(player.name, 'John')
        # Title got set to Smith
        self.assertEqual(player.title, 'Smith')

        # Trying to add another with the same first name fails
        resp = self.client.post(self.endpoint, {'name': 'jOhn Doe'})
        self.assertEqual(resp.status_code, 400)

    def test_intro_name(self):
        resp = self.client.post(self.endpoint, {'name': 'An adventurer'})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            Player.objects.get(pk=resp.data['id']).name,
            'An adventurer')

    def test_list_spw_chars(self):
        """
        Tests that getting the list of characters from a root SPW gives
        the list not of its own characters but that of its spawned worlds
        that belong to that user.
        """

        resp = self.client.post(self.endpoint, {'name': 'Lindsay'})
        self.assertEqual(resp.status_code, 201)

        resp = self.client.post(self.endpoint, {'name': 'Annie'})
        self.assertEqual(resp.status_code, 201)

        resp = self.client.get(self.endpoint)
        self.assertEqual(resp.status_code, 200)
        results = resp.data['results']
        # 1 more because the base test class creates a player
        self.assertEqual(len(results), 3)
        # Most recently created char is listed first
        self.assertEqual(results[0]['name'], 'Annie')
        self.assertEqual(results[1]['name'], 'Lindsay')

    def test_create_mpw_char(self):
        self.world.is_multiplayer = True
        self.world.save()

        resp = self.client.post(self.endpoint, {'name': 'John'})
        self.assertEqual(resp.status_code, 201)
        john = Player.objects.get(pk=resp.data['id'])

        resp = self.client.post(self.endpoint, {'name': 'Jack'})
        self.assertEqual(resp.status_code, 201)
        jack = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(jack.name, 'Jack')

        self.assertEqual(john.world, jack.world)
        self.assertTrue(john.world.is_multiplayer)

        # Test that you can't pick the same name in MPWs
        resp = self.client.post(self.endpoint, {'name': 'Jack'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['name'][0], "This name is already taken.")

    def test_create_mpw_char_while_in_instance(self):
        """
        Regression test for the scenario where a player enters an instance
        and then tries to create a character in the base world with the same
        name, which should result in an error.
        """
        self.world.is_multiplayer = True
        self.world.save()
        instance = World.objects.new_world(
            name='An Instance',
            author=self.user,
            is_multiplayer=True,
            instance_of=self.world,
        )
        instance_spawn_world = instance.create_spawn_world()

        resp = self.client.post(self.endpoint, {'name': 'John'})
        self.assertEqual(resp.status_code, 201)
        john = Player.objects.get(pk=resp.data['id'])

        john.world = instance_spawn_world
        john.in_game = True
        john.save(update_fields=['world', 'in_game'])

        resp = self.client.post(self.endpoint, {'name': 'John'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                         "Cannot create a character while in an instance.")

    def _starting_item_definition(self, *, slug, name, equipment_type=None):
        base_properties = {}
        item_type = adv_consts.ITEM_TYPE_INERT
        if equipment_type:
            item_type = adv_consts.ITEM_TYPE_EQUIPPABLE
            base_properties["equipment_type"] = equipment_type
        return ItemDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            item_type=item_type,
            base_properties=base_properties,
        )

    def _set_starting_equipment(self, entries):
        self.world.config.starting_equipment = entries
        self.world.config.save(update_fields=["starting_equipment"])

    def test_starting_equipment(self):
        sword_definition = self._starting_item_definition(
            slug='sword',
            name='a sword',
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H)
        helmet_definition = self._starting_item_definition(
            slug='helmet',
            name='a helmet',
            equipment_type=adv_consts.EQUIPMENT_TYPE_HEAD)
        compass_definition = self._starting_item_definition(
            slug='compass',
            name='a compass')
        self._set_starting_equipment([
            {'item_definition': f'itemdefinition.{sword_definition.slug}', 'count': 1},
            {'item_definition': f'itemdefinition.{helmet_definition.slug}', 'count': 1},
            {'item_definition': f'itemdefinition.{compass_definition.slug}', 'count': 1},
        ])

        resp = self.client.post(self.endpoint, {'name': 'John'})
        john = Player.objects.get(pk=resp.data['id'])

        self.assertEqual(john.equipment.weapon.definition, sword_definition)
        self.assertEqual(john.equipment.head.definition, helmet_definition)
        self.assertTrue(john.inventory.filter(definition=compass_definition).exists())

    def test_assassin_starting_equipment(self):
        """
        Regression test, makes sure that an assassin doesn't start with a
        2H weapon
        """
        sword_definition = self._starting_item_definition(
            slug='greatsword',
            name='a sword',
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_2H)
        self._set_starting_equipment([
            {'item_definition': f'itemdefinition.{sword_definition.slug}', 'count': 1},
        ])
        resp = self.client.post(self.endpoint, {
            'archetype': adv_consts.ARCHETYPE_ASSASSIN,
            'name': 'John'
        })
        john = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(john.archetype, adv_consts.ARCHETYPE_ASSASSIN)
        self.assertIsNone(john.equipment.weapon)

    def test_assassin_start_dual_wield(self):
        """
        Regression test, makes sure that an assassin doesn't start with a
        2H weapon
        """
        dagger_definition = self._starting_item_definition(
            slug='dagger',
            name='a dagger',
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H)
        self._set_starting_equipment([
            {'item_definition': f'itemdefinition.{dagger_definition.slug}', 'count': 2},
        ])
        resp = self.client.post(self.endpoint, {
            'archetype': adv_consts.ARCHETYPE_ASSASSIN,
            'name': 'Assassin'
        })
        john = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(john.archetype, adv_consts.ARCHETYPE_ASSASSIN)
        self.assertEqual(john.equipment.weapon.definition, dagger_definition)
        self.assertEqual(john.equipment.offhand.definition, dagger_definition)

    def test_class_specific_starting_equipment(self):
        # For both
        helmet_definition = self._starting_item_definition(
            slug='class-helmet',
            name='a helmet',
            equipment_type=adv_consts.EQUIPMENT_TYPE_HEAD)

        # for warriors
        sword_definition = self._starting_item_definition(
            slug='warrior-sword',
            name='a sword',
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_2H)

        # for assassins
        dagger_definition = self._starting_item_definition(
            slug='assassin-dagger',
            name='a dagger',
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H)
        self._set_starting_equipment([
            {'item_definition': f'itemdefinition.{helmet_definition.slug}', 'count': 1},
            {
                'item_definition': f'itemdefinition.{sword_definition.slug}',
                'count': 1,
                'archetype': 'warrior',
            },
            {
                'item_definition': f'itemdefinition.{dagger_definition.slug}',
                'count': 1,
                'archetype': 'assassin',
            },
        ])

        resp = self.client.post(self.endpoint, {
            'archetype': adv_consts.ARCHETYPE_WARRIOR,
            'name': 'Warrior'
        })
        warrior = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(warrior.equipment.weapon.definition, sword_definition)
        self.assertEqual(warrior.equipment.head.definition, helmet_definition)

        resp = self.client.post(self.endpoint, {
            'archetype': adv_consts.ARCHETYPE_ASSASSIN,
            'name': 'Assassin'
        })
        assassin = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(assassin.equipment.weapon.definition, dagger_definition)
        self.assertEqual(assassin.equipment.head.definition, helmet_definition)

    def test_prevent_char_creation(self):
        self.world.config.can_create_chars = False
        self.world.config.save()

        resp = self.client.post(self.endpoint, {'name': 'John'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['non_field_errors'][0],
            "Character creation is disabled for this world.")

    def test_create_player_with_faction(self):

        faction_starting_room = Room.objects.create(
            name='Elves Starting Room',
            world=self.world,
            zone=self.zone,
            x=1, y=0, z=0)

        Faction.objects.create(
            code='humans',
            name='Humans',
            world=self.world,
            type='core',
            playable=True,
            starting_room=self.room)
        Faction.objects.create(
            code='elves',
            name='Elves',
            world=self.world,
            type='core',
            playable=True,
            starting_room=faction_starting_room)
        self.world.config.player_creation = {
            'core_faction': {
                'mode': 'choose_required',
                'default': 'humans',
                'options': ['humans', 'elves'],
            },
        }
        self.world.config.save(update_fields=['player_creation'])

        resp = self.client.post(self.endpoint, {
            'name': 'John',
            'faction': 'elves',
        })
        self.assertEqual(resp.status_code, 201)

        player = Player.objects.select_related('core_faction').get(
            pk=resp.data['id'])
        self.assertEqual(player.core_faction.code, 'elves')
        self.assertFalse(
            player.faction_assignments.filter(
                faction__type='core',
            ).exists()
        )
        # Check that player got placed in the faction's starting room
        self.assertEqual(player.room, faction_starting_room)

        # Invalid selections are rejected by the authored player-creation policy.
        resp = self.client.post(self.endpoint, {
            'name': 'Jane',
            'faction': 'dne',
        })
        self.assertEqual(resp.status_code, 400)

    def test_mpw_naming_restrictions(self):
        self.world.is_multiplayer = True
        self.world.save()

        resp = self.client.post(self.endpoint, {'name': 'John2'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            str(resp.data['name'][0]),
            'No numbers allowed in player names.')

        resp = self.client.post(self.endpoint, {'name': 'John@'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            str(resp.data['name'][0]),
            'No special characters allowed in player names.')

    def test_create_classless_char(self):
        self.world.is_multiplayer = True
        self.world.save()

        resp = self.client.post(self.endpoint, {
            'name': 'John',
            'archetype': ''})
        self.assertEqual(resp.status_code, 201)
        john = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(john.archetype, '')

    def test_create_char_uses_locked_world_default_class(self):
        self.world.config.stat_system = {
            "attributes": [
                {"key": "constitution", "label": "Constitution"},
                {"key": "intelligence", "label": "Intelligence"},
            ],
            "class_profiles": {
                "hoplite": {
                    "label": "Hoplite",
                    "attribute_weights": {
                        "constitution": 4,
                        "intelligence": 0,
                    },
                },
                "warlord": {
                    "label": "Warlord",
                    "attribute_weights": {
                        "constitution": 2,
                        "intelligence": 2,
                    },
                },
            },
            "class_selection": {
                "enabled": False,
                "default": "hoplite",
            },
            "formulas": {
                "base_resources": {
                    "energy": {"source": "intelligence", "multiplier": 2},
                    "stamina": {"flat": 100},
                    "health": {},
                },
                "global_rules": [
                    {"source": "constitution", "target": "health_max", "multiplier": 2},
                ],
            },
        }
        self.world.config.save(update_fields=["stat_system"])

        resp = self.client.post(self.endpoint, {
            'name': 'John',
            'archetype': 'warlord',
        })
        self.assertEqual(resp.status_code, 201)
        john = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(john.archetype, 'hoplite')
        self.assertEqual(john.energy, 0)

    def test_name_exclusions(self):
        self.world.is_multiplayer = True
        self.world.save()

        self.world.config.name_exclusions = 'Jesus\nGod\nAllah'
        self.world.config.save()

        resp = self.client.post(self.endpoint, {'name': 'Jesus'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            str(resp.data['name'][0]),
            'That name is unavailable.')

        resp = self.client.post(self.endpoint, {'name': 'god'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            str(resp.data['name'][0]),
            'That name is unavailable.')




class TestLobbyCharacterPermissions(WorldTestCase):

    def test_non_builder_cannot_patch_character(self):
        self.world.is_public = True
        self.world.save()
        non_builder = self.create_user('nonbuilder@example.com')
        player = self.create_player('Patchy', user=non_builder)
        endpoint = reverse('lobby-world-char', args=[self.world.pk, player.pk])

        self.client.force_authenticate(non_builder)
        resp = self.client.patch(endpoint, data={
            'glory': 999,
            'level': 50,
            'is_builder': True,
        }, format='json')

        self.assertEqual(resp.status_code, 403)
        player.refresh_from_db()
        self.assertEqual(player.glory, 0)
        self.assertEqual(player.level, 1)
        self.assertEqual(player.is_builder, False)


class TestPlayerTransfer(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.spawn_world = self.world.create_spawn_world(
            lifecycle=api_consts.WORLD_STATE_COMPLETE)
        self.player = Player.objects.create(
            name='Jack',
            room=self.room,
            user=self.user,
            world=self.spawn_world,
            gender='male')
        self.endpoint = reverse('lobby-world-transfer', args=[self.world.key])
        self.client.force_authenticate(self.user)

        self.transfer_world = World.objects.new_world(
            name='Transfer World',
            is_multiplayer=True)
        self.transfer_world.create_spawn_world()

        self.transfer_to = self.transfer_world.config.starting_room
        self.player.room.transfer_to = self.transfer_to
        self.player.room.save()

    # Success tests

    def test_change_gender(self):
        resp = self.client.post(self.endpoint, data={
            'player': self.player.id,
            'name': 'Jane',
            'gender': 'female',
        })
        self.assertEqual(resp.status_code, 201)

        self.player.refresh_from_db()
        self.assertEqual(self.player.gender, 'female')

    # Test failures

    def test_name_is_not_taken(self):
        john = Player.objects.create(
            name='John',
            room=self.transfer_to,
            world=self.transfer_world.spawned_worlds.first(),
            user=self.user)

        resp = self.client.post(self.endpoint, data={
            'player': self.player.id,
            'name': 'John',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['non_field_errors'],
                         ['This name is already taken.'])

    def test_player_ownership(self):
        "Tests that only the user of a player can transfer the player."
        self.third_party = User.objects.create_user('john@example.com', 'p')
        self.client.force_authenticate(self.third_party)
        resp = self.client.post(self.endpoint, data={
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['player'],
                         ['Player does not belong to this user account.'])

    def test_valid_player(self):
        # Invalid player
        resp = self.client.post(self.endpoint, data={
            'player': 132,
            'name': 'John',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['player'], ['Invalid player ID'])

    def test_in_transfer_room(self):
        # Player is in a transfer room
        self.player.room.transfer_to = None
        self.player.room.save()
        resp = self.client.post(self.endpoint, data={
            'player': self.player.id,
            'name': 'John',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['non_field_errors'],
                         ['Player is not in a transfer room.'])

    def test_in_spw(self):
        self.player.world.is_multiplayer = True
        self.player.world.save()

        resp = self.client.post(self.endpoint, data={
            'player': self.player.id,
            'name': 'John',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['non_field_errors'],
                         ['Player is not in a single player world.'])

    def test_spw_is_complete(self):
        self.player.world.lifecycle = api_consts.WORLD_STATE_STORED
        self.player.world.save()

        # SPW not complete
        resp = self.client.post(self.endpoint, data={
            'player': self.player.id,
            'name': 'John',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['non_field_errors'],
                         ['Player is not in a completed world.'])

    def test_dest_world_is_multiplayer(self):
        self.player.room.transfer_to.world.is_multiplayer = False
        self.player.room.transfer_to.world.save()

        resp = self.client.post(self.endpoint, data={
            'player': self.player.id,
            'name': 'John',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['non_field_errors'],
                         ['Destination world is not multiplayer.'])

    def test_player_is_not_temporary(self):
        """
        Test that transfering a player is only possible for non-temporary
        user accounts.
        """
        self.user.is_temporary = True
        self.user.save()

        resp = self.client.post(self.endpoint, data={
            'player': self.player.id,
            'name': 'John',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['player'], ['User is temporary.'])

    def test_name_normalization(self):
        resp = self.client.post(self.endpoint, data={
            'player': self.player.id,
            'name': 'John Smith',
        })
        self.assertEqual(resp.status_code, 201)

        self.player.refresh_from_db()
        self.assertEqual(self.player.world,
                         self.transfer_world.spawned_worlds.first())
        self.assertEqual(self.player.room, self.transfer_to)
        self.assertEqual(self.player.name, 'John')
        self.assertEqual(self.player.title, 'Smith')
        self.assertEqual(self.player.gender, 'male')
