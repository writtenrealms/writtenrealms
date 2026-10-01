"""Character-wide base quests and run-local instance quests."""

from django.db.models import Q

from quests.models import QuestInstance, QuestOfferState


def instances_for_player(player):
    return QuestInstance.objects.filter(player=player).filter(
        Q(template__world__instance_of__isnull=True) | Q(world_id=player.world_id)
    )


def offers_for_player(player):
    return QuestOfferState.objects.filter(player=player).filter(
        Q(world_id=player.world_id)
        | Q(world__isnull=True, template__world__instance_of__isnull=True)
    )


def offer_lookup(player, template):
    return {
        "player": player,
        "template": template,
        "world_id": player.world_id if template.world.instance_of_id else None,
    }
