import mock

from config import constants as adv_consts

from config import game_settings as adv_config

from rest_framework.test import APITestCase
from rest_framework.reverse import reverse

from config import constants as api_consts
from builders.models import ItemTemplate, Faction, FactionAssignment
from spawns.models import Player
from tests.base import WorldTestCase
from users.models import User
from worlds.models import World, Room, StartingEq


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
        # Check for normal warrior starting values
        self.assertEqual(player.health, 63)
        self.assertEqual(player.stamina,
                         adv_config.PLAYER_STARTING_MAX_STAMINA)
        self.assertEqual(player.mana, 14)

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

    def test_starting_eq(self):
        sword_template = self.world.create_item_template(
            name='a sword',
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H)
        helmet_template = self.world.create_item_template(
            name='a helmet',
            equipment_type=adv_consts.EQUIPMENT_TYPE_HEAD)
        compass_template = self.world.create_item_template(name='a compass')

        StartingEq.objects.create(
            worldconfig=self.world.config,
            itemtemplate=sword_template)

        StartingEq.objects.create(
            worldconfig=self.world.config,
            itemtemplate=helmet_template)

        StartingEq.objects.create(
            worldconfig=self.world.config,
            itemtemplate=compass_template)

        resp = self.client.post(self.endpoint, {'name': 'John'})
        john = Player.objects.get(pk=resp.data['id'])

        self.assertEqual(john.equipment.weapon.template, sword_template)
        self.assertEqual(john.equipment.head.template, helmet_template)
        self.assertEqual(john.inventory.all().first().template, compass_template)

    def test_assassin_starting_eq(self):
        """
        Regression test, makes sure that an assassin doesn't start with a
        2H weapon
        """
        sword_template = self.world.create_item_template(
            name='a sword',
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_2H)
        StartingEq.objects.create(
            worldconfig=self.world.config,
            itemtemplate=sword_template)
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
        dagger_template = self.world.create_item_template(
            name='a dagger',
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H)
        StartingEq.objects.create(
            worldconfig=self.world.config,
            itemtemplate=dagger_template,
            num=2)
        resp = self.client.post(self.endpoint, {
            'archetype': adv_consts.ARCHETYPE_ASSASSIN,
            'name': 'Assassin'
        })
        john = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(john.archetype, adv_consts.ARCHETYPE_ASSASSIN)
        self.assertEqual(john.equipment.weapon.template, dagger_template)
        self.assertEqual(john.equipment.offhand.template, dagger_template)

    def test_class_specific_starting_eq(self):
        # For both
        helmet_template = self.world.create_item_template(
            name='a helmet',
            equipment_type=adv_consts.EQUIPMENT_TYPE_HEAD)
        StartingEq.objects.create(
            worldconfig=self.world.config,
            itemtemplate=helmet_template)

        # for warriors
        sword_template = self.world.create_item_template(
            name='a sword',
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_2H)
        StartingEq.objects.create(
            worldconfig=self.world.config,
            itemtemplate=sword_template,
            archetype='warrior')

        # for assassins
        dagger_template = self.world.create_item_template(
            name='a dagger',
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H)
        StartingEq.objects.create(
            worldconfig=self.world.config,
            itemtemplate=dagger_template,
            archetype='assassin')

        resp = self.client.post(self.endpoint, {
            'archetype': adv_consts.ARCHETYPE_WARRIOR,
            'name': 'Warrior'
        })
        warrior = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(warrior.equipment.weapon.template, sword_template)
        self.assertEqual(warrior.equipment.head.template, helmet_template)

        resp = self.client.post(self.endpoint, {
            'archetype': adv_consts.ARCHETYPE_ASSASSIN,
            'name': 'Assassin'
        })
        assassin = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(assassin.equipment.weapon.template, dagger_template)
        self.assertEqual(assassin.equipment.head.template, helmet_template)

    def test_starting_gold(self):
        self.world.config.starting_gold = 100
        self.world.config.save()
        resp = self.client.post(self.endpoint, {'name': 'John'})
        john = Player.objects.get(pk=resp.data['id'])
        self.assertEqual(john.gold, 100)

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

        faction = Faction.objects.create(
            code='elves',
            name='Elves',
            world=self.world,
            is_core=True,
            starting_room=faction_starting_room)

        resp = self.client.post(self.endpoint, {
            'name': 'John',
            'faction': 'elves',
        })
        self.assertEqual(resp.status_code, 201)

        player = Player.objects.get(pk=resp.data['id'])
        faction_assignment = player.faction_assignments.get(
            faction__code='elves')
        self.assertEqual(faction_assignment.value, 1)
        # Check that player got placed in the faction's starting room
        self.assertEqual(player.room, faction_starting_room)

        # Test creating with invalid faction just ignores it
        faction = Faction.objects.create(
            code='elves',
            name='Elves',
            world=self.world,
            is_core=True,
            starting_room=faction_starting_room)

        resp = self.client.post(self.endpoint, {
            'name': 'John',
            'faction': 'dne',
        })
        self.assertEqual(resp.status_code, 201)
        player = Player.objects.get(pk=resp.data['id'])
        self.assertFalse(player.faction_assignments.exists())

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




class TestDeletePlayerCharacter(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.spawn_world = self.world.create_spawn_world()

    def test_delete_player_char_sets_it_pending(self):
        player = Player.objects.create(
            name='John',
            world=self.spawn_world,
            room=self.room,
            user=self.user)

        self.assertIsNone(player.pending_deletion_ts)

        list_endpoint = reverse('lobby-world-chars', args=[self.world.pk])
        resp = self.client.get(list_endpoint)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 2)

        endpoint = reverse('lobby-world-char', args=[self.world.pk, player.pk])
        resp = self.client.delete(endpoint)

        player.refresh_from_db()
        self.assertIsNotNone(player.pending_deletion_ts)

        list_endpoint = reverse('lobby-world-chars', args=[self.world.pk])
        resp = self.client.get(list_endpoint)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)


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
            'is_immortal': True,
        }, format='json')

        self.assertEqual(resp.status_code, 403)
        player.refresh_from_db()
        self.assertEqual(player.glory, 0)
        self.assertEqual(player.level, 1)
        self.assertEqual(player.is_immortal, False)


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

    def test_success(self):
        resp = self.client.post(self.endpoint, data={
            'player': self.player.id,
            'name': 'John',
        })
        self.assertEqual(resp.status_code, 201)

        self.player.refresh_from_db()
        self.assertEqual(self.player.world,
                         self.transfer_world.spawned_worlds.first())
        self.assertEqual(self.player.room, self.transfer_to)
        self.assertEqual(self.player.name, 'John')
        self.assertEqual(self.player.gender, 'male')

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
        self.assertEqual
        (resp.data['player'], ['Invalid player ID'])

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
