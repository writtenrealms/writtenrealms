"""Read-only, paginated completion history for builder player details."""

from rest_framework import serializers
from rest_framework.pagination import CursorPagination

from worlds.models import InstanceClearRecord


class PlayerCompletionPagination(CursorPagination):
    page_size = 20
    ordering = ('-completed_at', '-id')


class PlayerCompletionSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstanceClearRecord
        fields = (
            'id', 'template_name', 'template_slug', 'started_at',
            'completed_at', 'clear_time_ms', 'time_control',
        )
        read_only_fields = fields


def player_completion_history(request, player):
    # Query the indexed historical snapshot, which survives run cleanup and
    # records each reset attempt separately. Do not join live participants.
    records = InstanceClearRecord.objects.filter(
        participants__contains=[{'player_id': player.pk}],
    ).only(*PlayerCompletionSerializer.Meta.fields)
    pagination = PlayerCompletionPagination()
    page = pagination.paginate_queryset(records, request)
    return pagination.get_paginated_response(PlayerCompletionSerializer(page, many=True).data)
