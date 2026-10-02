import os
from pathlib import Path
import runpy
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

from celery.fixups.django import DjangoWorkerFixup
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.utils import ConnectionHandler
from django.test import SimpleTestCase, TransactionTestCase


PRODUCTION_SETTINGS = Path(__file__).resolve().parents[1] / 'config/settings/production.py'


def production_database_settings(max_age=None):
    environment = {
        'DJANGO_SECRET_KEY': 's' * 50,
        'JWT_SECRET': 'j' * 50,
        'ALLOWED_HOSTS': 'example.invalid',
        'WR_SITE_BASE': 'https://example.invalid',
        'POSTGRES_DB': 'example',
        'POSTGRES_USER': 'example',
        'POSTGRES_PASSWORD': 'test-only',
        'POSTGRES_HOST': 'db.example.invalid',
        'POSTGRES_SSLROOTCERT': '/test-only/root.crt',
        'CELERY_BROKER_URL': 'amqp://example.invalid',
    }
    with patch.dict(os.environ, environment):
        if max_age is None:
            os.environ.pop('POSTGRES_CONN_MAX_AGE', None)
        else:
            os.environ['POSTGRES_CONN_MAX_AGE'] = max_age
        return runpy.run_path(str(PRODUCTION_SETTINGS))['DATABASES']['default']


class TestProductionDatabaseSettings(SimpleTestCase):
    def test_asgi_connections_default_to_no_reuse(self):
        database = production_database_settings()
        self.assertEqual(database['CONN_MAX_AGE'], 0)
        self.assertTrue(database['CONN_HEALTH_CHECKS'])

    def test_worker_can_enable_bounded_reuse(self):
        database = production_database_settings('60')
        self.assertEqual(database['CONN_MAX_AGE'], 60)
        self.assertTrue(database['CONN_HEALTH_CHECKS'])
        self.assertEqual(database['OPTIONS']['sslmode'], 'verify-full')

    def test_invalid_or_unbounded_values_fail_configuration(self):
        for value in ('-1', 'None', 'forever', '1.5', ''):
            with self.subTest(value=value), self.assertRaisesMessage(
                ImproperlyConfigured, 'POSTGRES_CONN_MAX_AGE must be a nonnegative integer.',
            ):
                production_database_settings(value)


class TestCeleryDatabaseReuse(TransactionTestCase):
    """Exercise the installed Celery/Django lifecycle with an isolated connection."""

    def setUp(self):
        database_settings = dict(connection.settings_dict)
        production = production_database_settings('60')
        for key in ('CONN_MAX_AGE', 'CONN_HEALTH_CHECKS'):
            database_settings[key] = production[key]
        self.database = ConnectionHandler({'default': database_settings})['default']
        self.addCleanup(self.database.close)
        self.fixup = DjangoWorkerFixup(SimpleNamespace(conf={}))
        self.fixup._db = SimpleNamespace(
            connections=SimpleNamespace(all=lambda: [self.database]),
        )
        self.enterContext(patch.object(self.fixup, 'close_cache'))
        self.task = SimpleNamespace(request=SimpleNamespace(is_eager=False))

    def _query(self):
        with self.database.cursor() as cursor:
            cursor.execute('SELECT 1')
            self.assertEqual(cursor.fetchone(), (1,))

    def _next_task(self):
        self.fixup.on_task_postrun(sender=self.task)
        self.fixup.on_task_prerun(sender=self.task)

    def test_worker_reuses_connection_and_health_checks_once_per_task(self):
        self._query()
        original = self.database.connection
        self._next_task()
        with patch.object(self.database, 'is_usable', wraps=self.database.is_usable) as health:
            self._query()
            self._query()
            self.assertIs(self.database.connection, original)
            health.assert_called_once_with()

    def test_expired_connection_is_replaced_at_task_boundary(self):
        self._query()
        original = self.database.connection
        self.database.close_at = monotonic() - 1
        self._next_task()
        self.assertIsNone(self.database.connection)
        self.assertTrue(original.closed)
        self._query()
        self.assertIsNot(self.database.connection, original)

    def test_failed_health_check_reconnects_before_command_query(self):
        self._query()
        original = self.database.connection
        self._next_task()
        with patch.object(self.database, 'is_usable', return_value=False):
            self._query()
        self.assertTrue(original.closed)
        self.assertIsNot(self.database.connection, original)
