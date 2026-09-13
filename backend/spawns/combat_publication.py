"""Canonical combat output with recipient projection at publication time."""
from copy import deepcopy
from dataclasses import replace

from spawns.events import GameEvent
from spawns.models import Player


def room_recipients(context, encounter):
    key = (encounter.world_id, encounter.room_id)
    cache = getattr(context, '_room_recipients', {})
    if key not in cache:
        cache[key] = [f'player.{pk}' for pk in Player.objects.filter(
            world_id=key[0], room_id=key[1], in_game=True,
        ).order_by('pk').values_list('pk', flat=True)]
        context._room_recipients = cache
    return cache[key]


def engagement_events(context, encounter, attacker, target):
    """Announce a new attack to its target and visible room observers once."""
    from spawns.actions.combat import _engage_events, stand_player
    from spawns.state_payloads import safe_capitalize

    events = []
    if isinstance(target, Player):
        stand_player(target)
        events = _engage_events(player=target, room=target.room, mob=attacker)
        events[0] = replace(events[0], text=f'{safe_capitalize(attacker.name)} attacks you!')
    recipients = [key for key in room_recipients(context, encounter) if key != target.key]
    if recipients and not attacker.is_invisible and not target.is_invisible:
        events.append(GameEvent('notification.combat.engage', {
            'encounter_id': encounter.pk,
            'actor': {'key': attacker.key, 'name': attacker.name},
            'target': {'key': target.key, 'name': target.name},
        }, recipients, f'{safe_capitalize(attacker.name)} attacks {target.name}!'))
    return events


def snapshot_event(context, encounter):
    from spawns.actions.effects import active_combatant_effects
    members = context.members(encounter)
    effects = active_combatant_effects([context.actors[p.actor_key] for p in members])
    by_id = {p.pk: p for p in members}
    participants = []
    for member in members:
        actor = context.actors.get(member.actor_key)
        if actor is None:
            continue
        if isinstance(actor, Player) and not hasattr(actor, 'health_max'):
            from core.computations import compute_stats
            stats = compute_stats(actor.level, actor.archetype, char=actor, world=actor.world)
            actor.health_max = stats.get('health_max', 1)
            actor.energy_max = stats.get('energy_max', 1)
        participants.append({
            'key': member.actor_key, 'side': member.side_id,
            'name': actor.name, 'level': actor.level, 'health': actor.health, 'health_max': actor.health_max,
            'energy': actor.energy, 'energy_max': actor.energy_max,
            'status': 'active', '_concealed': actor.is_invisible,
            'current_target': by_id[member.current_target_id].actor_key
            if member.current_target_id in by_id else None,
            'effects': effects.get(member.actor_key, []),
        })
    recipients = set(room_recipients(context, encounter))
    recipients.update(p.actor_key for p in context.participants
                      if p.encounter_id == encounter.pk and p.player_id)
    return GameEvent('notification.combat.snapshot', {
        'encounter_id': encounter.pk, 'state_revision': encounter.state_revision,
        'world_id': encounter.world_id, 'room_id': encounter.room_id,
        'round': encounter.round_number, 'status': encounter.status,
        'participants': participants, 'merged_into': encounter.merged_into_id,
    }, sorted(recipients))


def project_snapshot(data, viewer):
    result = deepcopy(data)
    all_rows = result.get('participants', [])
    own = next((p for p in all_rows if p['key'] == viewer), None)
    visible = [p for p in all_rows if p['key'] == viewer or not p.get('_concealed')]
    visible_keys = {p['key'] for p in visible}
    for row in visible:
        row.pop('_concealed', None)
        row['relation'] = ('self' if row['key'] == viewer else 'neutral' if own is None else
                           'ally' if row['side'] == own['side'] else 'enemy')
        if row.get('current_target') not in visible_keys:
            row['current_target'] = None
        for effect in row.get('effects', []):
            source = effect.get('source') or {}
            if f'{source.get("type")}.{source.get("id")}' not in visible_keys:
                effect['source'] = {}
            effect.pop('primitives', None)
    result['participants'] = visible
    result['self'] = viewer if own else None
    return result


def project_message(message, viewer):
    if message.get('type') == 'notification.combat.snapshot':
        return {**message, 'data': project_snapshot(message['data'], viewer)}
    awards = message.get('data', {}).get('_combat_awards')
    if awards is not None:
        data = {k: v for k, v in message['data'].items() if k != '_combat_awards'}
        return {**message, 'data': {**data, **awards.get(viewer, {})}}
    narration = message.get('data', {}).get('_combat_narration')
    if narration:
        data = {k: v for k, v in message['data'].items() if k != '_combat_narration'}
        return {**message, 'data': data, 'text': narration.get(viewer, message.get('text', ''))}
    return message


def snapshot_for_player(player):
    from spawns.combat_encounters import transact

    def read(ctx):
        member = ctx.participant(player.key)
        if member is None:
            return None
        encounter = ctx.encounters[member.encounter_id]
        if (player.world_id, player.room_id) != (encounter.world_id, encounter.room_id):
            return None
        return project_snapshot(snapshot_event(ctx, encounter).data, player.key)
    return transact(read, keys=[player.key])
