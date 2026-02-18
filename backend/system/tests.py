import mock

from django.test import TestCase
from rest_framework.reverse import reverse

from config import constants as adv_consts

from builders.models import HousingBlock, Quest, MobTemplate
from spawns.models import Player, Item, PlayerEnquire, Clan, ClanMembership, Mob
from tests.base import WorldTestCase


# Create your tests here.
class UpgradeItemTests(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.make_system_user()
        self.client.force_authenticate(self.user)

        self.spawn_world = self.world.create_spawn_world()

        self.player = Player.objects.create(
            world=self.spawn_world,
            room=self.room,
            user=self.user,
            name='John',
            in_game=True)

        self.item = Item.objects.create(
            world=self.spawn_world,
            name='a sword',
            quality=adv_consts.ITEM_QUALITY_IMBUED,
            strength=100)

        self.mob_template = MobTemplate.objects.create(
            world=self.world,
            name='an upgrader',
            is_upgrader=True)
        self.mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            template=self.mob_template)

        self.ep = reverse('game-upgrade-item')

    def test_upgrade_item_failure(self):
        self.mob_template.upgrade_success_chance = 0
        self.mob_template.save()

        resp = self.client.post(self.ep, {
            "player": self.player.id,
            "item": self.item.id,
            "mob": self.mob.id,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['outcome'], 'failure')
        self.assertIsNone(resp.data['item'])
        with self.assertRaises(Item.DoesNotExist):
            self.item.refresh_from_db()

    def test_upgrade_item_success(self):
        self.mob_template.upgrade_success_chance = 100
        self.mob_template.save()

        resp = self.client.post(self.ep, {
            "player": self.player.id,
            "item": self.item.id,
            "mob": self.mob.id,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['outcome'], 'success')

        self.item.refresh_from_db()
        self.assertEqual(self.item.strength, 120)

    def test_item_must_be_magical(self):
        self.item.quality = adv_consts.ITEM_QUALITY_NORMAL
        self.item.save()
        resp = self.client.post(self.ep, {
            "player": self.player.id,
            "item": self.item.id,
            "mob": self.mob.id,
        })
        self.assertEqual(resp.status_code, 400)

    def test_upgrade_imbued_limit(self):
        self.mob_template.upgrade_success_chance = 100
        self.mob_template.save()

        self.item.upgrade_count = 1
        self.item.save()
        resp = self.client.post(self.ep, {
            "player": self.player.id,
            "item": self.item.id,
            "mob": self.mob.id,
        })
        self.assertEqual(resp.status_code, 400)

        self.item.quality = adv_consts.ITEM_QUALITY_ENCHANTED
        self.item.save()
        resp = self.client.post(self.ep, {
            "player": self.player.id,
            "item": self.item.id,
            "mob": self.mob.id,
        })
        self.assertEqual(resp.status_code, 201)


class ToggleTests(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.make_system_user()
        self.client.force_authenticate(self.user)
        self.spawn_world = self.world.create_spawn_world()
        self.player = Player.objects.create(
            world=self.spawn_world,
            room=self.room,
            user=self.user,
            name='John',
            in_game=True)
        self.ep = reverse('game-toggle-room')
        # Create housing block, put the room in it
        block = HousingBlock.objects.create(
            name='housing block',
            owner=self.player,
            price=1)
        self.room.housing_block = block
        self.room.save()

    def test_toggle_room(self):
        self.room.ownership_type = adv_consts.ROOM_OWNERSHIP_TYPE_PRIVATE
        resp = self.client.post(self.ep, {
            "room": self.room.id
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.room.refresh_from_db()
        self.assertEqual(
            self.room.ownership_type,
            adv_consts.ROOM_OWNERSHIP_TYPE_PUBLIC)


class EnquireTests(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.make_system_user()
        self.client.force_authenticate(self.user)
        self.spawn_world = self.world.create_spawn_world()
        self.player = Player.objects.create(
            world=self.spawn_world,
            room=self.room,
            user=self.user,
            name='John',
            in_game=True)

        self.mob_template = MobTemplate.objects.create(
            world=self.world, name='a priest')

        self.quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)

    def test_track_enquire(self):
        ep = reverse('enquire-quest')
        resp = self.client.post(ep, {
            'player': self.player.id,
            'quest': self.quest.id,
        })
        self.assertEqual(resp.status_code, 201)
        enquire_record = PlayerEnquire.objects.get()
        self.assertEqual(enquire_record.quest, self.quest)
        self.assertEqual(enquire_record.player, self.player)


class SystemTestCase(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.make_system_user()
        self.client.force_authenticate(self.user)


class ClanTests(SystemTestCase):

    # game-cregister
    # game-cpassword
    # game-cjoin
    # game-cquit
    # game-cpromote
    # game-ckick


    # Register

    def test_register_new_clan(self):
        ep = reverse('game-cregister')

        # Need enough money
        resp = self.client.post(ep, {
            'clan': 'The Clan',
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['non_field_errors'][0],
                         'Registering a clan costs 1000 gold.')

        # Successful creation
        self.assertEqual(Clan.objects.count(), 0)
        self.player.gold = 1000
        self.player.save()
        resp = self.client.post(ep, {
            'clan': 'The Clan',
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 201)
        clan = Clan.objects.get()
        self.assertEqual(clan.name, 'The Clan')
        self.assertIsNone(clan.password)
        self.assertEqual(clan.world, self.player.world.context)
        self.assertEqual(
            clan.memberships.filter(
                rank=adv_consts.CLAN_RANK_MASTER
            ).first().player,
            self.player)
        self.player.refresh_from_db()
        self.assertEqual(self.player.gold, 0)

    def test_re_register(self):
        """
        Call the register endpoint again as the clan master but with
        a different name. This will change the name of the clan if that
        name is available.
        """

        ep = reverse('game-cregister')
        self.player.gold = 1000
        self.player.save()
        clan = Clan.objects.create(
            name='The Clan',
            world=self.player.world.context)
        ClanMembership.objects.create(
            clan=clan,
            player=self.player,
            rank=adv_consts.CLAN_RANK_MASTER)

        # If there's no changes, we get a 400
        resp = self.client.post(ep, {
            'clan': 'The Clan',
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['non_field_errors'][0],
                        'No changes detected.')

        # Successful rename
        resp = self.client.post(ep, {
            'clan': 'The New Clan',
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 201)
        clan.refresh_from_db()
        self.assertEqual(clan.name, 'The New Clan')

        # Successful recapitalization
        self.player.gold = 1000
        self.player.save()
        resp = self.client.post(ep, {
            'clan': 'The New CLAN',
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 201)
        clan.refresh_from_db()
        self.assertEqual(clan.name, 'The New CLAN')

        # non-master cannot re-register
        member = self.create_player('Member')
        member.gold = 1000
        member.save()
        ClanMembership.objects.create(
            clan=clan,
            player=member,
            rank=adv_consts.CLAN_RANK_MEMBER)
        resp = self.client.post(ep, {
            'clan': 'The New Clan',
            'player': member.id,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['non_field_errors'][0],
                        'Only the clan master can change the clan name.')

    def test_register_name_taken(self):
        ep = reverse('game-cregister')
        self.player.gold = 1000
        self.player.save()
        Clan.objects.create(
            name='The Clan',
            world=self.player.world.context)

        # New clan with name taken
        resp = self.client.post(ep, {
            'clan': 'The Clan',
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['non_field_errors'][0],
                         'That name is taken.')

        # Clan rename with name taken
        clan = Clan.objects.create(
            name='The New Clan',
            world=self.player.world.context)
        ClanMembership.objects.create(
            clan=clan,
            player=self.player,
            rank=adv_consts.CLAN_RANK_MASTER)
        resp = self.client.post(ep, {
            'clan': 'The Clan',
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['non_field_errors'][0],
                         'That name is taken.')

        # Test case sensitivity
        resp = self.client.post(ep, {
            'clan': 'The CLAN',
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['non_field_errors'][0],
                         'That name is taken.')

    # Password

    def test_set_password(self):
        ep = reverse('game-cpassword')
        self.player.gold = 1000
        self.player.save()
        clan = Clan.objects.create(
            name='The Clan',
            world=self.player.world.context)
        ClanMembership.objects.create(
            clan=clan,
            player=self.player,
            rank=adv_consts.CLAN_RANK_MASTER)

        # Set password
        resp = self.client.post(ep, {
            'player': self.player.id,
            'password': 'sesame',
        })
        self.assertEqual(resp.status_code, 201)
        clan.refresh_from_db()
        self.assertEqual(clan.password, 'sesame')

        # Clear password
        resp = self.client.post(ep, {
            'player': self.player.id,
            'password': 'clear',
        })
        self.assertEqual(resp.status_code, 201)
        clan.refresh_from_db()
        self.assertIsNone(clan.password)

        # Non-master cannot set the password
        member = self.create_player('Member')
        member.gold = 1000
        member.save()
        ClanMembership.objects.create(
            clan=clan,
            player=member,
            rank=adv_consts.CLAN_RANK_MEMBER)
        resp = self.client.post(ep, {
            'player': member.id,
            'password': 'sesame',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['non_field_errors'][0],
                         'Only the clan master can set the password.')

    # Join

    def test_join_clan(self):
        ep = reverse('game-cjoin')

        clan = Clan.objects.create(
            name='The Clan',
            world=self.player.world.context)
        clan_master = self.create_player('ClanMaster')
        ClanMembership.objects.create(
            clan=clan,
            player=clan_master,
            rank=adv_consts.CLAN_RANK_MASTER)

        # Wrong name
        resp = self.client.post(ep, {
            'player': self.player.id,
            'clan': 'Some clan',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['non_field_errors'][0],
                         'Wrong clan name or password.')

        # Successful join
        resp = self.client.post(ep, {
            'player': self.player.id,
            'clan': 'The Clan',
        })
        self.assertEqual(resp.status_code, 201)
        membership = ClanMembership.objects.filter(player=self.player).first()
        self.assertEqual(membership.rank, adv_consts.CLAN_RANK_MEMBER)

    def test_clan_password(self):
        ep = reverse('game-cjoin')

        clan = Clan.objects.create(
            name='The Clan',
            world=self.player.world.context,
            password='open sesame')
        clan_master = self.create_player('ClanMaster')
        ClanMembership.objects.create(
            clan=clan,
            player=clan_master,
            rank=adv_consts.CLAN_RANK_MASTER)

        # No password
        resp = self.client.post(ep, {
            'player': self.player.id,
            'clan': 'THE clan',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['non_field_errors'][0],
                         'This clan requires a password.')

        # Wrong password
        resp = self.client.post(ep, {
            'player': self.player.id,
            'clan': 'THE clan wrong',
        })
        self.assertEqual(resp.status_code, 400)

        # Correct password
        resp = self.client.post(ep, {
            'player': self.player.id,
            'clan': 'THE clan open sesame',
        })
        self.assertEqual(resp.status_code, 201)
        membership = ClanMembership.objects.filter(player=self.player).first()
        self.assertEqual(membership.rank, adv_consts.CLAN_RANK_MEMBER)

    def test_cannot_join_cross_race(self):
        ep = reverse('game-cjoin')

        from builders.models import Faction, FactionAssignment
        humans = Faction.objects.create(
            name='Humans',
            world=self.player.world.context,
            is_core=True)
        orcs = Faction.objects.create(
            name='Orcs',
            world=self.player.world.context,
            is_core=True)

        clan = Clan.objects.create(
            name='The Clan',
            world=self.player.world.context)
        clan_master = self.create_player('ClanMaster')
        ClanMembership.objects.create(
            clan=clan,
            player=clan_master,
            rank=adv_consts.CLAN_RANK_MASTER)

        FactionAssignment.objects.create(
            member=clan_master,
            faction=humans)
        FactionAssignment.objects.create(
            member=self.player,
            faction=orcs)

        resp = self.client.post(ep, {
            'player': self.player.id,
            'clan': 'The Clan',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['non_field_errors'][0],
                         'You cannot join this clan.')

    # Quit

    def test_quit_clan(self):
        ep = reverse('game-cquit')

        clan = Clan.objects.create(
            name='The Clan',
            world=self.player.world.context)
        ClanMembership.objects.create(
            clan=clan,
            player=self.player,
            rank=adv_consts.CLAN_RANK_MASTER)

        # Can leave if master and no members
        resp = self.client.post(ep, {
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 201)
        membership = ClanMembership.objects.filter(player=self.player).first()
        self.assertIsNone(membership)
        # This deletes the clan
        with self.assertRaises(Clan.DoesNotExist):
            clan.refresh_from_db()

        # Cannot leave if there are still members
        clan = Clan.objects.create(
            name='The Clan',
            world=self.player.world.context)
        ClanMembership.objects.create(
            clan=clan,
            player=self.player,
            rank=adv_consts.CLAN_RANK_MASTER)
        member = self.create_player('Member')
        ClanMembership.objects.create(
            clan=clan,
            player=member,
            rank=adv_consts.CLAN_RANK_MEMBER)
        resp = self.client.post(ep, {
            'player': self.player.id,
        })
        self.assertEqual(resp.status_code, 400)

    # Promote

    def test_promote_new_master(self):
        ep = reverse('game-cpromote')

        clan = Clan.objects.create(
            name='The Clan',
            world=self.player.world.context)
        player_membership = ClanMembership.objects.create(
            clan=clan,
            player=self.player,
            rank=adv_consts.CLAN_RANK_MASTER)

        member = self.create_player('Member')
        ClanMembership.objects.create(
            clan=clan,
            player=member,
            rank=adv_consts.CLAN_RANK_MEMBER)

        # Successful promotion
        resp = self.client.post(ep, {
            'player': self.player.id,
            'member': member.name,
        })
        self.assertEqual(resp.status_code, 201)
        member_membership = ClanMembership.objects.get(player=member)
        self.assertEqual(member_membership.rank, adv_consts.CLAN_RANK_MASTER)
        self.player.refresh_from_db()
        player_membership.refresh_from_db()
        self.assertEqual(player_membership.rank, adv_consts.CLAN_RANK_MEMBER)

        # Can't do it again since the player is now just a member
        resp = self.client.post(ep, {
            'player': self.player.id,
            'member': member.name,
        })
        self.assertEqual(resp.status_code, 400)

    # Kick

    def test_kick_member(self):
        ep = reverse('game-ckick')

        clan = Clan.objects.create(
            name='The Clan',
            world=self.player.world.context)
        player_membership = ClanMembership.objects.create(
            clan=clan,
            player=self.player,
            rank=adv_consts.CLAN_RANK_MASTER)

        member = self.create_player('Member')
        member_membership = ClanMembership.objects.create(
            clan=clan,
            player=member,
            rank=adv_consts.CLAN_RANK_MEMBER)

        # Successful kick
        resp = self.client.post(ep, {
            'player': self.player.id,
            'member': member.name,
        })
        self.assertEqual(resp.status_code, 201)
        with self.assertRaises(ClanMembership.DoesNotExist):
            member_membership.refresh_from_db()

        # Can't do it again since the player is now gone
        resp = self.client.post(ep, {
            'player': self.player.id,
            'member': member.name,
        })
        self.assertEqual(resp.status_code, 400)
