from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

from spawns.models import Item


class LegacyWorldConfigCleanupTests(SimpleTestCase):
    def test_legacy_models_and_runtime_item_profile_are_removed(self):
        with self.assertRaises(LookupError):
            apps.get_model('builders', 'RandomItemProfile')
        with self.assertRaises(LookupError):
            apps.get_model('builders', 'TransformationTemplate')
        with self.assertRaises(FieldDoesNotExist):
            Item._meta.get_field('profile')

    def test_legacy_builder_endpoints_are_removed(self):
        paths = (
            '/api/v1/builder/worlds/1/randomitemprofiles/',
            '/api/v1/builder/worlds/1/randomitemprofiles/1/',
            '/api/v1/builder/worlds/1/transformationtemplates/',
            '/api/v1/builder/worlds/1/transformationtemplates/1/',
            '/api/v1/game/system/generate/drops/',
        )

        for path in paths:
            with self.subTest(path=path):
                with self.assertRaises(Resolver404):
                    resolve(path)
