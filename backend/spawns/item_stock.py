"""Destination-scoped spawn stock and short transaction locks.

Transfers take their existing actor/item locks before the destination lock.
Reconciliation never waits for destination or parent locks: busy work is
retried on the next poll without advancing the plan's respawn deadline.
"""
import hashlib
from collections import defaultdict, deque

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.models import CharField, Count, F, Min, Q, Subquery, Window
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Coalesce, RowNumber


def destination_key(target):
    return ContentType.objects.get_for_model(target).pk, target.pk


def lock_destinations(world_id, targets, *, wait=True):
    from spawns.models import Item, Mob
    from worlds.models import Room, World

    keys = sorted({int.from_bytes(hashlib.blake2b(
        f'item-stock:{world_id}:{destination_key(target)}'.encode(), digest_size=8,
    ).digest(), 'big', signed=True) for target in targets if isinstance(target, (Room, Item, Mob))})
    if not keys:
        return True
    if connection.vendor != 'postgresql':
        return World.objects.select_for_update(skip_locked=not wait).filter(pk=world_id).exists()
    function = 'pg_advisory_xact_lock' if wait else 'pg_try_advisory_xact_lock'
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {function}(key) FROM unnest(%s::bigint[]) AS locks(key) ORDER BY key', [keys])
        results = cursor.fetchall()
    return wait or all(row[0] for row in results)


class ItemStock:
    """One batched count per entry, independent of its room/slot count.

    Item definitions produce one stock unit. A bundle roll is one unit while
    any of that roll remains at the destination, preserving bundle semantics.
    Player inventories and arbitrary nested bags never enter this query.
    """
    def __init__(self, *, world_id, run_id, entry_slug, targets):
        from spawns.models import Item

        self.world_id = world_id
        containers = defaultdict(set)
        for target in targets:
            ct, pk = destination_key(target)
            containers[ct].add(pk)
        locations = Q(pk__in=[])
        for ct, ids in containers.items():
            locations |= Q(container_type_id=ct, container_id__in=ids)
        self.query = Item.objects.filter(locations, world_id=world_id,
            spawn_placement__run_id=run_id, spawn_placement__entry_slug=entry_slug,
            spawn_placement__source_type__startswith='item',
            is_pending_deletion=False, upkeep_retiring=False).annotate(stock_unit=Coalesce(
                KT('roll_metadata__spawn_plan__stock_unit'),
                KT('roll_metadata__source_bundle_roll_id'), Cast('pk', CharField()),
                output_field=CharField(),
            ))
        self.counts = defaultdict(dict)
        for row in self.query.order_by().values('container_type_id', 'container_id', 'spawn_placement_id').annotate(
                units=Count('stock_unit', distinct=True)):
            self.counts[(row['container_type_id'], row['container_id'])][row['spawn_placement_id']] = row['units']

    def parents(self, *, limits):
        """Return at most the required number of representatives per target.

        Keep surplus stock in SQL. Lock only these potential parents, skipping
        a player/cleanup transaction already changing one. Ancestor targets
        were claimed by the earlier parent entry in this reconciliation.
        """
        from spawns.models import Item

        representatives = self.query.order_by().values('container_type_id', 'container_id', 'stock_unit').annotate(
            first_id=Min('pk')).values('first_id')
        within_quota = Q(pk__in=[])
        for (ct, pk), limit in limits.items():
            within_quota |= Q(container_type_id=ct, container_id=pk, stock_position__lte=limit)
        candidates = list(Item.objects.filter(pk__in=Subquery(representatives)).annotate(
            stock_position=Window(RowNumber(), partition_by=[F('container_type_id'), F('container_id')],
                                  order_by=F('pk').asc()),
        ).filter(within_quota))
        locked = {item.pk: item for item in Item.objects.select_for_update(skip_locked=True).filter(
            pk__in=[item.pk for item in candidates], world_id=self.world_id,
            is_pending_deletion=False, upkeep_retiring=False,
        ).order_by('pk')}
        available = defaultdict(list)
        busy = False
        for before in candidates:
            current = locked.get(before.pk)
            if current is None or destination_key_from_item(current) != destination_key_from_item(before):
                busy = True
                continue
            available[destination_key_from_item(current)].append(current)
        return available, busy


def destination_key_from_item(item):
    return item.container_type_id, item.container_id


def assign_parents(placements, candidates):
    """Prefer original slots, then let returned surplus satisfy empty slots."""
    assigned, used = {}, set()
    originals = defaultdict(deque)
    for item in candidates:
        originals[item.spawn_placement_id].append(item)
    for placement in placements:
        if originals[placement.pk]:
            candidate = originals[placement.pk].popleft()
            assigned[placement.pk] = candidate
            used.add(candidate.pk)
    remaining = iter(item for item in candidates if item.pk not in used)
    for placement in placements:
        if placement.pk not in assigned:
            candidate = next(remaining, None)
            if candidate is not None:
                assigned[placement.pk] = candidate
    return assigned


def reconcile_item_entry(*, world, run, entry, placements, eligible_ids, parents, has_children, context):
    from spawns.actions.base import ActionError
    from spawns.models import Mob
    from spawns.spawn_plans import _materialize_placement

    groups, targets = defaultdict(list), {}
    for placement in placements:
        target = (parents.get((placement.parent_entry_slug, placement.parent_slot_index))
                  if placement.parent_entry_slug else placement.room)
        if target is not None:
            key = destination_key(target)
            groups[key].append(placement)
            targets[key] = target
    if not targets:
        return 0, False
    if not lock_destinations(world.pk, targets.values(), wait=False):
        return 0, True
    stock = ItemStock(world_id=world.pk, run_id=run.pk, entry_slug=entry.slug, targets=targets.values())
    # A source edit from mob to item still waits for its old live mob.
    old_mobs = list(Mob.objects.filter(world=world, is_pending_deletion=False,
                                      spawn_placement_id__in=[p.pk for p in placements]))
    mobs_by_slot = {mob.spawn_placement_id: mob for mob in old_mobs}
    candidates, busy = stock.parents(limits={key: len(slots) for key, slots in groups.items()}) if has_children else ({}, False)
    if has_children and old_mobs:
        locked_mobs = {mob.pk for mob in Mob.objects.select_for_update(skip_locked=True).filter(
            pk__in=[mob.pk for mob in old_mobs], is_pending_deletion=False)}
        busy |= len(locked_mobs) != len(old_mobs)
    else:
        locked_mobs = set()
    if busy:
        return 0, True
    spawned_count = 0
    for key, slots in groups.items():
        counts = stock.counts[key]
        occupied = {slot.pk for slot in slots if slot.pk in mobs_by_slot}
        # Match existing originals first, then count returned surplus against
        # the remaining quota even when all copies came from the same slot.
        item_slots = {slot.pk for slot in slots if slot.pk not in occupied and counts.get(slot.pk, 0)}
        occupied |= item_slots
        surplus = max(0, sum(counts.values()) - len(item_slots))
        for slot in slots:
            if slot.pk not in occupied and surplus:
                occupied.add(slot.pk)
                surplus -= 1
        if has_children:
            assigned = assign_parents([p for p in slots if p.pk not in mobs_by_slot], candidates.get(key, []))
            for slot in slots:
                parent = assigned.get(slot.pk)
                old_mob = mobs_by_slot.get(slot.pk)
                if old_mob and old_mob.pk in locked_mobs:
                    parent = old_mob
                if parent is not None:
                    parents[(entry.slug, slot.slot_index)] = parent
        for slot in slots:
            if slot.pk in occupied or slot.pk not in eligible_ids:
                continue
            try:
                spawned = _materialize_placement(placement=slot, spawn_world=world,
                    reconcile_context=context, target=targets[key], check_live_output=False, entry=entry)
            except ActionError as exc:
                if exc.code not in ('container_full', 'container_nested', 'invalid_container'):
                    raise
                # Single spawns and whole bundles roll back their own failed
                # deposit. Full chests must not interrupt other destinations.
                break
            spawned_count += len(spawned)
            if spawned and has_children:
                parents[(entry.slug, slot.slot_index)] = spawned[0]
    return spawned_count, busy
