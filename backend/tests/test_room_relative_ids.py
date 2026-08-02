from concurrent.futures import ThreadPoolExecutor
import importlib
from threading import Barrier, Event

from django.core.exceptions import ValidationError
from django.db import (
    IntegrityError,
    close_old_connections,
    connections,
    models,
    transaction,
)
from django.test import TestCase, TransactionTestCase

from worlds.models import (
    BIGINT_MAX,
    MAX_ALLOCATABLE_ROOM_RELATIVE_ID,
    Room,
    World,
)


def create_room(*, world, name, x, relative_id=None):
    kwargs = {
        'world': world,
        'name': name,
        'x': x,
        'y': 0,
        'z': 0,
    }
    if relative_id is not None:
        return Room.objects.create_with_imported_relative_id(
            relative_id=relative_id,
            **kwargs,
        )
    return Room.objects.create(**kwargs)


class RoomRelativeIDTests(TestCase):

    def setUp(self):
        self.world = World.objects.create(name='Stable Room IDs')

    def test_allocates_positive_world_scoped_ids(self):
        first = create_room(world=self.world, name='First', x=0)
        second = create_room(world=self.world, name='Second', x=1)
        other_world = World.objects.create(name='Other World')
        other_first = create_room(
            world=other_world,
            name='Other First',
            x=0,
        )

        self.assertEqual(first.relative_id, 1)
        self.assertEqual(second.relative_id, 2)
        self.assertEqual(other_first.relative_id, 1)
        self.assertGreater(first.relative_id, 0)

    def test_deleted_identity_is_not_reused(self):
        first = create_room(world=self.world, name='First', x=0)
        second = create_room(world=self.world, name='Second', x=1)
        second.delete()

        replacement = create_room(
            world=self.world,
            name='Replacement',
            x=2,
        )
        self.world.refresh_from_db()

        self.assertEqual(first.relative_id, 1)
        self.assertEqual(replacement.relative_id, 3)
        self.assertEqual(self.world.next_room_relative_id, 4)

    def test_stale_world_save_cannot_rewind_allocator(self):
        stale_world = World.objects.get(pk=self.world.pk)
        first = create_room(world=self.world, name='First', x=0)

        stale_world.description = 'An unrelated edit.'
        stale_world.save()
        second = create_room(world=self.world, name='Second', x=1)

        self.assertEqual(first.relative_id, 1)
        self.assertEqual(second.relative_id, 2)

        with self.assertRaisesRegex(
            ValidationError,
            'high-water mark',
        ):
            World.objects.filter(pk=self.world.pk).update(
                next_room_relative_id=1,
            )

    def test_imported_identity_advances_persistent_allocator(self):
        imported = create_room(
            world=self.world,
            name='Imported',
            x=0,
            relative_id=40,
        )
        allocated = create_room(
            world=self.world,
            name='Allocated',
            x=1,
        )
        self.world.refresh_from_db()

        self.assertEqual(imported.relative_id, 40)
        self.assertEqual(allocated.relative_id, 41)
        self.assertEqual(self.world.next_room_relative_id, 42)

    def test_import_cannot_recreate_retired_or_skipped_identity(self):
        imported = create_room(
            world=self.world,
            name='Imported',
            x=0,
            relative_id=40,
        )
        imported.delete()

        with self.assertRaisesRegex(
            ValidationError,
            'already allocated or retired',
        ):
            create_room(
                world=self.world,
                name='Reused',
                x=1,
                relative_id=40,
            )
        with self.assertRaisesRegex(
            ValidationError,
            'already allocated or retired',
        ):
            create_room(
                world=self.world,
                name='Skipped',
                x=2,
                relative_id=20,
            )

    def test_room_identity_is_immutable_after_creation(self):
        room = create_room(world=self.world, name='First', x=0)
        original_relative_id = room.relative_id
        room.relative_id = original_relative_id + 10

        with self.assertRaisesRegex(ValidationError, 'immutable'):
            room.save(update_fields=['relative_id'])

        room.refresh_from_db()
        self.assertEqual(room.relative_id, original_relative_id)

        other_world = World.objects.create(name='Other')
        room.world = other_world
        with self.assertRaisesRegex(ValidationError, 'immutable'):
            room.save(update_fields=['world'])

    def test_queryset_cannot_bypass_identity_immutability(self):
        room = create_room(world=self.world, name='First', x=0)

        with self.assertRaisesRegex(ValidationError, 'identity fields'):
            Room.objects.filter(pk=room.pk).update(relative_id=20)
        with self.assertRaisesRegex(ValidationError, 'identity fields'):
            Room.objects.bulk_update([room], ['world'])
        with self.assertRaisesRegex(ValidationError, 'identity allocator'):
            Room.objects.bulk_create([
                Room(
                    world=self.world,
                    relative_id=20,
                    name='Bypass',
                    x=20,
                    y=0,
                    z=0,
                ),
            ])
        with self.assertRaisesRegex(ValidationError, 'identity allocator'):
            Room._base_manager.bulk_create([
                Room(
                    world=self.world,
                    relative_id=21,
                    name='Base manager bypass',
                    x=21,
                    y=0,
                    z=0,
                ),
            ])

    def test_imported_identity_must_be_positive_integer(self):
        for invalid_value in (0, -1, True, 'not-a-number'):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaises(ValidationError):
                    create_room(
                        world=self.world,
                        name='Invalid',
                        x=1,
                        relative_id=invalid_value,
                    )

    def test_allocator_exhaustion_does_not_overflow_bigint_counter(self):
        with self.assertRaisesRegex(ValidationError, 'space is exhausted'):
            create_room(
                world=self.world,
                name='Explicit overflow room',
                x=-1,
                relative_id=BIGINT_MAX,
            )
        self.world.refresh_from_db()
        self.assertEqual(self.world.next_room_relative_id, 1)

        World.objects.advance_room_identity_allocator(
            world_id=self.world.pk,
            next_relative_id=MAX_ALLOCATABLE_ROOM_RELATIVE_ID,
        )
        final_room = create_room(
            world=self.world,
            name='Final allocatable room',
            x=0,
        )
        self.world.refresh_from_db()

        self.assertEqual(
            final_room.relative_id,
            MAX_ALLOCATABLE_ROOM_RELATIVE_ID,
        )
        self.assertEqual(self.world.next_room_relative_id, BIGINT_MAX)

        with self.assertRaisesRegex(ValidationError, 'space is exhausted'):
            create_room(
                world=self.world,
                name='Overflow room',
                x=1,
            )
        with self.assertRaisesRegex(ValidationError, 'space is exhausted'):
            create_room(
                world=self.world,
                name='Exhausted imported room',
                x=2,
                relative_id=MAX_ALLOCATABLE_ROOM_RELATIVE_ID,
            )

        self.world.refresh_from_db()
        self.assertEqual(self.world.next_room_relative_id, BIGINT_MAX)
        self.assertEqual(self.world.rooms.count(), 1)


class ConcurrentRoomRelativeIDTests(TransactionTestCase):

    def setUp(self):
        self.world = World.objects.create(name='Concurrent Rooms')

    def test_concurrent_creates_allocate_distinct_ids(self):
        create_count = 4
        barrier = Barrier(create_count)

        def create_concurrently(index):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                room = Room.objects.create(
                    world_id=self.world.pk,
                    name=f'Room {index}',
                    x=index,
                    y=0,
                    z=0,
                )
                return room.relative_id
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=create_count) as executor:
            relative_ids = list(
                executor.map(create_concurrently, range(create_count))
            )

        self.world.refresh_from_db()
        self.assertEqual(sorted(relative_ids), [1, 2, 3, 4])
        self.assertEqual(self.world.next_room_relative_id, 5)

    def test_database_rejects_retired_identity_through_plain_manager(self):
        migration = importlib.import_module(
            'worlds.migrations.0124_enforce_room_identity_inserts'
        )
        connection = connections['default']
        with connection.schema_editor() as schema_editor:
            migration.create_room_identity_insert_trigger(
                None,
                schema_editor,
            )
        try:
            retired = create_room(
                world=self.world,
                name='Retired',
                x=0,
            )
            retired_relative_id = retired.relative_id
            retired.delete()
            self.world.refresh_from_db()
            next_relative_id = self.world.next_room_relative_id

            plain_manager = models.Manager()
            plain_manager.model = Room
            with self.assertRaises(IntegrityError), transaction.atomic():
                plain_manager.bulk_create([
                    Room(
                        world=self.world,
                        relative_id=retired_relative_id,
                        name='Raw bypass',
                        x=1,
                        y=0,
                        z=0,
                    ),
                ])

            self.world.refresh_from_db()
            self.assertEqual(
                self.world.next_room_relative_id,
                next_relative_id,
            )
            self.assertFalse(
                self.world.rooms.filter(
                    relative_id=retired_relative_id,
                ).exists()
            )

            plain_world_manager = models.Manager()
            plain_world_manager.model = World
            with self.assertRaises(IntegrityError), transaction.atomic():
                plain_world_manager.filter(pk=self.world.pk).update(
                    next_room_relative_id=1,
                )
            self.world.refresh_from_db()
            self.assertEqual(
                self.world.next_room_relative_id,
                next_relative_id,
            )
        finally:
            with connection.schema_editor() as schema_editor:
                migration.drop_room_identity_insert_trigger(
                    None,
                    schema_editor,
                )

    def test_full_world_save_locks_allocator_until_update_completes(self):
        allocator_read = Event()
        release_world_save = Event()
        room_create_started = Event()
        room_create_finished = Event()

        def save_stale_world():
            close_old_connections()
            try:
                stale_world = World.objects.get(pk=self.world.pk)
                stale_world.description = 'Concurrent unrelated edit'
                paused = False

                def pause_after_allocator_read(
                    execute,
                    sql,
                    params,
                    many,
                    context,
                ):
                    nonlocal paused
                    result = execute(sql, params, many, context)
                    normalized_sql = sql.upper()
                    if (
                        not paused
                        and normalized_sql.lstrip().startswith('SELECT')
                        and 'WORLDS_WORLD' in normalized_sql
                        and 'NEXT_ROOM_RELATIVE_ID' in normalized_sql
                    ):
                        paused = True
                        allocator_read.set()
                        if not release_world_save.wait(timeout=5):
                            raise RuntimeError(
                                'Timed out waiting to release world save.')
                    return result

                connection = connections['default']
                with connection.execute_wrapper(pause_after_allocator_read):
                    stale_world.save()
            finally:
                close_old_connections()

        def create_concurrently():
            close_old_connections()
            try:
                room_create_started.set()
                room = Room.objects.create(
                    world_id=self.world.pk,
                    name='Concurrent room',
                    x=0,
                    y=0,
                    z=0,
                )
                return room.relative_id
            finally:
                room_create_finished.set()
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            save_future = executor.submit(save_stale_world)
            self.assertTrue(allocator_read.wait(timeout=5))
            create_future = executor.submit(create_concurrently)
            self.assertTrue(room_create_started.wait(timeout=5))

            # The allocator SELECT must hold the world row lock until the
            # unrelated full save has written its refreshed counter.
            completed_while_world_save_paused = room_create_finished.wait(
                timeout=0.5,
            )
            release_world_save.set()

            save_future.result(timeout=5)
            first_relative_id = create_future.result(timeout=5)

        self.assertFalse(completed_while_world_save_paused)
        second = Room.objects.create(
            world=self.world,
            name='Following room',
            x=1,
            y=0,
            z=0,
        )
        self.world.refresh_from_db()

        self.assertEqual(first_relative_id, 1)
        self.assertEqual(second.relative_id, 2)
        self.assertEqual(self.world.next_room_relative_id, 3)
