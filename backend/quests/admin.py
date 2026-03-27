from django.contrib import admin
from django.db.models import Count

from core.admin import BaseAdmin, ContextRootWorldFilter, DirectRootWorldFilter
from quests.models import (
    QuestArcTemplate,
    QuestJournalEntry,
    QuestInstance,
    QuestObjectiveState,
    QuestOfferState,
    QuestTemplate,
)


def template_count(quest_arc):
    return getattr(quest_arc, '_template_count', 0)


template_count.short_description = 'Templates'


def journal_entry_count(quest_instance):
    return getattr(quest_instance, '_journal_entry_count', 0)


journal_entry_count.short_description = 'Journal Entries'


def objective_count(quest_instance):
    return getattr(quest_instance, '_objective_count', 0)


objective_count.short_description = 'Objectives'


class QuestArcTemplateAdmin(BaseAdmin):
    list_display = ('id', 'slug', 'name', 'world', template_count)
    list_filter = (DirectRootWorldFilter,)
    raw_id_fields = ['world']
    search_fields = ['id', 'slug', 'name']
    list_select_related = ['world']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _template_count=Count('quest_templates', distinct=True),
        )


class QuestTemplateAdmin(BaseAdmin):
    list_display = (
        'id',
        'slug',
        'name',
        'world',
        'arc',
        'status',
        'quest_type',
        'scope',
    )
    list_filter = (
        DirectRootWorldFilter,
        'status',
        'quest_type',
        'scope',
        'repeatability_mode',
    )
    raw_id_fields = ['world', 'arc']
    search_fields = ['id', 'slug', 'name']
    list_select_related = ['world', 'arc']
    display_as_choicefield = [
        'quest_type',
        'scope',
        'status',
        'repeatability_mode',
    ]


class QuestInstanceAdmin(BaseAdmin):
    list_display = (
        'id',
        'template',
        'player',
        'world',
        'status',
        'resolution',
        'current_step_id',
        objective_count,
        journal_entry_count,
        'last_journal_entry_at',
        'resolved_at',
    )
    list_filter = (ContextRootWorldFilter, 'status', 'resolution')
    raw_id_fields = ['world', 'template', 'player']
    readonly_fields = ['created_ts', 'modified_ts', 'resolved_at', 'last_journal_entry_at']
    search_fields = [
        'id',
        'template__slug',
        'template__name',
        'player__name',
        'current_step_id',
    ]
    display_as_choicefield = ['status', 'resolution']
    list_select_related = ['template', 'player', 'world']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _objective_count=Count('objective_states', distinct=True),
            _journal_entry_count=Count('journal_entries', distinct=True),
        )


class QuestObjectiveStateAdmin(BaseAdmin):
    list_display = (
        'id',
        'quest_instance',
        'objective_id',
        'status',
        'progress_current',
        'progress_target',
        'last_matching_event_type',
        'last_matching_event_at',
    )
    list_filter = ('status',)
    raw_id_fields = ['quest_instance']
    readonly_fields = ['created_ts', 'modified_ts', 'last_matching_event_at']
    search_fields = [
        'id',
        'objective_id',
        'text',
        'quest_instance__template__slug',
        'quest_instance__player__name',
    ]
    display_as_choicefield = ['status']
    list_select_related = ['quest_instance', 'quest_instance__template', 'quest_instance__player']


class QuestJournalEntryAdmin(BaseAdmin):
    list_display = (
        'id',
        'quest_instance',
        'entry_type',
        'step_id',
        'created_ts',
    )
    list_filter = ('entry_type',)
    raw_id_fields = ['quest_instance']
    readonly_fields = ['created_ts', 'modified_ts']
    search_fields = [
        'id',
        'step_id',
        'recap',
        'quest_instance__template__slug',
        'quest_instance__player__name',
    ]
    display_as_choicefield = ['entry_type']
    list_select_related = ['quest_instance', 'quest_instance__template', 'quest_instance__player']


class QuestOfferStateAdmin(BaseAdmin):
    list_display = (
        'id',
        'player',
        'template',
        'is_visible',
        'cooldown_until',
        'snoozed_until',
        'last_seen_at',
        'last_accepted_at',
        'last_resolved_at',
    )
    list_filter = ('is_visible',)
    raw_id_fields = ['player', 'template']
    readonly_fields = ['created_ts', 'modified_ts', 'last_seen_at', 'last_accepted_at', 'last_resolved_at']
    search_fields = [
        'id',
        'player__name',
        'template__slug',
        'template__name',
    ]
    list_select_related = ['player', 'template']


admin.site.register(QuestArcTemplate, QuestArcTemplateAdmin)
admin.site.register(QuestTemplate, QuestTemplateAdmin)
admin.site.register(QuestInstance, QuestInstanceAdmin)
admin.site.register(QuestObjectiveState, QuestObjectiveStateAdmin)
admin.site.register(QuestJournalEntry, QuestJournalEntryAdmin)
admin.site.register(QuestOfferState, QuestOfferStateAdmin)
