"""Invalidate active focuses on lifecycle changes, not on each game tick."""
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from spawns.models import CombatParticipant, Mob, MobConversationFocus, Player


def invalidate(actor, *, combat=False):
    if not settings.LLM_API_KEY:
        return
    query = MobConversationFocus.objects.filter(mob__isnull=False)
    query = query.filter(player_id=actor.pk) if isinstance(actor, Player) else query.filter(mob_id=actor.pk)
    focus = query.first()
    if focus is None:
        return
    changed = combat or actor.health <= 0 or (focus.world_id, focus.room_id) != (actor.world_id, actor.room_id)
    if isinstance(actor, Player):
        changed = changed or not actor.in_game or focus.player_location_sequence != actor.location_sequence
    else:
        changed = changed or actor.is_pending_deletion or focus.mob_move_sequence != actor.follow_move_sequence
    if not changed:
        return
    from spawns.conversations.service import _close
    with transaction.atomic():
        # Nonblocking because a mob move/combat join may already own its row.
        # A concurrent command still verifies all snapshots before admission.
        player = Player.objects.select_for_update(skip_locked=True).filter(pk=focus.player_id).only('id').first()
        if player:
            current = MobConversationFocus.objects.select_for_update().filter(
                player_id=player.pk, generation=focus.generation).first()
            _close(current)


@receiver(post_save, sender=Player)
@receiver(post_save, sender=Mob)
def actor_changed(sender, instance, update_fields=None, raw=False, created=False, **kwargs):
    if raw or created or not settings.LLM_API_KEY:
        return
    relevant = {'room', 'room_id', 'world', 'world_id', 'in_game', 'location_sequence',
                'follow_move_sequence', 'is_pending_deletion'}
    if update_fields is not None and not relevant.intersection(update_fields) and instance.health > 0:
        return
    invalidate(instance)


@receiver(post_save, sender=CombatParticipant)
def entered_combat(sender, instance, raw=False, **kwargs):
    if not raw and instance.is_active and settings.LLM_API_KEY:
        actor = instance.player if instance.player_id else instance.mob
        if actor:
            invalidate(actor, combat=True)
