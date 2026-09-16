from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from backend.config.exceptions import ServiceError
from builders.currencies import create_currency
from builders.instance_templates import create_instance_template
from builders.models import FACTION_TYPE_CORE, Faction
from builders.world_export import manifest_stream_to_yaml, serialize_world_export_payload
from config import constants
from core.world_config import inherited_system_config
from spawns.actions.combat import _flee_destination_for_direction, _flee_route_context
from spawns.actions.base import ActionError
from spawns.models import Player, PlayerEvent
from spawns.serializers import AnimateWorldSerializer
from spawns.services import WorldGate
from spawns.state_payloads import serialize_world
from worlds.models import World
from worlds.tasks import run_world_spawn_plans


class WorldSettingsPortabilityTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('world-rules-export@example.com', 'p')
        self.client.force_authenticate(self.user)
        self.source = World.objects.new_world(
            name='Phalanx', author=self.user, is_multiplayer=True,
        )
        create_currency(world=self.source, code='obol', name='Obol')

    def _target(self):
        response = self.client.post(reverse('builder-world-list'), {
            'name': 'Destination', 'is_multiplayer': True,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        target = World.objects.get(pk=response.data['id'])
        self.assertNotEqual(target.pk, self.source.pk)
        return target

    def _apply(self, world, documents):
        return self.client.post(
            reverse('builder-world-manifest-apply', args=[world.pk]),
            {'manifest': manifest_stream_to_yaml(documents)}, format='json',
        )

    def _patch(self, world, spec):
        return self._apply(world, [{'kind': 'world', 'spec': spec}])

    def _set_config(self, world, **values):
        for field, value in values.items():
            setattr(world.config, field, value)
        world.config.save(update_fields=list(values))

    def _import(self):
        target = self._target()
        response = self._apply(target, serialize_world_export_payload(self.source)['documents'])
        self.assertEqual(response.status_code, 200, response.data)
        target.refresh_from_db()
        return target

    def _round_trip(self, *, bundle):
        instance = None
        if bundle:
            instance = create_instance_template(
                base_world=self.source, author=self.user, name='Outpost', instance_slug='outpost',
            )
        target = self._target()
        # Import must not overwrite destination admission or retained compatibility flags.
        excluded = {'can_create_chars': False, 'autoflee': 44, 'has_corpse_decay': False}
        self._set_config(target, **excluded)
        for never_reload, flee_to_unknown_rooms, cooldown in ((True, False, 7), (False, True, 0)):
            values = {
                'never_reload': never_reload, 'flee_to_unknown_rooms': flee_to_unknown_rooms,
                'cross_race_cooldown': cooldown,
            }
            self._set_config(self.source, **values)
            if instance:
                self._set_config(
                    instance, never_reload=not never_reload, cross_race_cooldown=cooldown + 3,
                )
            documents = serialize_world_export_payload(self.source)['documents']
            for document in documents:
                if document['kind'] != 'world':
                    continue
                spec = document['spec']
                for field in excluded:
                    self.assertNotIn(field, spec)
                self.assertIn('never_reload', spec)
                self.assertIn('cross_race_cooldown', spec)
                if document.get('metadata', {}).get('world_ref', 'world@base') != 'world@base':
                    self.assertNotIn('flee_to_unknown_rooms', spec)
                else:
                    self.assertEqual(spec['flee_to_unknown_rooms'], flee_to_unknown_rooms)
            for _ in range(2):
                response = self._apply(target, documents)
                self.assertEqual(response.status_code, 200, response.data)
                target.refresh_from_db()
                for field, value in {**values, **excluded}.items():
                    self.assertEqual(getattr(target.config, field), value, field)
                config_response = self.client.get(reverse('builder-world-config', args=[target.pk]))
                self.assertEqual(config_response.status_code, 200, config_response.data)
                for field, value in values.items():
                    self.assertEqual(config_response.data['config'][field], value)
                    self.assertEqual(config_response.data['manifest']['spec'][field], value)
                if instance:
                    imported = World.objects.select_related('config', 'instance_of__config').get(
                        instance_of=target, instance_slug='outpost',
                    )
                    self.assertEqual(imported.config.never_reload, not never_reload)
                    self.assertEqual(imported.config.cross_race_cooldown, cooldown + 3)
                    self.assertEqual(
                        inherited_system_config(imported).flee_to_unknown_rooms, flee_to_unknown_rooms,
                    )
            self.assertEqual(serialize_world_export_payload(target)['documents'], documents)

    def test_world_rules_survive_fresh_import_reimport_and_reset_to_defaults(self):
        self._round_trip(bundle=False)

    def test_family_rules_preserve_local_overrides_and_base_flee_policy(self):
        self._round_trip(bundle=True)

    def test_partial_rules_preserve_omitted_fields_and_validate_values(self):
        self._set_config(self.source, never_reload=True, flee_to_unknown_rooms=False, cross_race_cooldown=9)
        response = self._patch(self.source, {'cross_race_cooldown': 0})
        self.assertEqual(response.status_code, 200, response.data)
        self.source.config.refresh_from_db()
        self.assertTrue(self.source.config.never_reload)
        self.assertFalse(self.source.config.flee_to_unknown_rooms)
        self.assertEqual(self.source.config.cross_race_cooldown, 0)
        for field, value in (
            ('never_reload', 'invalid'), ('flee_to_unknown_rooms', 'invalid'),
            ('cross_race_cooldown', -1), ('cross_race_cooldown', 1.5),
            ('cross_race_cooldown', True), ('cross_race_cooldown', 2147483648),
            ('can_create_chars', False), ('autoflee', 10), ('has_corpse_decay', False),
        ):
            with self.subTest(field=field, value=value):
                spec = {'never_reload': False, field: value}
                response = self._patch(self.source, spec)
                self.assertEqual(response.status_code, 400, response.data)
                self.source.config.refresh_from_db()
                self.assertTrue(self.source.config.never_reload)

    def test_instance_flee_policy_is_inherited_and_cannot_be_overridden(self):
        self._set_config(self.source, flee_to_unknown_rooms=False)
        instance = create_instance_template(
            base_world=self.source, author=self.user, name='Outpost', instance_slug='outpost',
        )
        self.assertTrue(instance.config.flee_to_unknown_rooms)  # Unused local storage keeps its default.
        response = self._patch(instance, {'flee_to_unknown_rooms': True})
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('inherit core systems', str(response.data))

    def test_imported_flee_policy_controls_base_and_instance_routes_and_payloads(self):
        self._set_config(self.source, flee_to_unknown_rooms=False)
        create_instance_template(
            base_world=self.source, author=self.user, name='Outpost', instance_slug='outpost',
        )
        target = self._import()
        scopes = [target, World.objects.get(instance_of=target, instance_slug='outpost')]
        for scope in scopes:
            runtime = scope.spawned_worlds.filter(is_multiplayer=True).first() or scope.create_spawn_world()
            room = scope.config.starting_room
            escape = room.create_at('east')
            player = Player.objects.create(
                user=self.user, name='Explorer', world=runtime, room=room, stamina=1000,
            )
            player.viewed_rooms.add(room)
            with self.assertRaises(ActionError):
                _flee_destination_for_direction(player, _flee_route_context(player), 'east')
            self.assertFalse(AnimateWorldSerializer(runtime).data['flee_to_unknown_rooms'])
            self.assertFalse(serialize_world(runtime)['flee_to_unknown_rooms'])
            player.viewed_rooms.add(escape)
            _flee_destination_for_direction(player, _flee_route_context(player), 'east')
            runtime = World.objects.select_related(
                'context__instance_of__config', 'context__config', 'config',
            ).get(pk=runtime.pk)
            with self.assertNumQueries(0):
                self.assertFalse(AnimateWorldSerializer().get_flee_to_unknown_rooms(runtime))

    def test_imported_never_reload_controls_scheduled_spawn_reconciliation(self):
        self._set_config(self.source, never_reload=True)
        target = self._import()
        runtime = target.spawned_worlds.filter(is_multiplayer=True).get()
        runtime.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)
        with patch('worlds.tasks.run_spawn_plans_for_world') as run:
            run_world_spawn_plans()
            self.assertNotIn(runtime.pk, [call.kwargs['world'].pk for call in run.call_args_list])
        response = self._patch(target, {'never_reload': False})
        self.assertEqual(response.status_code, 200, response.data)
        with patch('worlds.tasks.run_spawn_plans_for_world') as run:
            run_world_spawn_plans()
            self.assertIn(runtime.pk, [call.kwargs['world'].pk for call in run.call_args_list])

    def test_imported_cross_faction_cooldown_is_enforced_at_login(self):
        self._set_config(self.source, cross_race_cooldown=7)
        target = self._import()
        runtime = target.spawned_worlds.filter(is_multiplayer=True).get()
        runtime.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)
        user = get_user_model().objects.create_user('cooldown-player@example.com', 'p')
        players = []
        for code in ('greek', 'persian'):
            faction = Faction.objects.create(world=target, code=code, name=code, type=FACTION_TYPE_CORE)
            player = Player.objects.create(
                user=user, world=runtime, room=target.config.starting_room, name=code,
                core_faction=faction,
            )
            players.append(player)
        PlayerEvent.objects.create(player=players[0], event=constants.PLAYER_EVENT_LOGOUT)
        gate = WorldGate(player=players[1], world=runtime)
        gate.ip = None
        with self.assertRaisesMessage(ServiceError, 'wait 7 minutes'):
            gate.preflight()
        response = self._patch(target, {'cross_race_cooldown': 0})
        self.assertEqual(response.status_code, 200, response.data)
        runtime.refresh_from_db()
        gate.preflight()
