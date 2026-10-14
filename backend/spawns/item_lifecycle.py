"""Transactional item retention, transfers, and bounded retirement.

Physical item roots serialize containment changes. A retiring item is hidden
immediately; descendants are retired and deleted in bounded subsequent batches.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import timedelta
from math import ceil
import logging
from time import monotonic

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q

from spawns.actions.base import ActionError
from spawns.instance_clock import clock_guard, gameplay_now, live_worlds
from worlds.upkeep_policy import effective_upkeep, timer_seconds

lifecycle_write = ContextVar('item_lifecycle_write', default=False)
_placement = ContextVar('item_authored_placement', default=None)
MAX_TRANSFER_ITEMS = 4096
MAX_CONTAINER_DEPTH = 32
logger = logging.getLogger(__name__)
FIELDS = ['container_type', 'container_id', 'upkeep_kind', 'upkeep_root', 'upkeep_placed', 'upkeep_storage',
          'upkeep_policy', 'upkeep_started_ts', 'upkeep_empty_ts', 'upkeep_due_ts',
          'upkeep_revision']


@contextmanager
def authored_placement(policy):
    token = _placement.set(policy or {})
    try:
        yield
    finally:
        _placement.reset(token)


@contextmanager
def raw_writes():
    token = lifecycle_write.set(True)
    try:
        yield
    finally:
        lifecycle_write.reset(token)


def _models():
    from spawns.models import Item
    from worlds.models import Room, World
    return Item, Room, World


def _item_ct():
    return ContentType.objects.get_for_model(_models()[0]).pk


def _chains(items):
    """Batch each ancestor depth, independent of the number of looted items."""
    Item, _, _ = _models()
    known = {i.pk: i for i in items}
    frontier = list(items)
    for _ in range(MAX_CONTAINER_DEPTH):
        ids = {i.container_id for i in frontier if i.container_type_id == _item_ct()} - known.keys()
        if not ids:
            break
        frontier = list(Item.objects.filter(pk__in=ids))
        if len(frontier) != len(ids):
            raise ActionError('This container is unavailable.', code='item_container_invalid')
        known.update((i.pk, i) for i in frontier)
    result = {}
    for item in items:
        chain, seen = [item], {item.pk}
        while chain[-1].container_type_id == _item_ct():
            parent = known.get(chain[-1].container_id)
            if parent is None or parent.pk in seen or len(chain) >= MAX_CONTAINER_DEPTH or parent.world_id != item.world_id:
                raise ActionError('This container is unavailable.', code='item_container_invalid')
            chain.append(parent)
            seen.add(parent.pk)
        result[item.pk] = chain
    return result


def _chain(item):
    return _chains([item])[item.pk]


def _available(chain):
    if any(i.upkeep_retiring or i.is_pending_deletion for i in chain):
        raise ActionError('That item is no longer available.', code='item_not_found')


def _tree(items):
    """Fetch each depth in one query, with a firm transaction/memory limit."""
    Item, _, _ = _models()
    result = {i.pk: i for i in items}
    if len(result) > MAX_TRANSFER_ITEMS:
        raise ActionError('Move fewer items at once.', code='item_transfer_budget')
    frontier = list(result)
    for _ in range(MAX_CONTAINER_DEPTH):
        children = list(Item.objects.filter(container_type_id=_item_ct(),
                        container_id__in=frontier).order_by('id')[:MAX_TRANSFER_ITEMS + 1])
        children = [i for i in children if i.pk not in result]
        if not children:
            return list(result.values())
        result.update((i.pk, i) for i in children)
        if len(result) > MAX_TRANSFER_ITEMS:
            raise ActionError('Move fewer items at once.', code='item_transfer_budget')
        frontier = [i.pk for i in children]
    raise ActionError('The containers are nested too deeply.', code='item_transfer_budget')


def _ground_timer(world, room):
    policy = effective_upkeep(world)
    timer = policy['ground_items']['abandoned']
    zone_timer = (world.upkeep_zone_snapshot.get(str(room.zone_id), {})
                  if world.upkeep_snapshot else room.zone.ground_item_upkeep)
    if zone_timer and zone_timer.get('mode') != 'inherit_world':
        timer = zone_timer
    return deepcopy(timer)


def _minimum(*dates):
    dates = [d for d in dates if d is not None]
    return min(dates) if dates else None


def upkeep_notice(item, *, world=None, now=None):
    """Inspection snapshots, not client wall-clock countdowns (instances pause)."""
    if item.upkeep_due_ts:
        now = now or gameplay_now(world or item.world_id)
        seconds = max(0, (item.upkeep_due_ts - now).total_seconds())
        duration = f'about {ceil(seconds / 60)} minutes' if seconds > 60 else 'less than a minute'
        return (f'At inspection: expires in {duration}, including anything left inside.'
                if item.type in ('corpse', 'container') else f'At inspection: expires in {duration}.')
    if item.upkeep_kind == 'player_corpse' and item.upkeep_empty_ts is None:
        if item.upkeep_policy.get('empty', {}).get('mode') == 'none':
            return 'Automatic corpse decay is disabled for this corpse.'
        return 'Retained while death equipment remains. Emptying this corpse starts its configured decay timer.'
    if item.upkeep_kind == 'npc_corpse' and item.upkeep_empty_ts is None and timer_seconds(item.upkeep_policy['empty']):
        return 'Retained until emptied. Emptying this corpse starts its configured decay timer.'
    if item.upkeep_started_ts and item.upkeep_due_ts is None:
        return 'Automatic cleanup is disabled for this item.'
    if item.upkeep_storage:
        return 'Protected storage: deposited items are retained here.'
    if item.upkeep_placed and item.type == 'container':
        return 'Deposited items follow the local ground-item cleanup policy.'
    return None


def _set_state(items, target, world, *, placed=None, inherited_deadlines=None):
    """Resolve one destination for the whole batch; no per-item policy queries."""
    Item, Room, _ = _models()
    now = gameplay_now(world)
    chain = _chain(target) if isinstance(target, Item) else []
    root = chain[-1] if chain else None
    target_due = _minimum(*(ancestor.upkeep_due_ts for ancestor in chain))
    corpse = next((i for i in chain if i.upkeep_kind in ('npc_corpse', 'player_corpse')), None)
    protected = any(i.upkeep_storage or i.is_persistent for i in chain)
    top = root.container if root else target
    ground = isinstance(top, Room)
    timer = _ground_timer(world, top) if ground and not protected and not corpse else None
    by_id = {i.pk: i for i in items}
    for item in items:
        # Find containment inside this batch before changing its policies.
        # Moving a whole tree must not give corpse contents a ground timer.
        parent, group_corpse, seen = item, False, set()
        while parent.container_type_id == _item_ct() and parent.container_id in by_id:
            if parent.pk in seen:
                raise ActionError('Invalid container cycle.', code='invalid_target')
            seen.add(parent.pk)
            parent = by_id[parent.container_id]
            group_corpse = group_corpse or parent.upkeep_kind in ('npc_corpse', 'player_corpse')
        if len(chain) + len(seen) + 1 > MAX_CONTAINER_DEPTH:
            raise ActionError('The containers are nested too deeply.', code='item_transfer_budget')
        inherited_due = (inherited_deadlines or {}).get(parent.pk)
        item.upkeep_root_id = root.pk if root else parent.pk
        item.upkeep_revision += 1
        if item.upkeep_kind in ('npc_corpse', 'player_corpse'):
            continue  # Death lifecycle remains authoritative if explicitly moved.
        item.upkeep_placed = placed is not None
        item.upkeep_storage = bool(placed and placed.get('contents') == 'preserve')
        if placed is not None or item.is_persistent or protected or corpse or group_corpse or not ground:
            item.upkeep_due_ts = None
            item.upkeep_started_ts = None
            item.upkeep_policy = {}
            item.upkeep_kind = ''
        else:
            if item.upkeep_started_ts is None:
                item.upkeep_started_ts = now
                item.upkeep_policy = {'abandoned': deepcopy(timer)}
            seconds = timer_seconds(item.upkeep_policy['abandoned'])
            deadline = item.upkeep_started_ts + timedelta(seconds=seconds) if seconds else None
            item.upkeep_due_ts = _minimum(item.upkeep_due_ts, deadline, inherited_due,
                                          target_due)
            item.upkeep_kind = 'ground'
    Item.objects.bulk_update(items, FIELDS, batch_size=256)


def save_item(item, args, kwargs):
    """Cover ordinary model writes; bulk transfer callers use move_items."""
    Item, _, World = _models()
    world = World.objects.select_related('config', 'context__config',
                                        'context__instance_of__config').get(pk=item.world_id)
    with clock_guard(world), transaction.atomic():
        if not item._state.adding:
            previous = Item.objects.get(pk=item.pk)
            if (previous.container_type_id, previous.container_id) == (item.container_type_id, item.container_id):
                root = _chain(previous)[-1]
                Item.objects.select_for_update().get(pk=root.pk)
                previous = Item.objects.select_for_update().get(pk=item.pk)
                current_chain = _chain(previous)
                _available(current_chain)
                if current_chain[-1].pk != root.pk:
                    raise ActionError('That item has moved. Try again.', code='item_moved')
                for name in FIELDS:
                    name = name + '_id' if name in ('upkeep_root', 'container_type') else name
                    setattr(item, name, getattr(previous, name))
                with raw_writes():
                    return item.save(*args, **kwargs)
            target = item.container
            moved = move_items([item], target, world=world)
            # Preserve non-containment fields in a caller's ordinary save.
            state = moved[0]
            for name in FIELDS:
                setattr(item, name + '_id' if name in ('upkeep_root', 'container_type') else name,
                        getattr(state, name + '_id' if name in ('upkeep_root', 'container_type') else name))
            fields = kwargs.get('update_fields')
            if fields is not None:
                kwargs['update_fields'] = list(set(fields) | set(FIELDS))
            with raw_writes():
                return item.save(*args, **kwargs)
        target = item.container
        if isinstance(target, Item):
            before = _chain(target)
            Item.objects.select_for_update().get(pk=before[-1].pk)
            target = Item.objects.get(pk=target.pk)
            current_chain = _chain(target)
            if current_chain[-1].pk != before[-1].pk:
                raise ActionError('That container has moved. Try again.', code='item_moved')
            if target.world_id != world.pk or any(i.upkeep_retiring for i in current_chain):
                raise ActionError('That container is no longer available.', code='item_not_found')
        from spawns.item_stock import lock_destinations
        lock_destinations(world.pk, [target])
        with raw_writes():
            result = item.save(*args, **kwargs)
        # Precreated corpses are armed explicitly at completed death.
        if item.type != 'corpse':
            _set_state([item], target, world, placed=_placement.get())
        else:
            Item.objects.filter(pk=item.pk).update(upkeep_root_id=item.pk)
            item.upkeep_root_id = item.pk
        return result


def move_items(items, target, *, world=None):
    """Move a selection and its descendants under physical-root locks."""
    Item, _, World = _models()
    ids = sorted({i.pk for i in items})
    if not ids:
        return []
    if len(ids) > MAX_TRANSFER_ITEMS:
        raise ActionError('Move fewer items at once.', code='item_transfer_budget')
    if world is None:
        world = World.objects.select_related('config', 'context__config',
                    'context__instance_of__config').get(pk=items[0].world_id)
    with clock_guard(world), transaction.atomic():
        before = list(Item.objects.filter(pk__in=ids, world_id=world.pk).order_by('id'))
        if len(before) != len(ids):
            raise ActionError('That item is no longer available.', code='item_not_found')
        chain_map = _chains(before)
        chains = [chain_map[i.pk] for i in before]
        target_chain = _chain(target) if isinstance(target, Item) else []
        roots = sorted({c[-1].pk for c in [*chains, target_chain] if c})
        list(Item.objects.select_for_update().filter(pk__in=roots).order_by('id'))
        current = list(Item.objects.select_for_update(of=('self',)).filter(pk__in=ids)
                       .select_related('definition', 'currency').order_by('id'))
        if len(current) != len(ids):
            raise ActionError('That item is no longer available.', code='item_not_found')
        current_chains = _chains(current)
        for old, item in zip(before, current):
            chain = current_chains[item.pk]
            _available(chain)
            if chain[-1].pk not in roots or (old.container_type_id, old.container_id) != (item.container_type_id, item.container_id):
                raise ActionError('That item has moved. Try again.', code='item_moved')
        if isinstance(target, Item):
            target = Item.objects.get(pk=target.pk, world_id=world.pk)
            chain = _chain(target)
            _available(chain)
            if chain[-1].pk not in roots:
                raise ActionError('That container has moved. Try again.', code='item_moved')
        members = _tree(current)
        if isinstance(target, Item) and target.pk in {i.pk for i in members}:
            raise ActionError('You cannot place a container inside itself.', code='invalid_target')
        if isinstance(target, Item) and any(i.upkeep_kind in ('npc_corpse', 'player_corpse') for i in members):
            raise ActionError('A corpse must remain outside other containers.', code='invalid_target')
        from spawns.item_stock import lock_destinations
        lock_destinations(world.pk, [target])
        # A shared inherited deadline cannot make one unrelated item expire early.
        inherited = {i.pk: _minimum(*(a.upkeep_due_ts for a in c
                        if a.upkeep_kind not in ('npc_corpse', 'player_corpse')))
                     for i, c in zip(before, chains)}
        old_parents = {i.container_id for i in before if i.container_type_id == _item_ct()}
        ct = ContentType.objects.get_for_model(target)
        for item in current:
            item.container_type_id, item.container_id = ct.pk, target.pk
        members_by_id = {i.pk: i for i in members}
        members_by_id.update((i.pk, i) for i in current)
        # Retain individual source deadlines while resolving destination policy
        # once and writing the entire selection in bounded batches.
        _set_state(list(members_by_id.values()), target, world, inherited_deadlines=inherited)
        refresh_empty_corpses(old_parents, now=gameplay_now(world))
        return current


def refresh_empty_corpses(ids, *, now):
    Item, _, _ = _models()
    if not ids:
        return
    contents = Item.objects.filter(container_type_id=_item_ct(), container_id=OuterRef('pk'),
                                   upkeep_retiring=False, is_pending_deletion=False)
    corpses = list(Item.objects.filter(pk__in=ids, upkeep_kind__in=['npc_corpse', 'player_corpse'],
                     upkeep_empty_ts__isnull=True, upkeep_retiring=False)
                   .annotate(has_contents=Exists(contents)).filter(has_contents=False))
    for corpse in corpses:
        corpse.upkeep_empty_ts = now
        seconds = timer_seconds(corpse.upkeep_policy['empty'])
        corpse.upkeep_due_ts = _minimum(corpse.upkeep_due_ts,
                                        now + timedelta(seconds=seconds) if seconds else None)
        corpse.upkeep_revision += 1
    if corpses:
        Item.objects.bulk_update(corpses, ['upkeep_empty_ts', 'upkeep_due_ts', 'upkeep_revision'])


def arm_corpse(corpse, *, kind, owner_id=None):
    Item, _, World = _models()
    world = World.objects.select_related('config', 'context__config',
                                        'context__instance_of__config').get(pk=corpse.world_id)
    with clock_guard(world), transaction.atomic():
        corpse = Item.objects.select_for_update().get(pk=corpse.pk)
        if corpse.upkeep_kind in ('npc_corpse', 'player_corpse'):
            return
        now = gameplay_now(world)
        policy = effective_upkeep(world)['corpses'][kind]
        seconds = timer_seconds(policy['unclaimed'])
        Item.objects.filter(pk=corpse.pk).update(upkeep_kind=kind + '_corpse',
            upkeep_owner_id=owner_id, upkeep_root_id=corpse.pk, upkeep_policy=policy,
            upkeep_started_ts=now, upkeep_due_ts=now + timedelta(seconds=seconds) if seconds else None)
        # Contents inherit the corpse's fate, including the player recovery exemption.
        # These contents came from character possession or death loot. Their
        # existing physical-root index reaches nested bags without loading every
        # descendant into Python or imposing an interactive transfer limit on
        # death. Unclassified pre-feature descendants have no timers to cancel;
        # actual containment still governs their retirement and later recovery.
        roots = [corpse.pk, *corpse.inventory.values_list('pk', flat=True)]
        Item.objects.filter(world_id=world.pk).filter(
            Q(pk__in=roots) | Q(upkeep_root_id__in=roots),
        ).exclude(pk=corpse.pk).update(upkeep_root_id=corpse.pk,
            upkeep_due_ts=None, upkeep_started_ts=None, upkeep_policy={}, upkeep_kind='',
            upkeep_placed=False, upkeep_storage=False, upkeep_revision=F('upkeep_revision') + 1)
        refresh_empty_corpses([corpse.pk], now=now)


def _retire(item, now):
    Item, Room, _ = _models()
    chain = _chain(item)
    _available(chain)
    room = chain[-1].container
    item.upkeep_retiring = True
    Item.objects.filter(pk=item.pk).update(upkeep_retiring=True, is_pending_deletion=True,
                                          upkeep_due_ts=None, upkeep_revision=F('upkeep_revision') + 1)
    refresh_empty_corpses([item.container_id] if item.container_type_id == _item_ct() else [], now=now)
    return room.pk if isinstance(room, Room) else None


def _publish_retirements(world_id, rooms):
    from spawns.events import GameEvent, enqueue_game_events
    from spawns.models import Player
    from spawns.state_payloads import safe_capitalize
    recipients = {}
    for pk, room_id in Player.objects.filter(world_id=world_id, room_id__in=rooms,
                                             in_game=True).values_list('pk', 'room_id'):
        recipients.setdefault(room_id, []).append(f'player.{pk}')
    events = []
    for room_id, items in rooms.items():
        if not recipients.get(room_id):
            continue
        corpses = [item for item in items if 'corpse' in item.upkeep_kind]
        ground_items = [item for item in items if 'corpse' not in item.upkeep_kind]
        messages = []
        if corpses:
            messages.append(f'{corpses[0].name[:120]} crumbles to dust.' if len(corpses) == 1
                            else 'Several corpses crumble to dust.')
        if ground_items:
            messages.append(f'{ground_items[0].name[:120]} is cleared away.' if len(ground_items) == 1
                            else f'{len(ground_items)} expired objects are cleared away.')
        text = ' '.join(safe_capitalize(message) for message in messages)
        events.append(GameEvent('item.expired', {
            'item_keys': [item.key for item in items], 'room_id': room_id,
            'runtime_world_id': world_id,
        }, recipients[room_id], text))
    enqueue_game_events(events)


def _drain_retiring(world_id, budget):
    """Expand at most budget edges, then delete at most budget leaf rows."""
    Item, _, _ = _models()
    retired = Item.objects.filter(world_id=world_id, upkeep_retiring=True)
    children = Item.objects.filter(world_id=world_id, container_type_id=_item_ct(),
                                  container_id__in=retired.values('pk'), upkeep_retiring=False)
    ids = list(children.order_by('id').values_list('id', flat=True)[:budget])
    if ids:
        Item.objects.filter(pk__in=ids).update(upkeep_retiring=True, is_pending_deletion=True,
                                              upkeep_due_ts=None)
    remaining = budget - len(ids)
    children = Item.objects.filter(container_type_id=_item_ct(), container_id=OuterRef('pk'))
    leaves = list(retired.annotate(has_children=Exists(children)).filter(has_children=False)
                  .order_by('id').values_list('id', flat=True)[:remaining])
    if leaves:
        Item.objects.filter(pk__in=leaves).delete()
    return len(ids) + len(leaves), len(leaves)


def process_due_items(world_id, now=None, budget=128):
    """One runtime, bounded work; duplicate tasks claim the same world row briefly."""
    from spawns.events import flush_game_event_outbox
    from config import game_settings
    if not game_settings.ITEM_UPKEEP_ENABLED:
        return {'retired': 0, 'deleted': 0, 'work': 0, 'disabled': True}
    started = monotonic()
    Item, _, World = _models()
    budget = max(1, min(int(budget), 1024))
    world = World.objects.select_related('config', 'context__config').get(pk=world_id)
    with clock_guard(world), transaction.atomic():
        # A world claim serializes cleanup jobs only. Gameplay takes item roots,
        # never this world row. Independent runtimes do not share a lock.
        if not World.objects.select_for_update(skip_locked=True).filter(
                pk=world_id, lifecycle='running').exists():
            return {'retired': 0, 'deleted': 0, 'busy': True}
        now = now or gameplay_now(world)
        due = live_worlds(Item.objects.filter(world_id=world_id, upkeep_retiring=False, is_pending_deletion=False,
                         upkeep_due_ts__lte=now)).order_by('upkeep_due_ts', 'id')
        candidates = list(due[:min(budget, 32)])
        retired, skipped = 0, 0
        rooms = {}
        for candidate in candidates:
            if monotonic() - started >= 3:
                break
            root_id = candidate.upkeep_root_id or candidate.pk
            root = Item.objects.select_for_update(skip_locked=True).filter(pk=root_id).first()
            if root is None:
                skipped += 1
                continue
            item = Item.objects.select_for_update(skip_locked=True).filter(pk=candidate.pk,
                          upkeep_retiring=False, is_pending_deletion=False, upkeep_due_ts__lte=now).first()
            if item is None or item.upkeep_root_id != candidate.upkeep_root_id:
                continue
            try:
                room_id = _retire(item, now)
            except ActionError:
                continue
            retired += 1
            if room_id is not None:
                rooms.setdefault(room_id, []).append(item)
        _publish_retirements(world_id, rooms)
        work, deleted = _drain_retiring(world_id, budget - retired)
        transaction.on_commit(flush_game_event_outbox, robust=True)
    result = {'retired': retired, 'deleted': deleted, 'work': work + retired,
              'skipped': skipped, 'duration_ms': round((monotonic() - started) * 1000),
              'budget_exhausted': work + retired >= budget or retired + skipped < len(candidates),
              'oldest_overdue_seconds': max(0, (now - candidates[0].upkeep_due_ts).total_seconds()) if candidates else 0}
    logger.info('Item upkeep world=%s result=%s', world_id, result)
    return result
