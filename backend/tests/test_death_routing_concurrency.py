from queue import Queue
from threading import Event, Thread
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from core.death_routing import (
    DeathRoutingCompilation,
    acquire_death_routing_config_locks,
    death_routing_config_ids_for_world,
    replace_compiled_policy,
)
from worlds.models import World, WorldConfig


@skipUnless(
    connection.vendor == "postgresql",
    "Death-routing advisory-lock concurrency requires PostgreSQL.",
)
class TestDeathRoutingConfigLockConcurrency(TransactionTestCase):
    wait_timeout_seconds = 5

    def setUp(self):
        super().setUp()
        self.config = WorldConfig.objects.create()
        self.world = World.objects.new_world(
            name="Death Routing Lock World",
            config=self.config,
        )

    def _wait_for_blocked_advisory_lock(self, backend_pid, worker_errors):
        deadline = monotonic() + self.wait_timeout_seconds
        while monotonic() < deadline:
            if not worker_errors.empty():
                self._fail_for_worker_errors(worker_errors)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_locks
                        WHERE pid = %s
                          AND locktype = 'advisory'
                          AND NOT granted
                    )
                    """,
                    [backend_pid],
                )
                if cursor.fetchone()[0]:
                    return
            sleep(0.01)
        self.fail("Exclusive publication never reached the advisory-lock wait.")

    def _fail_for_worker_errors(self, worker_errors):
        errors = []
        while not worker_errors.empty():
            worker_name, error = worker_errors.get_nowait()
            errors.append(f"{worker_name}: {error!r}")
        if errors:
            self.fail("Worker failure(s): " + "; ".join(errors))

    def _join_workers(self, *workers):
        for worker in workers:
            if worker is not None:
                worker.join(timeout=self.wait_timeout_seconds)
        still_running = [
            worker.name
            for worker in workers
            if worker is not None and worker.is_alive()
        ]
        if still_running:
            self.fail(
                "Database worker(s) did not stop: "
                + ", ".join(still_running)
            )

    def test_shared_config_lock_blocks_exclusive_publication_until_release(self):
        shared_acquired = Event()
        release_shared = Event()
        shared_released = Event()
        publication_attempting = Event()
        publication_finished = Event()
        worker_errors = Queue()
        backend_pids = {}

        def hold_shared_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    acquire_death_routing_config_locks(
                        [self.config.id],
                        shared=True,
                    )
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid()")
                        backend_pids["shared"] = cursor.fetchone()[0]
                    shared_acquired.set()
                    if not release_shared.wait(timeout=10):
                        raise AssertionError(
                            "Timed out waiting to release the shared lock."
                        )
                shared_released.set()
            except BaseException as error:
                worker_errors.put(("shared holder", error))
            finally:
                connection.close()

        def publish_exclusive_policy():
            close_old_connections()
            try:
                world = World.objects.get(pk=self.world.id)
                config = WorldConfig.objects.get(pk=self.config.id)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pids["publisher"] = cursor.fetchone()[0]
                    cursor.execute("SET lock_timeout = '5s'")
                publication_attempting.set()
                replace_compiled_policy(
                    world=world,
                    config=config,
                    compilation=DeathRoutingCompilation(routes=()),
                )
                publication_finished.set()
            except BaseException as error:
                worker_errors.put(("exclusive publisher", error))
            finally:
                connection.close()

        shared_worker = Thread(
            target=hold_shared_lock,
            name="death-routing-shared-holder",
        )
        publisher_worker = None
        shared_worker.start()
        try:
            self.assertTrue(
                shared_acquired.wait(timeout=self.wait_timeout_seconds),
                "The shared lock was not acquired.",
            )
            publisher_worker = Thread(
                target=publish_exclusive_policy,
                name="death-routing-exclusive-publisher",
            )
            publisher_worker.start()
            self.assertTrue(
                publication_attempting.wait(timeout=self.wait_timeout_seconds),
                "Exclusive publication did not start.",
            )
            self._wait_for_blocked_advisory_lock(
                backend_pids["publisher"],
                worker_errors,
            )
            self.assertFalse(publication_finished.is_set())

            release_shared.set()
            self.assertTrue(
                shared_released.wait(timeout=self.wait_timeout_seconds),
                "The shared-lock transaction did not commit.",
            )
            self.assertTrue(
                publication_finished.wait(timeout=self.wait_timeout_seconds),
                "Exclusive publication did not resume after shared release.",
            )
        finally:
            release_shared.set()
            self._join_workers(shared_worker, publisher_worker)

        self._fail_for_worker_errors(worker_errors)
        self.assertNotEqual(
            backend_pids["shared"],
            backend_pids["publisher"],
        )
        self.config.refresh_from_db()
        self.assertEqual(self.config.death_routing_generation, 1)

    def test_multiple_shared_config_locks_can_coexist(self):
        first_acquired = Event()
        second_acquired = Event()
        release_shared = Event()
        worker_errors = Queue()
        backend_pids = {}

        def hold_shared_lock(worker_name, acquired):
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET lock_timeout = '5s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        backend_pids[worker_name] = cursor.fetchone()[0]
                    acquire_death_routing_config_locks(
                        [self.config.id],
                        shared=True,
                    )
                    acquired.set()
                    if not release_shared.wait(timeout=10):
                        raise AssertionError(
                            "Timed out waiting to release shared locks."
                        )
            except BaseException as error:
                worker_errors.put((worker_name, error))
            finally:
                connection.close()

        first_worker = Thread(
            target=hold_shared_lock,
            args=("first", first_acquired),
            name="death-routing-first-shared-holder",
        )
        second_worker = None
        first_worker.start()
        try:
            self.assertTrue(
                first_acquired.wait(timeout=self.wait_timeout_seconds),
                "The first shared lock was not acquired.",
            )
            second_worker = Thread(
                target=hold_shared_lock,
                args=("second", second_acquired),
                name="death-routing-second-shared-holder",
            )
            second_worker.start()
            self.assertTrue(
                second_acquired.wait(timeout=self.wait_timeout_seconds),
                "A second shared lock could not coexist with the first.",
            )
            self.assertNotEqual(
                backend_pids["first"],
                backend_pids["second"],
            )
        finally:
            release_shared.set()
            self._join_workers(first_worker, second_worker)

        self._fail_for_worker_errors(worker_errors)


class TestDeathRoutingConfigLockFamilies(TransactionTestCase):
    def test_instance_routing_config_ids_include_local_and_base_in_order(self):
        # Create the local config first so sorting cannot pass accidentally from
        # returning a hard-coded base-then-local sequence.
        local_config = WorldConfig.objects.create()
        base_config = WorldConfig.objects.create()
        base_world = World.objects.new_world(
            name="Death Routing Lock Base",
            config=base_config,
        )
        instance_world = World.objects.new_world(
            name="Death Routing Lock Instance",
            config=local_config,
            instance_of=base_world,
        )

        config_ids = death_routing_config_ids_for_world(
            world=instance_world,
            config=local_config,
        )

        self.assertEqual(
            config_ids,
            tuple(sorted((local_config.id, base_config.id))),
        )
        self.assertEqual(set(config_ids), {local_config.id, base_config.id})
