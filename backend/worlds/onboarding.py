"""Durable onboarding obligations and reward receipts; never heartbeat work."""
from django.db import transaction

from core.player_creation import validate_initial_instance_destination
from worlds.models import OnboardingRewardGrant, PlayerOnboarding, World


def prepare_onboarding_login(player):
    # Retained instances resume normally, including their original room/quests.
    if player.world.context.instance_of_id:
        return player
    onboarding = PlayerOnboarding.objects.filter(
        player_id=player.pk, left_at__isnull=True,
    ).first()
    if onboarding is None:
        return player

    with transaction.atomic():
        # Match instance creation's World -> Player lock order. A second login
        # rechecks the player after waiting and reuses the first reservation.
        template = (
            World.objects.select_for_update(of=('self',))
            .filter(pk=onboarding.template_id)
            .select_related('config', 'config__starting_room').first()
        )
        player = (
            type(player).objects.select_for_update(of=('self',))
            .select_related('world__context').get(pk=player.pk)
        )
        onboarding.refresh_from_db()
        if onboarding.left_at is not None or player.world.context.instance_of_id:
            return player
        if player.in_game:
            raise ValueError('Log out of this character before returning to the introduction.')
        if template is None or template.instance_of_id != player.world.context_id:
            raise ValueError('This character\'s introduction is unavailable. Contact the world builder.')
        validate_initial_instance_destination(template, slug=template.instance_slug)
        if template.no_start or (template.maintenance_mode and not player.is_builder):
            raise ValueError('This character\'s introduction is temporarily closed.')
        from worlds.instances import _reserve_initial_instance

        _reserve_initial_instance(player, template, returning=True)
    return player


def claim_onboarding_rewards(*, player, quest):
    """Called inside the quest resolution's transaction and Player row lock.

    Claim and rewards commit together. Normal quests have no receipt; onboarding
    quests keep one per outcome even after leaving or deleting a run/template.
    """
    if not quest.template.world.instance_of_id:
        return True
    onboarding = PlayerOnboarding.objects.filter(
        player_id=player.pk, template_id=quest.template.world_id,
    ).first()
    if onboarding is None:
        return True
    _, created = OnboardingRewardGrant.objects.get_or_create(
        onboarding=onboarding, quest_slug=quest.template.slug,
        resolution=quest.resolution or 'complete',
    )
    from system.support_events import record_quest_event

    record_quest_event(
        'onboarding.rewards_granted' if created else 'onboarding.rewards_skipped',
        player=player, quest=quest, reason=quest.resolution or 'complete',
    )
    return created
