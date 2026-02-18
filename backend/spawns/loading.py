import collections
from datetime import datetime, timedelta
import json
import random

from config import constants as adv_consts

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from backend.core.conditions import evaluate_conditions

from builders.models import (
    Rule,
    ItemTemplate,
    MobTemplate,
    Path)
from spawns.extraction import extract_population
from worlds.models import World, Room, Zone, Door


def run_loaders(world, zone_id=None, initial=False, repopulate=False, rdb=None):
    """
    Process all loaders in a spawn world. This method should be called over
    using the LoaderRun object because it handles loading the world's population
    data and processing door resets.

    Loaders are RDB aware for two reasons:
    * Fetching population data to accomodate for rule requirements
    * Checking current facts for loader conditions

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

    rdb = rdb or world.rdb
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

    if check:
        game_world = rdb.fetch(world.key)
        population_data = extract_population(game_world)
    else:
        population_data = None
        game_world = None

    # Update warzone data, if applicable
    if population_data:
        for _zone_id, zone_data in population_data['zone_data'].items():
            if zone_data:
                try:
                    zone = Zone.objects.get(pk=_zone_id)
                except Zone.DoesNotExist:
                    continue
                zone.zone_data = json.dumps(zone_data)
                zone.save()

    output = {
        'rules': [],
        'doors': [],
    }

    if zone_id:
        zones = [Zone.objects.get(pk=zone_id)]
    else:
        zones = world.context.zones.all()

    # Go through each zone and run its loaders if appropriate
    for zone in zones:

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
            zone.save()

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
                    game_world=game_world,
                    check=check,
                    should_zone_reset=should_zone_reset,
                    rdb=rdb,
                    population_data=population_data,
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

    def __init__(self, loader, world, game_world=None, check=True, rdb=None,
        population_data=None, should_zone_reset=False):

        if not world.context:
            raise TypeError("Can only run loaders on spawn worlds.")

        self.loader = loader
        self.world = world # Spawn world
        self.game_world = game_world
        self.check = check
        self.population_data = population_data
        self.should_zone_reset = should_zone_reset
        self.rdb = rdb or world.rdb if self.check else None
        self.rules_output = collections.OrderedDict()
        self.executed = False

    def execute(self, force=False):
        if self.executed:
            raise RuntimeError("Runner has already been executed.")

        # -- Condition check
        # We need to support 2 Loader Condition scenarios:
        # 1) We're loading the world and the loader is in an initial
        #    state, in case of which we don't have a loaded spawn world
        #    yet.
        # 2) We're loading the world and the loader is in a reload state,
        #    in case of which we have a loaded spawn world that has more
        #    up to date data than the API world.
        if self.loader.conditions:
            if self.game_world:
                actor = self.game_world
            else:
                actor = self.world
            evaluation = evaluate_conditions(
                actor=actor,
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
            zone_data = json.loads(self.loader.zone.zone_data)

            try:
                is_run_allowed = False
                is_run_allowed = eval(
                    self.loader.loader_condition,
                    {"__builtins__":None},
                    zone_data)
            except (NameError, SyntaxError, TypeError):
                print("Error with loader condition: %s and data %s" % (
                    self.loader.loader_condition,
                    zone_data))
                return self.rules_output
            if not is_run_allowed:
                return self.rules_output

        self.rules_qs = self.loader.rules.all().order_by('order')

        if self.rules_qs:
            self.process_rules()
            self.loader.last_processing_ts = timezone.now()
            self.loader.save()
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

    def get_num_from_templates_in_room(self, template, room):
        """
        Given a template, return the number of items or mobs in that room
        that are from that template. This does a live check against RDB

        For a given template and room, get the number of spawns of that
        template in that room.
        """
        num = 0
        game_room = self.rdb.fetch(room.get_game_key(self.world))

        if isinstance(template, ItemTemplate):
            candidates = game_room.inventory
        elif isinstance(template, MobTemplate):
            candidates = game_room.get_chars(mobs_only=True)

        for candidate in candidates:
            try:
                if int(candidate.template_id) == template.pk:
                    num += 1
            except TypeError:
                continue

        return num

    def load_mob_template(self, rule):

        output = []

        if self.check:
            # Get the number of mobs in the population data that have been
            # loaded by this rule.
            num_loaded = len(self.population_data['rules'].get(rule.id, []))
        else:
            num_loaded = 0

        should_load = rule.num_copies - num_loaded

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

            output.append(
                rule.template.spawn(
                    target=room,
                    spawn_world=self.world,
                    roams=roams,
                    rule=rule))

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
            instances = self.rules_output[target.id]
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

        if self.check:
            num_loaded = len(self.population_data['rules'].get(rule.id, []))
        else:
            num_loaded = 0

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
