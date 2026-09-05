"""Selector resolution over the admitted, bounded participant set."""
from spawns.combat_encounters import eligible
from spawns.models import Player


def targets(context, encounter, actor, selector, selected=None):
    member = context.participant(actor.key)
    if member is None:
        return []
    rows = context.members(encounter)
    candidates = []
    for row in rows:
        target = context.actors.get(row.actor_key)
        if not eligible(target) or (target is not actor and target.is_invisible):
            continue
        if (target.world_id, target.room_id) != (encounter.world_id, encounter.room_id):
            continue
        allied = row.side_id == member.side_id
        if selector in {'self', 'actor', 'effect.source'}:
            include = target.key == actor.key
        elif selector == 'room.allies':
            include = allied
        elif selector == 'room.players':
            include = isinstance(target, Player)
        elif selector in {'room.hostiles', 'room.secondary_hostile'}:
            include = not allied and (selector != 'room.secondary_hostile' or target is not selected)
        else:
            include = selected is not None and target.key == selected.key
        if include:
            candidates.append(target)
    candidates.sort(key=lambda a: (-int(getattr(a, 'target_priority', 0)), a.key))
    return candidates[:1] if selector == 'room.secondary_hostile' else candidates


def apply_group_effect(context, encounter, actor, component, ability, viewer, round_id):
    from spawns.actions import combat
    events = []
    for target in targets(context, encounter, actor, component['target']):
        if combat.component_targets_character_effect(component, ability=ability):
            events.extend(combat._apply_character_scoped_effect(
                encounter=encounter, component=component, source=actor, target=target,
                viewer=viewer, room=encounter.room, ability=ability, round_id=round_id,
            ))
        else:
            duration = int((component.get('duration') or {}).get('rounds') or 1)
            label = combat._component_label(component, ability)
            combat._append_effect(encounter, effect=component['effect'], source=actor, target=target,
                                   duration_rounds=duration, label=label,
                                   category=component.get('category') or 'neutral',
                                   primitives=component.get('primitives') or [], tick=component.get('tick') or {})
            events.extend(combat._combat_effect_application_events(
                viewer=viewer, room=encounter.room, actor=actor, target=target, ability=ability,
                effect=component['effect'], label=label, duration_rounds=duration, round_id=round_id,
            ))
    return events
