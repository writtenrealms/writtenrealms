import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.db.models import Q

from config import constants as api_consts
from lobby.serializers import WorldTransferSerializer
from spawns.actions.combat import apply_player_death
from spawns.follow_lifecycle import clear_movement_follows_for_players
from spawns.models import Mob, MovementFollow
from spawns.services import WorldGate
from tests.base import WorldTestCase
from worlds.instances import (
    create_fresh_instance_run,
    enter_players_into_run,
    reset_instance,
)
from worlds.models import World, WorldConfig


class MovementFollowLifecycleTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self._graph_number = 0

    def _create_follow_graph(self, affected_player):
        self._graph_number += 1
        suffix = self._graph_number
        world = affected_player.world
        room = affected_player.room

        mob_leader = Mob.objects.create(
            name=f"Lifecycle mob leader {suffix}",
            world=world,
            room=room,
        )
        incoming_follower = self.create_player(
            f"Incoming follower {suffix}",
            world=world,
            room=room,
        )
        unrelated_leader = self.create_player(
            f"Unrelated leader {suffix}",
            world=world,
            room=room,
        )
        unrelated_follower = self.create_player(
            f"Unrelated follower {suffix}",
            world=world,
            room=room,
        )

        MovementFollow.objects.create(
            follower=affected_player,
            leader_mob=mob_leader,
        )
        MovementFollow.objects.create(
            follower=incoming_follower,
            leader_player=affected_player,
        )
        unrelated = MovementFollow.objects.create(
            follower=unrelated_follower,
            leader_player=unrelated_leader,
        )
        return unrelated.id

    def _assert_follow_graph_cleared(self, affected_player, unrelated_id):
        self.assertFalse(
            MovementFollow.objects.filter(
                Q(follower_id=affected_player.id)
                | Q(leader_player_id=affected_player.id)
            ).exists()
        )
        self.assertTrue(
            MovementFollow.objects.filter(pk=unrelated_id).exists()
        )

    def _instance_template(self):
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=["is_multiplayer"])
        config = WorldConfig.objects.create()
        template = World.objects.new_world(
            name="Follow Lifecycle Trial",
            author=self.user,
            config=config,
            is_multiplayer=True,
            instance_of=self.world,
        )
        return template, template.config.starting_room

    def _enter_instance(self):
        _template, instance_room = self._instance_template()
        spawned_instance = World.enter_instance(
            player=self.player,
            transfer_to_id=instance_room.id,
            transfer_from_id=self.room.id,
        )
        self.player.refresh_from_db()
        return spawned_instance, instance_room

    def test_helper_deletes_outgoing_and_player_led_edges_only(self):
        unrelated_id = self._create_follow_graph(self.player)

        deleted = clear_movement_follows_for_players([self.player.id])

        self.assertEqual(deleted, 2)
        self._assert_follow_graph_cleared(self.player, unrelated_id)

    def test_logout_clears_follow_relationships(self):
        unrelated_id = self._create_follow_graph(self.player)
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=["is_multiplayer"])

        with patch.object(WorldGate, "exit_mpw"):
            WorldGate(player=self.player, world=self.spawn_world).exit()

        self._assert_follow_graph_cleared(self.player, unrelated_id)

    def test_direct_player_reset_clears_follow_relationships(self):
        unrelated_id = self._create_follow_graph(self.player)
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=["is_multiplayer"])

        self.player.reset()

        self._assert_follow_graph_cleared(self.player, unrelated_id)

    def test_direct_initialize_reset_clears_follow_relationships(self):
        unrelated_id = self._create_follow_graph(self.player)

        self.player.initialize(
            reset=True,
            include_starting_equipment=False,
        )

        self._assert_follow_graph_cleared(self.player, unrelated_id)

    def test_death_relocation_clears_follow_relationships(self):
        unrelated_id = self._create_follow_graph(self.player)
        death_room = self.room.create_at(api_consts.DIRECTION_EAST)
        self.world.config.death_room = death_room
        self.world.config.save(update_fields=["death_room"])

        apply_player_death(
            player=self.player,
            origin_room=self.room,
            cause="follow_lifecycle_test",
            forced=True,
            death_token=uuid.uuid4(),
        )

        self._assert_follow_graph_cleared(self.player, unrelated_id)

    def test_single_player_instance_entry_clears_follow_relationships(self):
        unrelated_id = self._create_follow_graph(self.player)
        _template, instance_room = self._instance_template()

        World.enter_instance(
            player=self.player,
            transfer_to_id=instance_room.id,
            transfer_from_id=self.room.id,
        )

        self._assert_follow_graph_cleared(self.player, unrelated_id)

    def test_batch_instance_entry_clears_each_players_relationships(self):
        _template, instance_room = self._instance_template()
        member = self.create_player("Batch member")
        player_unrelated_id = self._create_follow_graph(self.player)
        member_unrelated_id = self._create_follow_graph(member)
        run = create_fresh_instance_run(
            _template,
            leader=self.player,
            member_ids=[member.id],
        )

        enter_players_into_run(
            run,
            players_and_transfer_rooms=[
                (self.player, self.room),
                (member, self.room),
            ],
            entry_room=instance_room,
        )

        self._assert_follow_graph_cleared(self.player, player_unrelated_id)
        self._assert_follow_graph_cleared(member, member_unrelated_id)

    def test_instance_leave_clears_follow_relationships(self):
        self._enter_instance()
        unrelated_id = self._create_follow_graph(self.player)

        World.leave_instance(player=self.player)

        self._assert_follow_graph_cleared(self.player, unrelated_id)

    def test_instance_reset_clears_follow_relationships(self):
        self._enter_instance()
        unrelated_id = self._create_follow_graph(self.player)

        reset_instance(player=self.player)

        self._assert_follow_graph_cleared(self.player, unrelated_id)

    def test_single_to_multiplayer_transfer_clears_follow_relationships(self):
        self.spawn_world.lifecycle = api_consts.WORLD_STATE_COMPLETE
        self.spawn_world.save(update_fields=["lifecycle"])
        transfer_world = World.objects.new_world(
            name="Follow Transfer World",
            is_multiplayer=True,
        )
        destination_runtime = transfer_world.create_spawn_world()
        destination_room = transfer_world.config.starting_room
        self.room.transfer_to = destination_room
        self.room.save(update_fields=["transfer_to"])
        unrelated_id = self._create_follow_graph(self.player)
        serializer = WorldTransferSerializer(
            data={"player": self.player.id, "name": "Transferred"},
            context={"request": SimpleNamespace(user=self.user)},
        )
        serializer.is_valid(raise_exception=True)

        transferred_player = serializer.save()

        self._assert_follow_graph_cleared(self.player, unrelated_id)
        self.assertEqual(transferred_player.world_id, destination_runtime.id)
        self.assertEqual(transferred_player.room_id, destination_room.id)

    def test_world_cleanup_bulk_clears_only_its_players_relationships(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        self._create_follow_graph(self.player)

        other_world = World.objects.new_world(name="Other Follow World")
        other_runtime = other_world.create_spawn_world()
        other_room = other_world.config.starting_room
        other_leader = self.create_player(
            "Other leader",
            world=other_runtime,
            room=other_room,
        )
        other_follower = self.create_player(
            "Other follower",
            world=other_runtime,
            room=other_room,
        )
        other_relationship = MovementFollow.objects.create(
            follower=other_follower,
            leader_player=other_leader,
        )

        self.spawn_world.cleanup(spw=True)

        self.assertFalse(
            MovementFollow.objects.filter(
                Q(follower__world_id=self.spawn_world.id)
                | Q(leader_player__world_id=self.spawn_world.id)
            ).exists()
        )
        self.assertTrue(
            MovementFollow.objects.filter(pk=other_relationship.id).exists()
        )
        self.player.refresh_from_db()
        self.assertFalse(self.player.in_game)
