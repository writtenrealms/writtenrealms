import collections
from datetime import timedelta
import json
import logging

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from config import constants as api_consts
from builders.models import (
    RoomCommandCheck, Quest, Faction, FactionAssignment)
from spawns import serializers as spawn_serializers
from spawns.models import (
    Player,
    Item,
    Mob,
    Equipment,
    RoomCommandCheckState,
    PlayerQuest,
    PlayerTrophy,
    PlayerFlexSkill,
    PlayerFeat,
    Alias,
    Mark)
from worlds.models import World, Room, Door

logger = logging.getLogger('django')


class APIExtractor:
    """
    The extraction data comes in as a list of 'chunk' objects, each containing
    a 'model' directive which lets the API know what kind of save data it is.
    """

    def __init__(self, world, extraction_data):
        """
        World should be an API spawned world object
        """
        if not world.context:
            raise TypeError("Can only extract spawn worlds.")

        self.world = world

        # If the world is an instance, save a reference to the base world context
        self.base_context = self.world.context
        self.is_instance = False
        if self.base_context.instance_of:
            self.base_context = self.base_context.instance_of
            self.is_instance = True

        # Group chunks by chunk 'model' attribute
        self.chunks = self.group_chunks(extraction_data)

    # Top level invoked methods

    def extract_player(self, player):
        "Extract a player's data from the world"
        self.simple_save('player')
        player.refresh_from_db()

        self.save_viewed_rooms(player)
        self.save_factions(player)
        self.save_aliases(player)
        self.save_trophy(player)
        self.save_skills(player)
        self.save_marks(player)
        self.save_items(player)
        # It's important for equipment to be after items because all items
        # in the extraction data are marked as being on the player, since
        # that's how it is in the game schema. It's looking over which items
        # are marked as being in the player's equipment slot that we change
        # the container to be the equipment (done in the Equipment extraction
        # serializer).
        self.simple_save('equipment')

    def extract_world(self):
        """
        Extract all of the non-player portion of a world.

        Have to be careful not to call this method with an extraction payload
        that doesn't contain the world's mobs. It will marked them all as
        deleted.
        """
        self.save_items(self.world)
        self.save_mobs()

    def extract_spw(self, player):
        """
        Extract a world taking advantage of fact that the player's items will
        be included in the world's items.
        """
        self.simple_save('player')
        player.refresh_from_db()
        self.save_viewed_rooms(player)
        self.save_factions(player)
        self.save_aliases(player)
        self.save_trophy(player)
        self.save_skills(player)
        self.save_marks(player)
        self.save_items(self.world)
        self.save_mobs()
        self.simple_save('equipment')
        self.save_doors()
        self.save_command_checks()
        self.save_facts()

    def extract_persistent_items(self):
        # Get all the persistent items we currently know about
        existing_item_ids = set(Item.objects.filter(
            world_id=self.world.id,
            is_persistent=True).values_list('id', flat=True))

        extracted_item_ids = set([
            int(chunk['id']) for chunk in self.chunks.get('item', [])
            if chunk.get('id')
        ])

        # Remove items that were not seen in the extraction
        Item.objects.filter(
            is_persistent=True,
            pk__in=existing_item_ids - extracted_item_ids
        ).update(is_pending_deletion=True, pending_deletion_ts=timezone.now())

        # Also remove any item that was contained in the persistent item
        Item.objects.filter(
            container_type=ContentType.objects.get_for_model(Item),
            container_id__in=existing_item_ids - extracted_item_ids,
        ).update(is_pending_deletion=True, pending_deletion_ts=timezone.now())

        # Update the other items
        with transaction.atomic():
            extracted_items = Item.objects\
                    .select_for_update()\
                    .filter(
                        pk__in=extracted_item_ids)

            for chunk in self.chunks.get('item', []):
                item = extracted_items.get(pk=chunk['id'])
                item.container_type = ContentType.objects.get(
                    model=chunk['container_type'])
                item.container_id = int(chunk['container_id'])
                item.is_pending_deletion = False
                item.save()

    # Utility methods

    def group_chunks(self, chunks):
        """
        There's big performance benefits to doing one big query fetching
        multiple elements vs multiple queries for a single element. This method
        takes extraction data and groups it by keys, respecting the order it
        encountered them in.

        Returns:
        OrderedDict([('item', [...]), ('mob', [...])])
        """
        grouped_chunks = collections.OrderedDict()
        for chunk in chunks:
            if chunk['model'] not in grouped_chunks:
                grouped_chunks[chunk['model']] = []
            grouped_chunks[chunk['model']].append(chunk)
        return grouped_chunks

    # Component methods

    def simple_save(self, chunk_type):
        # Map out the chunk's 'model' attribute with serializer + queryset
        type_mapper = {
            'player': {
                'serializer': spawn_serializers.ExtractPlayerSerializer,
                'queryset': Player.objects.all(),
            },
            'equipment': {
                'serializer': spawn_serializers.ExtractEquipmentSerializer,
                'queryset': Equipment.objects.all(),
            },
        }
        serializer_cls = type_mapper[chunk_type]['serializer']
        qs = type_mapper[chunk_type]['queryset']

        updated_instances = []
        for chunk in self.chunks.get(chunk_type, []):
            try:
                qs.get(pk=chunk['id'])
            except ObjectDoesNotExist:
                continue

            serializer = serializer_cls(
                qs.get(pk=chunk['id']),
                data=chunk,
                context={'spawn_world': self.world})
            # 2022-06-27: Removing is the `raise_exception=True` argument
            # because of an intermittent bug where players can't save. It
            # seems to be caused by an equipment reference passed up from
            # the game engine that no longer exists in the API. When this
            # happens, players can't exit the game at all.
            # This is not ideal because it will cause silent errors where
            # users will actually lose equipment as soon as they log out,
            # but it's better than the alternative of keeping them in the
            # game with no way to get out.
            if serializer.is_valid():
                instance = serializer.save()
                updated_instances.append(instance)
            else:
                logger.info("Error saving %s %s:" % (chunk_type, chunk['id']))
                logger.info(serializer.errors)
        return updated_instances

    def save_items(self, reference):
        """
        Process all the items within a certain reference context.

        Reference can be player a world. It is the 'holder' object
        from which we are looking at data to see if anything should be
        marked as pending deletion.
        """

        # Create the inventory of the items that were on the reference
        # last the API knew.
        existing_item_ids = set()

        if isinstance(reference, Player):
            player = reference
            # Add the player's inventory
            player_inventory_ids = player.inventory.values_list('id', flat=True)
            existing_item_ids.update(player_inventory_ids)
            # Add items contained in the player's inventory containers
            existing_item_ids.update(
                Item.objects.filter(
                    container_type=ContentType.objects.get_for_model(Item),
                    container_id__in=player_inventory_ids,
                    #world_id=self.world.id,
                ).values_list('id', flat=True))
            # Add the player's equipment
            existing_item_ids.update(
                player.equipment.inventory.values_list('id', flat=True))

            # If any of the player's items still have a rule attached to them,
            # clear that attachement.
            items_from_rules = Item.objects.filter(
                pk__in=existing_item_ids,
                rule_id__isnull=False)
            if items_from_rules.exists():
                items_from_rules.update(rule_id=None)

        elif isinstance(reference, World):
            existing_item_ids.update(
                reference.items.values_list('id', flat=True))
        else:
            raise ValueError('Invalid reference: %s' % reference)

        # Create the inventory of the items that are being extracted
        extracted_item_ids = set([
            int(chunk['id']) for chunk in self.chunks.get('item', [])
            if chunk.get('id')
        ])

        # If any items were marked as pending deletion, but are now seen in
        # the extracted data (for example if a loader ran right before
        # extraction and the items hadn't made it yet), mark them as no longer
        # pending deletion.
        Item.objects.filter(
            pk__in=extracted_item_ids,
            is_pending_deletion=True,
        ).update(is_pending_deletion=False)

        # Any items that were in the existing set but not in the extracted
        # set gets marked as pending deletion
        Item.objects.filter(
            pk__in=existing_item_ids - extracted_item_ids,
            #world_id=self.world.id,
            is_pending_deletion=False,
        ).update(
            is_pending_deletion=True,
            pending_deletion_ts=timezone.now())

        if not self.chunks.get('item'): return

        with transaction.atomic():

            # Get the existing data for all the extracted items
            extracted_items = Item.objects\
                                .select_for_update()\
                                .filter(pk__in=extracted_item_ids)

            # Create an index of the extracted items, with the id as they key
            extracted_items_map = {
                item.id: item for item in extracted_items
            }

            # Go through each chunk and compare. If a change needs to happen,
            # save it to the database.

            for chunk in self.chunks['item']:

                # If the container_id is not knowable, we shouldn't store
                # this item as it will cause an exception anyway. It is a
                # regrettable loss of item though, may make sense to log this
                # at some point. One scenario where this will happen regularly
                # is any loot in the corse of a mob.
                if not chunk['container_id']:
                    print('%s has no container id...' % chunk['name'])
                    continue

                try:
                    item = extracted_items_map[int(chunk['id'])]
                except (KeyError, TypeError):
                    continue

                augment_id = (
                    int(chunk['augment_id'])
                    if chunk.get('augment_id')
                    else None)

                # Only write if we need to
                if (item.container_type.model != chunk['container_type']
                    or item.container_id != int(chunk['container_id'])
                    or item.augment_id != augment_id
                    or item.is_pending_deletion):

                    item.container_type = ContentType.objects.get(
                        model=chunk['container_type'])
                    item.container_id = int(chunk['container_id'])
                    item.augment_id = augment_id
                    item.is_pending_deletion = False
                    item.save(update_fields=[
                        'container_type',
                        'container_id',
                        'is_pending_deletion',
                        'augment_id'])

                    if chunk.get('corpse_id'):
                        Mob.objects.filter(pk=chunk['corpse_id']).update(
                            is_pending_deletion=True,
                            pending_deletion_ts=timezone.now())

    # World component

    def save_mobs(self):
        # Get the IDs of the mobs that were extracted from the game
        extracted_mob_ids = set([
            int(chunk['id']) for chunk in self.chunks.get('mob', [])
        ])

        # If any of the mobs that were in the world were marked for deletion
        # (for example if a loader ran right before the extraction and the
        # mobs hadn't made it to the game yet), mark them as no longer
        # pending deletion.
        self.world.mobs.filter(
            is_pending_deletion=True,
            id__in=extracted_mob_ids
        ).update(is_pending_deletion=False)

        # Get the IDs of the mobs that the Forge is currently tracking as
        # being alive.
        existing_mob_ids = set(
            self.world.mobs.filter(
                is_pending_deletion=False
            ).values_list('id', flat=True))

        # Clean out missing mobs that are older than the deletion threshold
        threshold = timezone.now() - timedelta(
            seconds=api_consts.MOB_DELETION_DELAY)
        delete_mobs = Mob.objects.filter(
            pk__in=existing_mob_ids - extracted_mob_ids,
            created_ts__lt=threshold,
        )
        delete_mobs.update(
            is_pending_deletion=True,
            pending_deletion_ts=timezone.now())

        if not self.chunks.get('mob'): return

        with transaction.atomic():
            extracted_mobs = Mob.objects\
                                .select_for_update()\
                                .filter(pk__in=extracted_mob_ids)

            extracted_mobs_map = {
                mob.id: mob for mob in extracted_mobs
            }

            # Go through each chunk and compare, move the mobs we need to
            for chunk in self.chunks['mob']:
                try:
                    mob = extracted_mobs_map[int(chunk['id'])]
                except KeyError:
                    continue
                if mob.room_id != int(chunk['room']):
                    mob.room_id = int(chunk['room'])
                    mob.save(update_fields=['room_id'])

    # Single Player World component

    def save_command_checks(self):
        for chunk in self.chunks.get('room_cmd_check', []):
            room_cmd_check = RoomCommandCheck.objects.get(pk=chunk['id'])
            if room_cmd_check.track_state:
                try:
                    check_state = room_cmd_check.room_cmd_check_states.filter(
                        world=self.world).get()
                except RoomCommandCheckState.DoesNotExist:
                    check_state = RoomCommandCheckState.objects.create(
                        world=self.world,
                        cmd_check=room_cmd_check)
                if chunk['state'] == 'passed' and not check_state.passed_ts:
                    check_state.passed_ts = timezone.now()
                    check_state.save()

    def save_doors(self):
        for chunk in self.chunks.get('door', []):
            try:
                door = Door.objects.get(
                    from_room_id=chunk['room_id'],
                    direction=chunk['direction'])
                door_state = door.door_states.get(
                    world=self.world)
                if door_state.state != chunk['state']:
                    door_state.state = chunk['state']
                    door_state.save()
                # if door.current_state != chunk['state']:
                #     door.current_state = chunk['state']
                #     door.save()
            except Door.DoesNotExist:
                pass

    def save_facts(self):
        for chunk in self.chunks.get('facts', []):
            self.world.facts = json.dumps(chunk['facts'])
            self.world.save()

    # Multi Player World

    def save_mpw_data(self):
        for chunk in self.chunks.get('world_data', []):
            self.world.facts = json.dumps(chunk['facts'])
            self.world.save()
        self.save_mobs()

    # Player components

    def save_aliases(self, player):
        for chunk in self.chunks.get('aliases', []):
            aliases = chunk['aliases']
            seen_ids = set()
            for alias_name, alias_dict in aliases.items():
                if 'id' in alias_dict:
                    alias = Alias.objects.get(pk=alias_dict['id'])
                else:
                    alias = Alias(player=player)
                alias.match = alias_dict['match']
                alias.replacement = alias_dict['replacement']
                alias.save()
                seen_ids.add(alias.id)

            # Remove the aliases whose ID we haven't seen
            Alias.objects.filter(
                player=player
            ).exclude(id__in=seen_ids).delete()

    def save_trophy(self, player):
        for chunk in self.chunks.get('trophy', []):
            for mob_template_id, count in chunk['trophy'].items():
                existing = player.trophy_entries.filter(
                    mob_template_id=mob_template_id).count()
                delta = count - existing
                for i in range(0, delta):
                    PlayerTrophy.objects.create(
                        player=player,
                        mob_template_id=mob_template_id)

    def save_skills(self, player):
        for chunk in self.chunks.get('skills', []):
            # Procedss flex skills
            skill_codes_added = []
            for skill_number, skill_code in chunk['skills']['flex'].items():
                try:
                    skill_number = int(skill_number)
                except ValueError: continue

                if not skill_code: continue
                skill, created = PlayerFlexSkill.objects.get_or_create(
                    player=player,
                    number=skill_number)
                skill.code = skill_code
                skill.save()
                skill_codes_added.append(skill_code)
            # Remove entries for codes that were not seen
            PlayerFlexSkill.objects.filter(
                player=player
            ).exclude(code__in=skill_codes_added).delete()

            # Process feats
            feats_added = []
            for feat_number, feat_code in chunk['skills']['feat'].items():
                feat, created = PlayerFeat.objects.get_or_create(
                    player=player,
                    number=feat_number)
                feat.code = feat_code
                feat.save()
                feats_added.append(feat_code)
            # Clean up
            if feats_added:
                PlayerFeat.objects.filter(
                    player=player,
                ).exclude(code__in=feats_added).delete()

            # Process custom skills
            skills = json.loads(player.skills or "{}")
            skills['custom'] = chunk['skills'].get('custom', {})
            player.skills = json.dumps(skills)
            player.save(update_fields=['skills'])

    def save_marks(self, player):
        seen_marks = []
        for chunk in self.chunks.get('marks', []):
            marks = chunk['marks']
            for name, value in marks.items():
                name = name.lower()
                value = value.lower()
                try:
                    mark = Mark.objects.get(
                        player=player,
                        name=name)
                    if mark.value != value:
                        mark.value = value
                        mark.save()
                except Mark.DoesNotExist:
                    mark = Mark.objects.create(
                        player=player,
                        name=name,
                        value=value)
                seen_marks.append(name)

        # Remove all unseen marks
        player.marks.exclude(name__in=seen_marks).delete()

    def save_factions(self, player):
        if self.is_instance:
            context = self.base_context
        else:
            context = self.world.context

        for chunk in self.chunks.get('factions', []):
            factions = chunk['factions']
            for faction_code in factions.keys():
                if faction_code == 'core':
                    continue
                try:
                    faction = Faction.objects.get(
                        world=context,
                        is_core=False,
                        code=faction_code)
                except Faction.DoesNotExist:
                    continue

                try:
                    f_assignment = FactionAssignment.objects.get(
                        faction=faction,
                        member_type__model='player',
                        member_id=player.id)
                    f_assignment.value = factions[faction_code]
                    f_assignment.save()
                except FactionAssignment.DoesNotExist:
                    f_assignment = FactionAssignment.objects.create(
                        faction=faction,
                        member=player,
                        value=factions[faction_code])

    def save_viewed_rooms(self, player):
        # Get the rooms that the user already knew about
        # prior to this extraction
        viewed_ids = player.viewed_rooms.values_list(
            'id', flat=True)
        room_ids_to_add = [] # room IDs to add that we're looking for
        for chunk in self.chunks.get('viewed_rooms', []):
            for room_id in chunk['room_ids']:
                room_id = int(room_id)
                if room_id not in viewed_ids:
                    room_ids_to_add.append(room_id)
        if room_ids_to_add:
            rooms_qs = Room.objects.filter(pk__in=room_ids_to_add)
            with transaction.atomic():
                for room in rooms_qs:
                    player.viewed_rooms.add(room)


def extract_population(game_world=None):
    """
    Returns information about a game world that the API loaders will want
    to know about.


    The mobs portion is primarily meant to assist with the loading
    decisions the API will have to deal with, mainly how many sentinels &
    roamers to maintain in each area.

    Return data:
    {
        mobs: [
            {
                mob_id: <mob id>,
                mob_template_id: <template id>,
                mob_room_id: <room id>,
                mob_zone_id: <zone id>
            }
        ],
        counts: {
            <template1 id>: 2,
            <template2 id>: 3,
        },
        rooms: { # defaultdict
            <rule1 id>: {
                <template1 id>: [<mob1 id>, <mob2 id>]
            }
        },
        zones: { # defaultdict
            <rule1 id>: {
                <template1 id>: [<mob1 id>, <mob2 id>]
            }
        },
        rules: { # defaultdict
            <rule1 id>: {
                <template1 id>: [<mob1 id>, <mob2 id>]
            }
        },
        zone_data: {
            <zone1 id>: <zone1 data string>,
            <zone2 id>: <zone2 data string>
        }
    }
    """

    # variables we're going to populate below
    template_counts = {}
    rooms = collections.defaultdict(dict)
    zones = collections.defaultdict(dict)
    rules = collections.defaultdict(dict)
    mobs = []
    zone_data = {}

    return {
        'mobs': mobs,
        'counts': template_counts,
        'rooms': rooms,
        'zones': zones,
        'rules': rules,
        'zone_data': zone_data,
    }
