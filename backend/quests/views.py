from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView

from builders import world_export as builder_world_export
from builders.views import BaseWorldBuilderView
from core.permissions import IsPlayerInGame
from quests import manifests as quest_manifests
from quests import serializers as quest_serializers
from quests.services.discovery import list_opportunities
from quests.services.engine import (
    QuestRuntimeError,
    abandon_instance,
    accept_template,
    choose_for_instance,
    info_for_player,
    list_active_instances,
    list_resolved_instances,
    resolve_template_for_player,
)
from quests.services.quest_log import build_quest_log
from quests.models import QuestArcTemplate, QuestTemplate
from spawns.events import publish_events


def _apply_query(qs, query):
    query = str(query or "").strip()
    if not query:
        return qs
    if query.isdigit():
        return qs.filter(pk=int(query))
    return qs.filter(Q(name__icontains=query) | Q(slug__icontains=query))


def _quest_manifest_serializer_context(world):
    entity_ref_cache, room_ref_cache = (
        builder_world_export.build_manifest_semantic_ref_caches(world)
    )
    return {
        'entity_ref_cache': entity_ref_cache,
        'room_ref_cache': room_ref_cache,
    }


class QuestTemplateListView(BaseWorldBuilderView):
    def get(self, request, world_pk, format=None):
        qs = (
            QuestTemplate.objects.filter(world=self.world)
            .select_related('arc', 'world')
            .order_by('name', 'created_ts')
        )
        qs = _apply_query(qs, request.query_params.get('query'))
        serializer = quest_serializers.QuestTemplateSerializer(
            qs,
            many=True,
            context=_quest_manifest_serializer_context(self.world),
        )
        return Response(
            {
                'new_quest_template': quest_manifests.serialize_quest_template_template(
                    world=self.world,
                ),
                'quests': serializer.data,
            }
        )


class QuestTemplateDetailView(BaseWorldBuilderView):
    def get(self, request, world_pk, pk, format=None):
        qs = QuestTemplate.objects.filter(world=self.world).select_related(
            'arc',
            'world',
        )
        if str(pk).isdigit():
            quest = get_object_or_404(qs, pk=int(pk))
        else:
            quest = get_object_or_404(qs, slug=pk)
        serializer = quest_serializers.QuestTemplateSerializer(
            quest,
            context=_quest_manifest_serializer_context(self.world),
        )
        return Response(serializer.data)


class QuestArcTemplateListView(BaseWorldBuilderView):
    def get(self, request, world_pk, format=None):
        qs = QuestArcTemplate.objects.filter(world=self.world).order_by('name', 'created_ts')
        qs = _apply_query(qs, request.query_params.get('query'))
        serializer = quest_serializers.QuestArcTemplateSerializer(qs, many=True)
        return Response(
            {
                'new_quest_arc_template': quest_manifests.serialize_quest_arc_template(
                    world=self.world,
                ),
                'quest_arcs': serializer.data,
            }
        )


class QuestArcTemplateDetailView(BaseWorldBuilderView):
    def get(self, request, world_pk, pk, format=None):
        qs = QuestArcTemplate.objects.filter(world=self.world)
        if str(pk).isdigit():
            quest_arc = get_object_or_404(qs, pk=int(pk))
        else:
            quest_arc = get_object_or_404(qs, slug=pk)
        serializer = quest_serializers.QuestArcTemplateSerializer(quest_arc)
        return Response(serializer.data)


quest_template_list = QuestTemplateListView.as_view()
quest_template_detail = QuestTemplateDetailView.as_view()
quest_arc_template_list = QuestArcTemplateListView.as_view()
quest_arc_template_detail = QuestArcTemplateDetailView.as_view()


class QuestRuntimeView(APIView):
    permission_classes = (IsAuthenticated, IsPlayerInGame)

    def handle_exception(self, exc):
        if isinstance(exc, QuestRuntimeError):
            return Response(
                {
                    "detail": exc.message,
                    "code": exc.code,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().handle_exception(exc)


class QuestOpportunityAcceptView(QuestRuntimeView):
    def post(self, request, slug, format=None):
        opportunities = {op["slug"]: op for op in list_opportunities(request.player, refresh=True)}
        if slug not in opportunities:
            raise QuestRuntimeError("Quest opportunity was not found.", code="opportunity_not_found")
        template = resolve_template_for_player(request.player, slug)
        result = accept_template(request.player, template)
        publish_events(result.events, actor_key=request.player.key)
        payload, info_text = info_for_player(request.player, str(result.quest_instance.id))
        return Response(
            {
                "quest": payload,
                "text": info_text,
            },
            status=status.HTTP_201_CREATED,
        )


class QuestActiveListView(QuestRuntimeView):
    def get(self, request, format=None):
        return Response({"quests": list_active_instances(request.player)})


class QuestResolvedListView(QuestRuntimeView):
    def get(self, request, format=None):
        return Response({"quests": list_resolved_instances(request.player)})


class QuestLogView(QuestRuntimeView):
    def get(self, request, format=None):
        return Response(build_quest_log(request.player))


class QuestInstanceInfoView(QuestRuntimeView):
    def get(self, request, instance_id, format=None):
        payload, info_text = info_for_player(request.player, str(instance_id))
        return Response({"quest": payload, "text": info_text})


class QuestInstanceAbandonView(QuestRuntimeView):
    def post(self, request, instance_id, format=None):
        result = abandon_instance(request.player, str(instance_id))
        publish_events(result.events, actor_key=request.player.key)
        payload, info_text = info_for_player(request.player, str(result.quest_instance.id))
        return Response({"quest": payload, "text": info_text})


class QuestInstanceChooseView(QuestRuntimeView):
    def post(self, request, instance_id, format=None):
        serializer = quest_serializers.QuestInstanceChoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = choose_for_instance(
            request.player,
            str(instance_id),
            serializer.validated_data["choice_id"],
        )
        publish_events(result.events, actor_key=request.player.key)
        payload, info_text = info_for_player(request.player, str(result.quest_instance.id))
        return Response({"quest": payload, "text": info_text})


quest_opportunity_accept = QuestOpportunityAcceptView.as_view()
quest_active_list = QuestActiveListView.as_view()
quest_resolved_list = QuestResolvedListView.as_view()
quest_log = QuestLogView.as_view()
quest_instance_info = QuestInstanceInfoView.as_view()
quest_instance_abandon = QuestInstanceAbandonView.as_view()
quest_instance_choose = QuestInstanceChooseView.as_view()
