from django.contrib.auth import get_user_model
from django.db import transaction

from rest_framework import serializers

from core.stat_system import (
    get_world_class_selection,
    get_world_label_bundle,
    world_uses_classes,
)
from worlds.models import Room, World, Zone


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
    class_selection = serializers.SerializerMethodField()
    is_classless = serializers.SerializerMethodField()
    instance_of_id = serializers.SerializerMethodField()

    class Meta:
        model = World
        fields = (
            'id', 'key', 'context_id',
            'name', 'description', 'author', 'created_ts',
            'factions',
            'labels',
            'class_selection',
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

        labels = {
            'attacks': attack_labels,
            'effects': effect_labels,
        }
        labels.update(get_world_label_bundle(world))
        return labels

    def get_is_classless(self, world):
        return not world_uses_classes(world)

    def get_class_selection(self, world):
        return get_world_class_selection(world)

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

    def update(self, instance, validated_data):
        mutable_fields = {"name", "description", "world"}
        changes = {
            field_name: value
            for field_name, value in validated_data.items()
            if field_name in mutable_fields
        }
        with transaction.atomic():
            zone = Zone.objects.select_for_update().get(pk=instance.pk)
            for field_name, value in changes.items():
                setattr(zone, field_name, value)
            if changes:
                zone.save(update_fields=[*changes, "modified_ts"])
        return zone


class RoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Room
        fields = ('id', 'key', 'name', 'description', 'x', 'y', 'z', 'zone')
