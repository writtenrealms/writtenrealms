"""Bounded, idempotent per-mob reward decisions for participant combat."""
from spawns.models import CombatRewardReceipt, Player, Item
from spawns.events import GameEvent


def _shares(total, players):
    quotient, remainder = divmod(max(0, int(total)), len(players)) if players else (0, 0)
    return {p.pk: quotient + int(index < remainder) for index, p in enumerate(players)}


def defeat_mob(context, encounter, participant, mob, killer):
    from spawns.actions import combat
    from spawns.combat_publication import room_recipients
    from spawns.merchants import deactivate_merchant_runtime

    mob_id = mob.pk
    if CombatRewardReceipt.objects.filter(mob_runtime_id=mob_id).exists():
        return []
    snapshot = combat._serialize_combat_char(mob)
    eligible = [p for p in context.members(encounter)
                if p.player_id and p.side_id != participant.side_id and
                int(p.contributions.get(str(mob_id), 0)) > 0]
    players = sorted([context.actors[p.actor_key] for p in eligible
                      if context.actors[p.actor_key].health > 0
                      and (context.actors[p.actor_key].world_id, context.actors[p.actor_key].room_id)
                      == (encounter.world_id, encounter.room_id)], key=lambda p: p.pk)
    currency_snapshot = dict(mob.currency_reward_snapshot or {})
    currencies = list(combat.Currency.objects.filter(
        world=combat.economy_world(encounter.world), code__in=currency_snapshot,
    )) if players and currency_snapshot else []
    experience = _shares(mob.exp_worth, players)
    currency_shares = {c.pk: _shares(currency_snapshot[c.code], players) for c in currencies}
    receipt = CombatRewardReceipt.objects.create(
        encounter=encounter, mob_runtime_id=mob_id, mob_snapshot=participant.actor_snapshot,
    )
    corpse = None
    if players:
        corpse_id = combat._ensure_corpse(mob)
        corpse = Item.objects.get(pk=corpse_id)
        combat.roll_mob_loot(mob=mob, corpse=corpse, killer=players[0], room=encounter.room)
    deactivate_merchant_runtime(mob)
    mob.delete()
    rewards = []
    projections = {}
    events = []
    for player in players:
        amount = experience[player.pk]
        leveling = combat.apply_experience(player, amount) if amount else None
        if amount:
            player.save(update_fields=['experience', 'level'])
        deltas = {c.pk: currency_shares[c.pk][player.pk] for c in currencies
                  if currency_shares[c.pk][player.pk]}
        if deltas:
            combat.mutate_balances(player, deltas, reason='mob.kill')
        money = [combat.money_payload(deltas[c.pk], c) for c in currencies if c.pk in deltas]
        actor_payload = combat.serialize_actor(player, encounter.room).model_dump()
        award = {'player_id': player.pk, 'experience': amount, 'currency': deltas}
        rewards.append(award)
        data = {'actor': actor_payload, 'source': snapshot, 'experience_gained': amount,
                'currency_rewards': money}
        if leveling:
            data.update(previous_level=leveling.previous_level, new_level=leveling.new_level,
                        levels_gained=leveling.levels_gained,
                        experience_progress=leveling.experience_progress,
                        experience_needed=leveling.experience_needed, max_level=leveling.max_level)
        text = combat._reward_text(experience_gained=amount, currency_rewards=money, leveling=leveling)
        room_payload = combat._room_payload(player, encounter.room)
        projections[player.key] = {**data, 'room': room_payload}
        if text:
            events.append(GameEvent('notification.reward', data, [player.key], text))
        events.append(GameEvent('quest.mob.killed', {
            **data, 'target': snapshot, 'room': room_payload,
            'levels_gained': leveling.levels_gained if leveling else 0,
        }))
    receipt.awards = rewards
    receipt.save(update_fields=['awards'])
    # A long-running encounter may see many replacement mobs. Retain only
    # contribution counters for living reward subjects; the receipt is final.
    for member in context.members(encounter):
        if str(mob_id) in member.contributions:
            member.contributions = {k: v for k, v in member.contributions.items() if k != str(mob_id)}
            member.save(update_fields=['contributions'])
    death = {'deceased': snapshot, '_combat_awards': projections, 'killer': combat._death_killer_payload(killer),
             'corpse': combat._serialize_corpse(corpse.pk) if corpse else combat._empty_corpse_payload()}
    events.insert(0, GameEvent('notification.death', death, room_recipients(context, encounter),
                               combat._mob_death_text(snapshot.get('name'))))
    return events
