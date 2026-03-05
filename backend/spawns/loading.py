import collections
import ast
from datetime import datetime, timedelta
import json
import logging
import random

from config import constants as adv_consts

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from backend.core.conditions import evaluate_conditions

from builders.models import (
    Loader,
    Rule,
    ItemTemplate,
    MobTemplate,
    Path)
from worlds.models import World, Room, Zone, Door


logger = logging.getLogger(__name__)


def _evaluate_loader_condition_expression(node, context):
    if isinstance(node, ast.Expression):
        return _evaluate_loader_condition_expression(node.body, context)

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(
                bool(_evaluate_loader_condition_expression(value, context))
                for value in node.values
            )
        if isinstance(node.op, ast.Or):
            return any(
                bool(_evaluate_loader_condition_expression(value, context))
                for value in node.values
            )
        raise ValueError("Unsupported boolean operator in loader_condition.")

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not bool(
                _evaluate_loader_condition_expression(node.operand, context)
            )
        raise ValueError("Unsupported unary operator in loader_condition.")

    if isinstance(node, ast.Compare):
        left = _evaluate_loader_condition_expression(node.left, context)
        comparisons = zip(node.ops, node.comparators)
        for operator, comparator_node in comparisons:
            right = _evaluate_loader_condition_expression(comparator_node, context)
            if isinstance(operator, ast.Eq):
                is_valid = left == right
            elif isinstance(operator, ast.NotEq):
                is_valid = left != right
            elif isinstance(operator, ast.Lt):
                is_valid = left < right
            elif isinstance(operator, ast.LtE):
                is_valid = left <= right
            elif isinstance(operator, ast.Gt):
                is_valid = left > right
            elif isinstance(operator, ast.GtE):
                is_valid = left >= right
            elif isinstance(operator, ast.In):
                is_valid = left in right
            elif isinstance(operator, ast.NotIn):
                is_valid = left not in right
            elif isinstance(operator, ast.Is):
                is_valid = left is right
            elif isinstance(operator, ast.IsNot):
                is_valid = left is not right
            else:
                raise ValueError("Unsupported comparison operator in loader_condition.")
            if not is_valid:
                return False
            left = right
        return True

    if isinstance(node, ast.Name):
        if node.id not in context:
            raise ValueError(
                "Unknown loader_condition variable: %s" % node.id)
        return context[node.id]

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.List):
        return [
            _evaluate_loader_condition_expression(element, context)
            for element in node.elts
        ]

    if isinstance(node, ast.Tuple):
        return tuple(
            _evaluate_loader_condition_expression(element, context)
            for element in node.elts
        )

    if isinstance(node, ast.Set):
        return {
            _evaluate_loader_condition_expression(element, context)
            for element in node.elts
        }

    raise ValueError("Unsupported expression in loader_condition.")


def evaluate_loader_condition(text, context):
    try:
        parsed = ast.parse(text, mode='eval')
    except SyntaxError as exc:
        raise ValueError(str(exc))
    return bool(_evaluate_loader_condition_expression(parsed, context))


def run_loaders(world, zone_id=None, initial=False, repopulate=False):
    """
    Process all loaders in a spawn world. This method should be called over
    using the LoaderRun object because it handles rule execution and door
    resets.

    The 'initial' argument determines whether we're running the loader
    against a world that is a clean state. Any time we're starting up a
    new MPW, check will be False. In a reload situation, it will be true.

    The 'repopulate' argument is typically passed in along with a zone_id,
    and will determine whether to respect the zone and loader's respawn
    wait times. It works much like initial except that it will still
    check for population counts.

    Three things will determine whether a loader will run:
    1) Is it time? (Force attribute)
    2) Are there enough things? (Check attribute)
    3) Does the condition allow this? (Always?)

    Returns a {'rules': [], 'doors': []} object

    sample rules output = [
        OrderedDict({
            1: [<Item 1>, <Mob 1>],
            2: [<Item 2>]
        }),
        OrderedDict({
            3: [<Item 3>, <Item 4>]
        }),
    ]
    """

    if not world.context:
        raise TypeError("Can only run loaders on spawn worlds.")

    check = False # Whether to check for population counts
    force = True # Whether to respect respawn wait times

    if initial:
        check = False
        force = True
    elif repopulate:
        check = True
        force = True
    else:
        check = True
        force = False

    output = {
        'rules': [],
        'doors': [],
    }

    if zone_id:
        zone_qs = Zone.objects.filter(pk=zone_id)
        if world.context_id:
            zone_qs = zone_qs.filter(world_id=world.context_id)
        zones = [zone_qs.get()]
    else:
        zones = world.context.zones.all()

    # Go through each zone and run its loaders if appropriate
    for zone in zones:

        with transaction.atomic():
            zone = Zone.objects.select_for_update().get(pk=zone.pk)

            # Determine if the zone is due for a reset
            should_zone_reset = False
            if not zone.last_respawn_ts:
                should_zone_reset = True
            else:
                threshold = (
                    zone.last_respawn_ts
                    + timedelta(seconds=zone.respawn_wait))
                if timezone.now() > threshold:
                    should_zone_reset = True

            if should_zone_reset:
                zone.last_respawn_ts = timezone.now()
                zone.save(update_fields=['last_respawn_ts'])

        # Reset doors for MPW
        if world.is_multiplayer and should_zone_reset:
            doors = Door.objects.filter(
                from_room__zone=zone)
            for door in doors:
                output['doors'].append({
                    'room_id': door.from_room.id,
                    'room_key': door.from_room.get_game_key(
                        spawn_world=world),
                    'state': door.default_state,
                    'direction': door.direction,
                    'name': door.name,
                })

        for loader in zone.loaders.order_by('order', 'created_ts'):
            output['rules'].append(
                LoaderRun(
                    loader,
                    world,
                    check=check,
                    should_zone_reset=should_zone_reset,
                ).execute(force=force))

    world.last_loader_run_ts = timezone.now()
    world.save(update_fields=['last_loader_run_ts'])

    return output


class LoaderRun:
    """
    Run one loader. State dependent. Depends each functon to alter
    self.output as they are invoked, and only wants to be run once.

    return sample: collections.OrderedDict({
        1: [<Item 1>, <Item 2>],
        2: [<Mob 1>],
    })
    """

    def __init__(self, loader, world, check=True, should_zone_reset=False):

        if not world.context:
            raise TypeError("Can only run loaders on spawn worlds.")

        self.loader = loader
        self.world = world # Spawn world
        self.check = check
        self.should_zone_reset = should_zone_reset
        self.rules_output = collections.OrderedDict()
        self.executed = False

    def execute(self, force=False):
        if self.executed:
            raise RuntimeError("Runner has already been executed.")

        with transaction.atomic():
            # Acquire a row lock so concurrent loader runs do not race each
            # other on gating fields such as last_processing_ts.
            self.loader = Loader.objects.select_related('zone')\
                                        .select_for_update()\
                                        .get(pk=self.loader.pk)

            # -- Condition check
            if self.loader.conditions:
                evaluation = evaluate_conditions(
                    actor=self.world,
                    text=self.loader.conditions)
                if evaluation['result'] == False:
                    self.executed = True
                    return self.rules_output

            # Check for conditions that could prevent the loader from running
            # (though it would still be considered executed)
            if not force:

                # If the loader sets to inherit from zone and the zone should
                # be reset, run the loader
                if self.loader.inherit_zone_wait:
                    if not self.should_zone_reset:
                        self.executed = True
                        return self.rules_output
                    # Account for zone being set to never respawn
                    if self.loader.zone.respawn_wait == -1:
                        self.executed = True
                        return self.rules_output

                # Setting respawn wait to -1 means never respawn
                elif self.loader.respawn_wait == -1:
                    self.executed = True
                    return self.rules_output

                # Setting respawn wait to something other than 0 means wait
                # the appropriate amount
                elif self.loader.last_processing_ts and self.loader.respawn_wait:
                    threshold = (
                        self.loader.last_processing_ts
                        + timedelta(seconds=self.loader.respawn_wait))
                    if timezone.now() < threshold:
                        self.executed = True
                        return self.rules_output

            # Process conditions if there are any
            if (self.loader.loader_condition and
                self.loader.zone and self.loader.zone.is_warzone):
                zone_data = json.loads(self.loader.zone.zone_data or "{}")
                if not isinstance(zone_data, dict):
                    zone_data = {}

                try:
                    is_run_allowed = evaluate_loader_condition(
                        self.loader.loader_condition,
                        zone_data)
                except ValueError as exc:
                    logger.warning(
                        "Error evaluating loader condition '%s': %s",
                        self.loader.loader_condition,
                        exc,
                    )
                    self.executed = True
                    return self.rules_output
                if not is_run_allowed:
                    self.executed = True
                    return self.rules_output

            self.rules_qs = self.loader.rules.all().order_by('order')

            if self.rules_qs:
                self.process_rules()
                self.loader.last_processing_ts = timezone.now()
                self.loader.save(update_fields=['last_processing_ts'])
                self.executed = True
                return self.rules_output

    def process_rules(self):
        for rule in self.rules_qs:
            self.process_rule(rule)
        return self.rules_output

    def process_rule(self, rule):
        """
        Process a rule, meaning invoke a certain targetting method based
        on the data in the rule:
        * target is a room
        * target is a rule
        * target is a zone
        """

        target = rule.target

        # Since template and target are generic FKs, we want to make sure
        # the data we're looking at makes sense. So:
        # * the template has to be an item template or a mob template.
        #   Note that transformation templates are not included here because
        #   those get applied as part of animation rather than during
        #   loading.
        # * the target has to be a room, rule or a zone.
        valid_templates = (ItemTemplate, MobTemplate)
        valid_targets = (Room, Rule, Zone, Path)
        if (not isinstance(rule.template, valid_templates)
            or
            not isinstance(target, valid_targets)):
            return

        if isinstance(rule.template, MobTemplate):
            self.rules_output[rule.id] = self.load_mob_template(rule)
            return
        elif isinstance(rule.template, ItemTemplate):
            self.rules_output[rule.id] = self.load_item_template(rule)
            return

        raise ValueError("Invalid rule template: %s" % rule.template)

    def _num_loaded_for_rule(self, rule):
        if not self.check:
            return 0

        from spawns.models import Item, Mob

        if isinstance(rule.template, MobTemplate):
            return Mob.objects.filter(
                world=self.world,
                rule=rule,
                is_pending_deletion=False,
            ).count()

        if isinstance(rule.template, ItemTemplate):
            return Item.objects.filter(
                world=self.world,
                rule=rule,
                is_pending_deletion=False,
            ).count()

        return 0

    def load_mob_template(self, rule):

        output = []
        num_loaded = self._num_loaded_for_rule(rule)

        should_load = rule.num_copies - num_loaded

        from spawns.ai_sidecar import maybe_enqueue_ai_sidecar_mob_spawned

        for i in range(0, should_load):

            room = None
            roams = None # None here essentially means static
            # Determine where we're going to load this template. We want
            # to find a single room, and we want to know whether the
            # resulting mob should roam or not.
            target = rule.target
            if isinstance(target, Room):
                room = target
            else:
                roams = target
                # Either zone or path, so we're going to be roaming, and
                # have to filter down a rooms queryset
                if isinstance(target, Zone):
                    rooms_qs = target.rooms.exclude(
                        type=adv_consts.ROOM_TYPE_WATER)
                elif isinstance(target, Path):
                    rooms_qs = target.rooms.all()
                    if target.entry_room:
                        rooms_qs = Room.objects.filter(pk=target.entry_room.pk)
                else:
                    raise ValueError(
                        "invalid target type: %s" % rule.target_type)

                # Filter down
                rooms_qs = rooms_qs.exclude(
                    flags__code=adv_consts.ROOM_FLAG_NO_LOAD
                ).exclude(
                    flags__code=adv_consts.ROOM_FLAG_NO_ROAM
                )

                # Now select one random room from among the possible choices.
                # This would be a good spot to place restrictions for example
                # due to max num in a path.
                try:
                    room = rooms_qs[random.randrange(0, rooms_qs.count())]
                except ValueError: # an empty queryset would throw this
                    continue

            spawned_mob = rule.template.spawn(
                target=room,
                spawn_world=self.world,
                roams=roams,
                rule=rule,
            )
            output.append(spawned_mob)

            maybe_enqueue_ai_sidecar_mob_spawned(
                mob=spawned_mob,
                source="loader",
                loader_id=self.loader.id,
                rule_id=rule.id,
            )

        return output

    def load_item_template(self, rule):
        target = rule.target
        output = []

        # Basic validation - only target rooms or rules
        if not isinstance(target, (Room, Rule, Zone, Path)):
            raise ValueError(
                "Item templates can only load in rooms or in the output of "
                "another rule.")

        output = []

        if isinstance(target, Rule):
            instances = self.rules_output.get(target.id)
            if instances is None:
                return []
            template = rule.template
            for instance in instances:
                output.extend([
                    rule.template.spawn(
                        target=instance,
                        spawn_world=self.world,
                        rule=rule)
                    for i in range(0, rule.num_copies)
                ])
            return output

        num_loaded = self._num_loaded_for_rule(rule)

        should_load = rule.num_copies - num_loaded

        for i in range(0, should_load):

            if isinstance(target, Room):
                room = target
            else:
                rooms_qs = target.rooms.exclude(
                    flags__code=adv_consts.ROOM_FLAG_NO_LOAD,
                ).exclude(
                    type=adv_consts.ROOM_TYPE_WATER,
                )

                # Now select one random room from among the possible choices.
                try:
                    room = rooms_qs[random.randrange(0, rooms_qs.count())]
                except ValueError: # an empty queryset would throw this
                    continue

            output.append(
                rule.template.spawn(
                    target=room,
                    spawn_world=self.world,
                    rule=rule))

        return output
