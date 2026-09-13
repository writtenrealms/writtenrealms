from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext

from spawns.models import CombatEncounter, CombatParticipant, CombatSide, Mob
from tests.base import WorldTestCase


class CombatActorDeletionTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.encounter = CombatEncounter.objects.create(
            world=self.spawn_world, room=self.room,
        )
        self.side = CombatSide.objects.create(encounter=self.encounter, position=1)

    def member(self, actor, **kwargs):
        kind = 'mob' if isinstance(actor, Mob) else 'player'
        return CombatParticipant.objects.create(
            encounter=self.encounter,
            side=self.side,
            actor_snapshot={'key': actor.key, 'kind': kind, 'name': actor.name},
            **{kind: actor},
            **kwargs,
        )

    def test_deleting_either_actor_preserves_inactive_combat_history(self):
        for actor in [self.player, self.create_mob('Guard')]:
            with self.subTest(actor=actor.key):
                member = self.member(actor)
                snapshot = member.actor_snapshot

                actor.delete()

                member.refresh_from_db()
                self.assertFalse(member.is_active)
                self.assertIsNone(member.player_id)
                self.assertIsNone(member.mob_id)
                self.assertEqual(member.actor_snapshot, snapshot)
                self.assertTrue(CombatEncounter.objects.filter(pk=self.encounter.pk).exists())

    def test_deleting_actor_preserves_existing_exit_reason(self):
        mob = self.create_mob('Guard')
        member = self.member(mob, is_active=False, exit_reason='dead')

        mob.delete()

        member.refresh_from_db()
        self.assertFalse(member.is_active)
        self.assertIsNone(member.mob_id)
        self.assertEqual(member.exit_reason, 'dead')

    def test_bulk_actor_deletion_batches_participant_updates(self):
        for count in [1, 10]:
            with self.subTest(count=count):
                mobs = [self.create_mob(f'Guard {i}') for i in range(count)]
                members = [self.member(mob) for mob in mobs]
                with CaptureQueriesContext(connection) as queries:
                    Mob.objects.filter(pk__in=[mob.pk for mob in mobs]).delete()

                updates = [query['sql'] for query in queries.captured_queries
                           if query['sql'].startswith('UPDATE "spawns_combatparticipant"')]
                self.assertEqual(len(updates), 2)
                self.assertEqual(CombatParticipant.objects.filter(
                    pk__in=[member.pk for member in members],
                    is_active=False, mob__isnull=True,
                ).count(), count)

    def test_active_participant_still_requires_an_actor(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CombatParticipant.objects.create(encounter=self.encounter, side=self.side)

    def test_world_cascade_deletes_active_combat_participants(self):
        self.member(self.player)
        self.member(self.create_mob('Guard'))
        world_id = self.spawn_world.pk

        self.spawn_world.delete()

        self.assertFalse(CombatEncounter.objects.filter(world_id=world_id).exists())
        self.assertFalse(CombatParticipant.objects.exists())
