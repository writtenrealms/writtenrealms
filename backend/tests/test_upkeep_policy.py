from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from worlds.upkeep_policy import normalize_upkeep, normalize_timer, MAX_LIFETIME_SECONDS


class UpkeepPolicyTests(SimpleTestCase):
    def test_defaults_and_partial_updates(self):
        policy = normalize_upkeep({})
        self.assertEqual(policy['corpses']['player']['empty']['seconds'], 600)
        changed = normalize_upkeep({'corpses': {'npc': {'empty': {'mode':'none'}}}}, existing=policy)
        self.assertEqual(changed['corpses']['npc']['empty'], {'mode':'none'})
        self.assertEqual(changed['ground_items'], policy['ground_items'])
        self.assertEqual(policy['corpses']['npc']['empty']['seconds'], 60)

    def test_invalid_shapes_and_durations(self):
        for value in [None, [], {'mode':'fixed','seconds':True}, {'mode':'fixed','seconds':0},
                      {'mode':'fixed','seconds':-1}, {'mode':'fixed','seconds':1.5},
                      {'mode':'fixed','seconds':MAX_LIFETIME_SECONDS+1},
                      {'mode':'none','seconds':1}, {'mode':'fixed','seconds':2,'extra':True}]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalize_timer(value)
        with self.assertRaises(ValidationError):
            normalize_upkeep({'corpses':{'player':{'unclaimed':{'mode':'fixed','seconds':600}}}})
        with self.assertRaises(ValidationError):
            normalize_upkeep({'unknown':{}})

    def test_instance_inheritance_and_zone_override(self):
        self.assertEqual(normalize_upkeep({'mode':'inherit_world'}, instance=True), {})
        self.assertEqual(normalize_timer({'mode':'inherit_world'}, inherit=True), {'mode':'inherit_world'})
        with self.assertRaises(ValidationError):
            normalize_timer({'mode':'inherit_world'})
