from django.contrib.auth import get_user_model

from rest_framework import serializers

from core.serializers import ReferenceField
from worlds.models import Room, World, Zone, StartingEq


class UserSerializer(serializers.ModelSerializer):

    worlds = serializers.HyperlinkedRelatedField(
        many=True,
        view_name='world-detail',
        read_only=True)

    class Meta:
        model = get_user_model()
        fields = ('id', 'url', 'username', 'email', 'worlds')


class WorldSerializer(serializers.ModelSerializer):
    "Lobby world"

    author = serializers.ReadOnlyField(source='author.username')
    labels = serializers.SerializerMethodField()
    is_classless = serializers.SerializerMethodField()
    instance_of_id = serializers.SerializerMethodField()

    class Meta:
        model = World
        fields = (
            'id', 'key', 'context_id',
            'name', 'description', 'author', 'created_ts',
            'factions',
            'labels',
            'is_classless',
            'instance_of_id',
        )

    def get_labels(self, world):

        attack_labels = {
            'anathema': 'Anathema',
            'attack': 'Attack',
            'attackspell': 'Attack',
            'backstab': 'Backstab',
            'bash': 'Bash',
            'blind': 'Blind',
            'burn': 'Burn',
            'burn_dot': 'Burn',
            'cleave': 'Cleave',
            'combust': 'Combust',
            'compel': 'Compel',
            'conditionaleffectattack': 'Attack',
            'counter': 'Counter',
            'crash': 'Crash',
            'customattack': 'Attack',
            'customdot': 'Attack',
            'customhot': 'Attack',
            'customheal': 'Attack',
            'dancingslash': 'Dancing Slash',
            'dazeattack': 'Attack',
            'dotspell': 'Attack',
            'effectattack': 'Attack',
            'flare': 'Flare',
            'flurry': 'Flurry',
            'forcedmoveattack': 'Attack',
            'freeze': 'Freeze',
            'frostspike': 'Spike',
            'gutpunch': 'Gut Punch',
            'heal': 'Heal',
            'healingspell': 'Attack',
            'heartstrike': 'Heart Strike',
            'hiltsmack': 'Hilt Smack',
            'hotspell': 'Attack',
            'hush': 'Hush',
            'innervate': 'Innervate',
            'jolt': 'Jolt',
            'knee': 'Knee',
            'lightningtorrent': 'Torrent',
            'mend': 'Mend',
            'meteor': 'Meteor',
            'mistbornheal': 'Mistborn',
            'quickstrike': 'Quick Strike',
            'rage_dot': 'Rage',
            'ravage': 'Ravage',
            'repent_attack': 'Repent',
            'repent_heal': 'Repent',
            'roomdamage': 'Attack',
            'secondwindheal': 'Second Wind',
            'shieldslam': 'Shield Slam',
            'sleep': 'Sleep',
            'smash': 'Smash',
            'splashattack': 'Attack',
            'stomp': 'Stomp',
            'wrack': 'Wrack',
        }

        effect_labels = {
            '': 'Effect',
            'absorb': 'Effect',
            'avatar': 'Avatar',
            'barrier': 'Barrier',
            'blind': 'Blind',
            'brace': 'Brace',
            'buff': 'Effect',
            'burn': 'Burn',
            'charged': 'Charged',
            'compel': 'Compel',
            'counter': 'Counter',
            'dancingslash': 'Dancing Slash',
            'daze': 'Daze',
            'debuff': 'Effect',
            'dispel': 'Effect',
            'dot': 'DOT',
            'freeze': 'Freeze',
            'fury': 'Fury',
            'haste': 'Effect',
            'hot': 'HOT',
            'immune': 'Phase Shift',
            'innervate': 'Innervate',
            'invisibility': 'Effect',
            'maelstrom': 'Maelstrom',
            'martyr': 'Martyr',
            'mend': 'Mend',
            'mistborn': 'Mistborn',
            'mistform': 'Mistform',
            'nightmare': 'Nightmare',
            'purge': 'Purge',
            'purify': 'Purify',
            'quicken': 'Quicken',
            'rage': 'Rage',
            'seal': 'Seal',
            'shield': 'Shield',
            'silence': 'Silence',
            'sleep': 'Sleep',
            'static': 'Static',
            'stealth': 'Effect',
            'stun': 'Stun',
            'summon': 'Effect',
            'thrill': 'Thrill',
            'ward': 'Ward',
            'weave': 'Weave',
            'will': 'Will',
            'wind': 'Second Wind',
            'winded': 'Winded',
            'wrack': 'Wrack',
        }

        return {
            'attacks': attack_labels,
            'effects': effect_labels,
        }

    def get_is_classless(self, world):
        root_world = world.context or world
        root_world = root_world.instance_of or root_world
        return root_world.config.is_classless

    def get_instance_of_id(self, world):
        context = world.context or world
        return context.instance_of_id

class ZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = Zone
        fields = (
            'id',
            'key',
            'name',
            'description',
            'created_ts',
            'key',
            'world',
        )


class RoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Room
        fields = ('id', 'key', 'name', 'description', 'x', 'y', 'z', 'zone')


class StartingEqSerializer(serializers.ModelSerializer):
    itemtemplate = ReferenceField()

    class Meta:
        model = StartingEq
        fields = (
            'id',
            'itemtemplate',
            'num',
            'archetype',
        )