from rest_framework import serializers

from worlds.models import WorldInformation


class WorldInformationInputSerializer(serializers.Serializer):
    information = serializers.CharField(
        allow_blank=True, trim_whitespace=False, max_length=WorldInformation.MAX_LENGTH,
    )

    def validate_information(self, value):
        # DRF normally coerces numbers into strings; authored Markdown must be text.
        if not isinstance(self.initial_data.get('information'), str):
            raise serializers.ValidationError('Must be text.')
        return value


def world_information(world):
    return WorldInformation.objects.filter(world=world).values_list('information', flat=True).first() or ''


def save_world_information(world, information):
    WorldInformation.objects.update_or_create(world=world, defaults={'information': information})
