from django.db import models

from core.db import AdventBaseModel, optional, list_to_choice


QUEST_TEMPLATE_TYPES = [
    'questlet',
    'quest',
    'contract',
    'world_event',
]

QUEST_SCOPES = [
    'player',
    'party',
    'guild',
    'world',
]

QUEST_TEMPLATE_STATUSES = [
    'draft',
    'active',
    'archived',
]

QUEST_REPEATABILITY_MODES = [
    'never',
    'cooldown',
    'always',
]

QUEST_INSTANCE_STATUSES = [
    'active',
    'resolved',
]

QUEST_INSTANCE_RESOLUTIONS = [
    'complete',
    'abandoned',
]

QUEST_OBJECTIVE_STATUSES = [
    'active',
    'complete',
    'failed',
    'hidden',
]

QUEST_JOURNAL_ENTRY_TYPES = [
    'step_entered',
    'objective_updated',
    'resolved',
    'system',
]


class QuestArcTemplate(AdventBaseModel):
    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='quest_arc_templates',
    )
    slug = models.SlugField(max_length=100)
    name = models.TextField()
    summary = models.TextField(**optional)
    journal_policy = models.JSONField(default=dict)

    class Meta(AdventBaseModel.Meta):
        unique_together = [('world', 'slug')]
        ordering = ['name', 'created_ts']


class QuestTemplate(AdventBaseModel):
    """
    Canonical authored quest definition.

    Structured manifest-backed fields are stored as JSON on the model:

    - ``discovery_policy`` stores ``spec.discovery``
    - ``slot_schema`` stores ``spec.slots``
    - ``graph`` stores ``{"steps": spec.steps}``
    - ``reward_policy`` stores ``spec.rewards``

    The manifest validation/schema for those payloads lives in
    ``quests.manifests`` (`QuestSpec`, `QuestStepSpec`, and related models).
    """
    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='quest_templates',
    )
    arc = models.ForeignKey(
        'quests.QuestArcTemplate',
        on_delete=models.SET_NULL,
        related_name='quest_templates',
        **optional,
    )
    slug = models.SlugField(max_length=100)
    name = models.TextField()
    quest_type = models.TextField(
        choices=list_to_choice(QUEST_TEMPLATE_TYPES),
        default='quest',
    )
    scope = models.TextField(
        choices=list_to_choice(QUEST_SCOPES),
        default='player',
    )
    status = models.TextField(
        choices=list_to_choice(QUEST_TEMPLATE_STATUSES),
        default='draft',
    )
    repeatability_mode = models.TextField(
        choices=list_to_choice(QUEST_REPEATABILITY_MODES),
        default='never',
    )
    repeatability_cooldown_seconds = models.PositiveIntegerField(default=0)
    max_active = models.PositiveIntegerField(default=1)
    # Schema: quests.manifests.QuestDiscoverySpec (QuestSpec.discovery).
    discovery_policy = models.JSONField(default=dict)
    # Schema: quests.manifests.QuestSpec.slots (arbitrary per-slot dicts; validated with the quest).
    slot_schema = models.JSONField(default=dict)
    # Schema: {"steps": [...]} where each step matches quests.manifests.QuestStepSpec (QuestSpec.steps).
    graph = models.JSONField(default=dict)
    # Schema: quests.manifests.QuestRewardsSpec (QuestSpec.rewards).
    reward_policy = models.JSONField(default=dict)
    manifest_version = models.TextField(default='v1alpha1')

    class Meta(AdventBaseModel.Meta):
        unique_together = [('world', 'slug')]
        ordering = ['name', 'created_ts']


class QuestInstance(AdventBaseModel):
    world = models.ForeignKey(
        'worlds.World',
        on_delete=models.CASCADE,
        related_name='quest_instances',
    )
    template = models.ForeignKey(
        'quests.QuestTemplate',
        on_delete=models.CASCADE,
        related_name='instances',
    )
    player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='quest_instances',
    )
    status = models.TextField(
        choices=list_to_choice(QUEST_INSTANCE_STATUSES),
        default='active',
    )
    resolution = models.TextField(
        choices=list_to_choice(QUEST_INSTANCE_RESOLUTIONS),
        **optional,
    )
    current_step_id = models.TextField(**optional)
    slot_bindings = models.JSONField(default=dict)
    local_state = models.JSONField(default=dict)
    visible_objective_ids = models.JSONField(default=list)
    resolved_at = models.DateTimeField(**optional)
    expires_at = models.DateTimeField(**optional)
    last_journal_entry_at = models.DateTimeField(**optional)

    class Meta(AdventBaseModel.Meta):
        ordering = ['-modified_ts', '-created_ts']
        indexes = [
            models.Index(fields=['player', 'status']),
            models.Index(fields=['template', 'status']),
            models.Index(fields=['world', 'status']),
        ]


class QuestObjectiveState(AdventBaseModel):
    quest_instance = models.ForeignKey(
        'quests.QuestInstance',
        on_delete=models.CASCADE,
        related_name='objective_states',
    )
    objective_id = models.TextField()
    text = models.TextField(**optional)
    status = models.TextField(
        choices=list_to_choice(QUEST_OBJECTIVE_STATUSES),
        default='active',
    )
    progress_current = models.PositiveIntegerField(default=0)
    progress_target = models.PositiveIntegerField(default=1)
    distinct_values = models.JSONField(default=list)
    last_matching_event_type = models.TextField(**optional)
    last_matching_event_at = models.DateTimeField(**optional)
    deadline_at = models.DateTimeField(**optional)

    class Meta(AdventBaseModel.Meta):
        unique_together = [('quest_instance', 'objective_id')]
        ordering = ['created_ts']


class QuestJournalEntry(AdventBaseModel):
    quest_instance = models.ForeignKey(
        'quests.QuestInstance',
        on_delete=models.CASCADE,
        related_name='journal_entries',
    )
    step_id = models.TextField(**optional)
    entry_type = models.TextField(
        choices=list_to_choice(QUEST_JOURNAL_ENTRY_TYPES),
        default='step_entered',
    )
    recap = models.TextField(**optional)
    payload = models.JSONField(default=dict)

    class Meta(AdventBaseModel.Meta):
        ordering = ['created_ts']


class QuestOfferState(AdventBaseModel):
    player = models.ForeignKey(
        'spawns.Player',
        on_delete=models.CASCADE,
        related_name='quest_offer_states',
    )
    template = models.ForeignKey(
        'quests.QuestTemplate',
        on_delete=models.CASCADE,
        related_name='offer_states',
    )
    is_visible = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(**optional)
    last_accepted_at = models.DateTimeField(**optional)
    last_resolved_at = models.DateTimeField(**optional)
    cooldown_until = models.DateTimeField(**optional)
    snoozed_until = models.DateTimeField(**optional)
    dismiss_count = models.PositiveIntegerField(default=0)

    class Meta(AdventBaseModel.Meta):
        unique_together = [('player', 'template')]
        ordering = ['-modified_ts', '-created_ts']
