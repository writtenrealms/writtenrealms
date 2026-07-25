"""Shared queryset annotations for runtime item payloads."""

from django.db.models import Exists, OuterRef

from builders.models import ItemSalvageYield


def with_item_salvageability(queryset):
    """Annotate items with intrinsic salvageability in the existing query."""
    salvage_yield = ItemSalvageYield.objects.filter(
        item_definition_id=OuterRef("definition_id"),
    )
    return queryset.annotate(
        _payload_is_salvageable=Exists(salvage_yield),
    )
