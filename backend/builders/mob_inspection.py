"""Read-only runtime inspection. No gameplay actions, provider calls, or prompt storage."""
from types import SimpleNamespace

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from builders.models import WorldBuilder
from core.view_mixins import WorldValidatorMixin
from inference.configuration import Unavailable
from inference.models import InferenceControl
from inference.providers import request_body
from spawns.conversations.policy import GREETING, INSTRUCTIONS, bounded_prompt, knowledge_packet, policy_queryset, revision_for, world_knowledge
from spawns.models import ActiveEffect, CombatParticipant, CommunicationSession, Mob, MobConversationFocus, MobState


class MobPageQuery(serializers.Serializer):
    q = serializers.CharField(max_length=120, required=False, allow_blank=True, default='')
    after = serializers.IntegerField(min_value=0, max_value=2**63 - 1, required=False, default=0)
    inventory_after = serializers.IntegerField(min_value=0, max_value=2**63 - 1, required=False, default=0)


class PromptPreviewInput(serializers.Serializer):
    speech = serializers.CharField(max_length=280, allow_blank=False, trim_whitespace=False)
    use_active_conversation = serializers.BooleanField(default=True)


def record(instance):
    """Concrete fields only: never recurse into accounts, settings, or relations."""
    if instance is None:
        return None
    return {field.attname: field.value_from_object(instance) for field in instance._meta.concrete_fields}


def room_summary(room):
    return {'id': room.pk, 'name': room.name, 'relative_id': room.relative_id,
            'world_id': room.world_id, 'zone': room.zone.name, 'zone_id': room.zone_id}


def mob_summary(mob):
    return {'id': mob.pk, 'key': mob.key, 'name': mob.name, 'level': mob.level, 'type': mob.type,
            'room': room_summary(mob.room), 'health': mob.health, 'health_max': mob.health_max,
            'pending_deletion': mob.is_pending_deletion,
            'definition': {'id': mob.definition_id, 'slug': mob.definition.slug, 'name': mob.definition.name,
                           'world_id': mob.definition.world_id} if mob.definition_id else None}


def mob_queryset(runtime):
    return Mob.objects.filter(world=runtime).select_related('definition', 'room__zone')


def conversation_policy(runtime):
    content = runtime.context
    root_id = content.instance_of_id or content.pk
    return policy_queryset().filter(world_id=root_id).first()


def conversation_summary(mob, policy):
    entry = mob.definition.conversation if mob.definition_id else None
    return {'configured': entry is not None, 'world_enabled': bool(policy and policy.enabled),
            'supported': bool(mob.definition_id and mob.definition.mob_type == 'humanoid'
                              and mob.definition.talkable and mob.talkable),
            'greeting': (entry or {}).get('greeting') or GREETING,
            'knowledge': (entry or {}).get('knowledge', ''), 'when': (entry or {}).get('when', {})}


def mob_details(mob, policy, inventory_after=0):
    inventory = list(mob.inventory.filter(id__gt=inventory_after).order_by('id')
                     .values('id', 'name', 'type', 'level', 'is_pending_deletion')[:101])
    equipped = list(mob.equipment.inventory.order_by('id')
                    .values('id', 'name', 'type', 'level', 'is_pending_deletion')[:101]) if mob.equipment_id else []
    effects = list(ActiveEffect.objects.filter(target_mob=mob).order_by('id').values()[:101])
    factions = list(mob.faction_assignments.order_by('id')
                    .values('faction_id', 'faction__name', 'value')[:101])
    state = MobState.objects.filter(mob=mob).values('data', 'version').first()
    placement = mob.spawn_placement
    definition = mob.definition
    services = {}
    if definition:
        for kind in ('merchant', 'trainer', 'crafting'):
            profile = getattr(definition, f'{kind}_profile')
            if profile:
                services[kind] = {'id': profile.pk, 'name': profile.name,
                                  'availability': getattr(definition, f'{kind}_availability')}
    return {**mob_summary(mob), 'runtime': record(mob), 'definition_record': record(definition),
            'state': state or {'data': {}, 'version': None},
            'spawn': {'placement': record(placement), 'plan': {
                'id': placement.run.plan_id, 'name': placement.run.plan.name, 'slug': placement.run.plan.slug,
                'run_id': placement.run_id}} if placement else None,
            'services': services, 'conversation': conversation_summary(mob, policy),
            'inventory': {'items': inventory[:100], 'after': inventory_after,
                          'next_after': inventory[99]['id'] if len(inventory) > 100 else None},
            'equipment': {'slots': record(mob.equipment), 'items': equipped[:100], 'truncated': len(equipped) > 100},
            'effects': effects[:100], 'effects_truncated': len(effects) > 100,
            'factions': factions[:100], 'factions_truncated': len(factions) > 100}


class CanInspectRuntime(permissions.BasePermission):
    def has_permission(self, request, view):
        # Runtime data never inherits the legacy public-world builder exception.
        return (request.user.is_authenticated and (request.user.is_staff
                or view.world.author_id == request.user.pk
                or WorldBuilder.objects.filter(world=view.world, user=request.user, builder_rank__gt=0).exists()))


class RuntimeMobView(WorldValidatorMixin, APIView):
    permission_classes = (permissions.IsAuthenticated, CanInspectRuntime)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response

    def runtime(self, pk):
        from builders.views import _get_builder_admin_spawn_world
        return _get_builder_admin_spawn_world(self.world, pk)

    def mob(self, runtime, mob_id):
        return get_object_or_404(mob_queryset(runtime).select_related(
            'equipment', 'spawn_placement__run__plan', 'definition__merchant_profile',
            'definition__trainer_profile', 'definition__crafting_profile'), pk=mob_id)

    def get(self, request, world_pk, pk, mob_id=None):
        runtime = self.runtime(pk)
        query = MobPageQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        context = {'id': runtime.pk, 'name': runtime.name, 'lifecycle': runtime.lifecycle,
                   'root_world_id': self.world.pk, 'inspected_at': timezone.now()}
        if mob_id is not None:
            mob = self.mob(runtime, mob_id)
            return Response({'world': context, 'mob': mob_details(mob, conversation_policy(runtime),
                            query.validated_data['inventory_after'])})
        qs = mob_queryset(runtime).filter(id__gt=query.validated_data['after'])
        text = query.validated_data['q'].strip()
        if text:
            match = Q(name__icontains=text) | Q(keywords__icontains=text) | Q(definition__slug__icontains=text) | Q(room__name__icontains=text)
            if text.isdecimal() and len(text) < 19:
                match |= Q(pk=int(text))
            qs = qs.filter(match)
        mobs = list(qs.order_by('id')[:51])
        return Response({'world': context, 'results': [mob_summary(mob) for mob in mobs[:50]],
                         'next_after': mobs[49].pk if len(mobs) > 50 else None})


class RuntimeMobDebugView(RuntimeMobView):
    permission_classes = (permissions.IsAuthenticated, permissions.IsAdminUser, CanInspectRuntime)

    def get(self, request, world_pk, pk, mob_id):
        runtime = self.runtime(pk)
        mob = self.mob(runtime, mob_id)
        policy = conversation_policy(runtime)
        focus = MobConversationFocus.objects.filter(mob=mob).first()
        return Response({'mob': mob_details(mob, policy), 'policy': record(policy),
                         'world_conversation': {'knowledge': world_knowledge(mob, policy)},
                         'focus': record(focus),
                         'combat': list(CombatParticipant.objects.filter(mob=mob, is_active=True).order_by('id').values()[:20]),
                         'inspected_at': timezone.now()})

    def post(self, request, world_pk, pk, mob_id):
        """POST keeps draft speech out of URLs/access logs; this operation writes nothing."""
        data = PromptPreviewInput(data=request.data)
        data.is_valid(raise_exception=True)
        runtime = self.runtime(pk)
        mob = self.mob(runtime, mob_id)
        policy = conversation_policy(runtime)
        config = conversation_summary(mob, policy)
        if not config['configured']:
            return Response({'detail': 'This mob definition has no conversation configuration.'}, status=400)
        focus = MobConversationFocus.objects.select_related('player').filter(mob=mob,
            expires_at__gt=timezone.now(), hard_expires_at__gt=timezone.now(),
            room_id=mob.room_id, world_id=runtime.pk, mob_move_sequence=mob.follow_move_sequence,
            policy_revision=revision_for(mob, policy), player__in_game=True, player__room_id=mob.room_id,
            player__world_id=runtime.pk).first()
        control = InferenceControl.objects.filter(pk=1).first()
        if focus and (focus.player_location_sequence != focus.player.location_sequence
                      or focus.player_disconnection_ts != focus.player.last_disconnection_ts
                      or not control or focus.inference_generation != control.generation
                      or focus.world_session != CommunicationSession.objects.filter(world=runtime)
                          .values_list('generation', flat=True).first()):
            focus = None
        use_focus = bool(data.validated_data['use_active_conversation'] and focus)
        transcript = focus.transcript if use_focus else [{'role': 'assistant', 'content': config['greeting']}]
        listener = SimpleNamespace(room=mob.room)
        profile = (control.configuration.get('profiles') or {}).get('fast') if control else None
        provider = control.configuration.get('provider') if control else None
        result = {'kind': 'preview', 'submitted': False, 'stored': False, 'generated_at': timezone.now(),
                  'context': 'active_conversation' if use_focus else 'new_conversation',
                  'knowledge_packet': knowledge_packet(listener, mob, policy), 'instructions': INSTRUCTIONS,
                  'provider': provider, 'model': profile.get('model') if profile else None,
                  'world_enabled': config['world_enabled'],
                  'integration_enabled': bool(control and control.enabled and settings.LLM_API_KEY),
                  'pending_reply': bool(use_focus and focus.pending_request_id),
                  'provider_body': None, 'messages': None, 'unavailable_reason': None}
        if not profile or provider not in ('openai', 'anthropic'):
            result['unavailable_reason'] = 'Configure an inference provider and fast model to preview its request body.'
        else:
            try:
                payload = bounded_prompt(listener, mob, policy, transcript, data.validated_data['speech'], profile)
                result['messages'] = payload['messages']
                result['provider_body'] = request_body(provider, profile, payload)
            except Unavailable as error:
                result['unavailable_reason'] = str(error)
        return Response(result)
