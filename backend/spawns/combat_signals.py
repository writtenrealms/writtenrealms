"""Mark room admission dirty when its inputs change, never on ordinary damage."""
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from builders.models import FactionAssignment, FactionRelationship, MobDefinition
from spawns.models import ActiveEffect, CharacterState, Mob, MobState, Player


POLICY_FIELDS = {'world_id', 'room_id', 'aggression', 'fights_back', 'attackable',
                 'is_invisible', 'is_pending_deletion', 'in_game', 'group_id', 'definition_id'}


def request_reconciliation(*args, **kwargs):
    from spawns.combat_reconciliation import request_reconciliation as request
    return request(*args, **kwargs)


@receiver(pre_save, sender=Player)
@receiver(pre_save, sender=Mob)
def capture_admission_inputs(sender, instance, raw=False, update_fields=None, **kwargs):
    instance._combat_previous_location = None
    instance._combat_admission_changed = False
    if raw:
        return
    fields = {f.attname for f in sender._meta.concrete_fields} & POLICY_FIELDS
    requested = {f + '_id' if f in {'room', 'world', 'definition'} else f for f in update_fields or fields}
    if not fields & requested:
        return
    previous = sender.objects.filter(pk=instance.pk).values(*fields).first() if instance.pk else None
    instance._combat_admission_changed = previous is None or any(
        previous[name] != getattr(instance, name) for name in fields & requested
    )
    if previous and (previous['world_id'], previous['room_id']) != (instance.world_id, instance.room_id):
        instance._combat_previous_location = (previous['world_id'], previous['room_id'])


@receiver(post_save, sender=Player)
@receiver(post_save, sender=Mob)
def actor_admission_changed(sender, instance, raw=False, **kwargs):
    if raw or not getattr(instance, '_combat_admission_changed', False):
        return
    previous = getattr(instance, '_combat_previous_location', None)
    from spawns.combat_encounters import current_context
    ctx = current_context()
    if ctx and instance.key in ctx.actors and previous:
        from spawns.combat_rounds import detach_actor
        ctx.actors[instance.key] = instance
        fled = getattr(ctx, 'fleeing_actor', None) == instance.key
        detach_actor(ctx, instance.key, reason='fled' if fled else 'moved', refund=not fled)
    if previous:
        request_reconciliation(*previous, [instance.key])
    request_reconciliation(instance.world_id, instance.room_id, [instance.key],
                           observed=isinstance(instance, Player) and instance.in_game)


@receiver(post_save, sender=MobState)
@receiver(post_save, sender=CharacterState)
def state_changed(sender, instance, raw=False, **kwargs):
    if raw:
        return
    actor = instance.mob if sender is MobState else instance.player
    request_reconciliation(actor.world_id, actor.room_id, [actor.key],
                           observed=isinstance(actor, Player) and actor.in_game)


@receiver(post_save, sender=FactionAssignment)
@receiver(post_delete, sender=FactionAssignment)
def faction_changed(sender, instance, raw=False, **kwargs):
    if raw or not instance.member_type_id or instance.member_type.model not in {'mob', 'player'}:
        return
    actor = instance.member
    if isinstance(actor, (Player, Mob)):
        request_reconciliation(actor.world_id, actor.room_id, [actor.key])


@receiver(post_save, sender=ActiveEffect)
@receiver(post_delete, sender=ActiveEffect)
def effect_changed(sender, instance, signal=None, **kwargs):
    from spawns.combat_encounters import current_context
    ctx = current_context()
    if ctx is not None:
        ctx.stats_cache = {}
    if ctx is not None and hasattr(ctx, 'effect_rows'):
        if signal is post_delete:
            ctx.effect_rows.pop(instance.pk, None)
        else:
            ctx.effect_rows[instance.pk] = instance
            from spawns.combat_encounters import MAX_EFFECTS
            if len(ctx.effect_rows) > MAX_EFFECTS:
                from spawns.actions.base import ActionError
                raise ActionError('That fight has reached its effect limit.', code='combat_capacity')


@receiver(post_save, sender=Player)
@receiver(post_save, sender=Mob)
def actor_stats_changed(sender, instance, update_fields=None, **kwargs):
    if update_fields is not None and not set(update_fields) & {'level', 'archetype', 'equipment', 'trait_instances'}:
        return
    from spawns.combat_encounters import current_context
    ctx = current_context()
    if ctx is not None:
        ctx.stats_cache = {}


@receiver(post_save, sender=MobDefinition)
@receiver(post_save, sender=FactionRelationship)
@receiver(post_delete, sender=FactionRelationship)
def authored_policy_changed(sender, instance, raw=False, created=False, **kwargs):
    if raw or created and sender is MobDefinition:
        return
    from django.db.models import F, Q
    from django.utils import timezone
    from spawns.models import CombatRoomState
    if sender is MobDefinition:
        mobs = Mob.objects.filter(definition_id=instance.pk, is_pending_deletion=False)
        rooms = CombatRoomState.objects.filter(world_id__in=mobs.values('world_id'),
                                               room_id__in=mobs.values('room_id'))
    else:
        world_id = instance.faction.world_id
        rooms = CombatRoomState.objects.filter(Q(world_id=world_id) | Q(world__context_id=world_id)
            | Q(world__context__instance_of_id=world_id))
    # One durable indexed update; heartbeat recovery dispatches bounded pages.
    # Do not enqueue a job for each mob when a builder edits shared policy.
    rooms.update(dirty_generation=F('dirty_generation') + 1, changed_actors=['*'],
                 next_run_ts=timezone.now())
