"""Transaction-local effect reads over the already locked combat actors."""
from django.db.models import Q

from spawns.combat_encounters import current_context, MAX_EFFECTS
from spawns.models import ActiveEffect


def cached_effects():
    ctx = current_context()
    if ctx is None:
        return None
    if not hasattr(ctx, 'effect_rows'):
        players = [a.pk for key, a in ctx.actors.items() if key.startswith('player.')]
        mobs = [a.pk for key, a in ctx.actors.items() if key.startswith('mob.')]
        rows = list(ActiveEffect.objects.select_for_update(of=('self',)).filter(
            Q(target_player_id__in=players) | Q(target_mob_id__in=mobs), remaining_rounds__gt=0,
        ).order_by('pk')[:MAX_EFFECTS + 1])
        if len(rows) > MAX_EFFECTS:
            from spawns.actions.base import ActionError
            raise ActionError('That fight has reached its effect limit.', code='combat_capacity')
        ctx.effect_rows = {e.pk: e for e in rows}
    rows = []
    for effect in ctx.effect_rows.values():
        if effect.remaining_rounds <= 0:
            continue
        key = f'player.{effect.target_player_id}' if effect.target_player_id else f'mob.{effect.target_mob_id}'
        actor = ctx.actors.get(key)
        member = ctx.participant(key)
        if actor is None or effect.world_id != actor.world_id:
            continue
        if effect.scope == ActiveEffect.SCOPE_ENCOUNTER and (
            not member or member.encounter_id != effect.encounter_id
            or ctx.encounters[member.encounter_id].room_id != actor.room_id
        ):
            continue
        rows.append(effect)
    return sorted(rows, key=lambda e: (e.created_ts, e.pk))


def actor_effects(actor, *, scope=None):
    ctx = current_context()
    if ctx is None or actor.key not in ctx.actors:
        return None
    rows = cached_effects()
    if rows is None:
        return None
    return [e for e in rows if
            (f'player.{e.target_player_id}' if e.target_player_id else f'mob.{e.target_mob_id}') == actor.key
            and (scope is None or e.scope == scope)]
