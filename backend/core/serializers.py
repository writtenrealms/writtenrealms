from rest_framework import serializers
from rest_framework.fields import Field

from django.contrib.contenttypes.models import ContentType

from builders.models import (
    BuilderAssignment,
    ItemBundle,
    ItemDefinition,
    MobDefinition,
    Path,
    Faction,
    WorldBuilder)
from spawns.models import Mob, Player, Item
from users.models import User
from worlds.models import Zone, Room

class KeyNameSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()
    id = serializers.IntegerField()
    model_type = serializers.CharField()


class empty:
    """
    This class is used to represent no data being provided for a given input
    or output value.

    It is required because `None` may be a valid input or output value.
    """
    pass


class ReferenceField(Field):
    """
    Field meant to work nicely with the Advent frontend. Over time the use
    of this field will almost certainly be phased out, but for now it gets
    the job done.

    Generates output of: {
        "name": "Resource name",
        "key": "type.id"
    }

    For input, takes either "type.id" or {"key": "type.id"}
    """

    def to_representation(self, obj):
        return {
            'model_type': obj.__class__.__name__.lower(),
            'key': obj.key,
            'name': obj.name,
            'id': obj.id,
        }

    def to_internal_value(self, data):
        if data is None or data == 'None' or not data:
            if self.required:
                raise serializers.ValidationError("Field is required.")
            return None

        # Try {'key': 'zone.1'} type reference input
        if type(data) == dict:
            rtype, rid = data['key'].split('.')
        # Try 'zone.1' type input
        else:
            rtype, rid = data.split('.')

        # context = self.context['view'].world
        # if context.instance_of:
        #     context = context.instance_of

        if rtype == 'zone':
            return Zone.objects.get(
                #world=context,
                pk=rid)
        elif rtype == 'room':
            return Room.objects.get(
                #world=context,
                pk=rid)
        elif rtype == 'path':
            return Path.objects.get(
                #world=context,
                pk=rid)
        else:
            if rtype == 'mob_definition':
                return MobDefinition.objects.get(pk=rid)
            elif rtype == 'item_definition':
                return ItemDefinition.objects.get(pk=rid)
            elif rtype == 'item_bundle':
                return ItemBundle.objects.get(pk=rid)
            elif rtype == 'faction':
                return Faction.objects.get(pk=rid)
            elif rtype == 'user':
                return User.objects.get(pk=rid)
            elif rtype == 'builder_assignment':
                return BuilderAssignment.objects.get(pk=rid)
            elif rtype == 'world_builder':
                return WorldBuilder.objects.get(pk=rid)

        raise ValueError("Undeclared reference type: %s" % rtype)

def ref_field(obj):
    return ReferenceField().to_representation(obj)


class KeyField(Field):
    def to_representation(self, value):
        return value.key


class AuthorField(serializers.ReadOnlyField):
    def to_representation(self, author):
        return {
            'id': author.id,
            'key': author.key,
            'name': (
                author.username.capitalize()
                if author.username else 'Anonymous User'),
        } if author else None


class ContainerTypeField(Field):
    def to_internal_value(self, value):
        return ContentType.objects.get(model=value)
