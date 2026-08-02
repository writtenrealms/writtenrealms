from rest_framework import serializers

from quests import manifests as quest_manifests
from quests.models import QuestArcTemplate, QuestTemplate


class QuestArcTemplateSerializer(serializers.ModelSerializer):
    manifest = serializers.SerializerMethodField()
    yaml = serializers.SerializerMethodField()
    delete_manifest = serializers.SerializerMethodField()
    delete_yaml = serializers.SerializerMethodField()

    class Meta:
        model = QuestArcTemplate
        fields = [
            'id',
            'key',
            'slug',
            'name',
            'summary',
            'journal_policy',
            'manifest',
            'yaml',
            'delete_manifest',
            'delete_yaml',
        ]

    def get_manifest(self, obj):
        return quest_manifests.quest_arc_template_to_manifest(obj)

    def get_yaml(self, obj):
        return quest_manifests.manifest_to_yaml(self.get_manifest(obj))

    def get_delete_manifest(self, obj):
        return quest_manifests.quest_arc_delete_manifest(obj)

    def get_delete_yaml(self, obj):
        return quest_manifests.manifest_to_yaml(self.get_delete_manifest(obj))


class QuestTemplateSerializer(serializers.ModelSerializer):
    arc = serializers.SerializerMethodField()
    manifest = serializers.SerializerMethodField()
    yaml = serializers.SerializerMethodField()
    delete_manifest = serializers.SerializerMethodField()
    delete_yaml = serializers.SerializerMethodField()

    class Meta:
        model = QuestTemplate
        fields = [
            'id',
            'key',
            'slug',
            'name',
            'quest_type',
            'scope',
            'status',
            'arc',
            'repeatability_mode',
            'repeatability_cooldown_seconds',
            'max_active',
            'discovery_policy',
            'slot_schema',
            'graph',
            'reward_policy',
            'manifest_version',
            'manifest',
            'yaml',
            'delete_manifest',
            'delete_yaml',
        ]

    def get_arc(self, obj):
        if not obj.arc:
            return None
        return {
            'id': obj.arc.id,
            'key': obj.arc.key,
            'slug': obj.arc.slug,
            'name': obj.arc.name,
        }

    def _portable_manifest(self, obj):
        cache = self.context.setdefault('_portable_manifest_cache', {})
        cache_key = obj.pk if obj.pk is not None else id(obj)
        manifest = cache.get(cache_key)
        if manifest is None:
            manifest = quest_manifests.quest_template_to_portable_manifest(
                obj,
                entity_ref_cache=self.context.get('entity_ref_cache'),
                room_ref_cache=self.context.get('room_ref_cache'),
            )
            cache[cache_key] = manifest
        return manifest

    def get_manifest(self, obj):
        return self._portable_manifest(obj)

    def get_yaml(self, obj):
        return quest_manifests.manifest_to_yaml(
            self._portable_manifest(obj)
        )

    def get_delete_manifest(self, obj):
        return quest_manifests.quest_template_delete_manifest(obj)

    def get_delete_yaml(self, obj):
        return quest_manifests.manifest_to_yaml(self.get_delete_manifest(obj))


class QuestInstanceChoiceSerializer(serializers.Serializer):
    choice_id = serializers.CharField()
