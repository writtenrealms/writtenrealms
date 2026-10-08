"""
Character pages outside the game.

Owners see the same character data the game client syncs (stats, equipment,
inventory, currencies) and can manage their gear while the character is out of
the game. Everyone else who can see the world gets the character's public
headlines.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from builders.models import WorldBuilder
from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.actions.items import (
    EquipAction,
    GetAction,
    PutAction,
    RemoveEquipmentAction,
)
from spawns.models import Item, Player
from spawns.state_payloads import (
    get_player_with_related,
    load_equipment_items,
    resolve_item_name,
    serialize_actor,
    serialize_world,
)

MAX_DESCRIPTION_LENGTH = 5000


def base_world_of(player):
    """The world a character belongs to in the lobby (not its spawn or instance run)."""
    context = player.world.context
    if context is None:
        return player.world
    return context.instance_of or context


def can_view_world(user, world):
    # Mirrors IsLobbyView for read access.
    if user.is_authenticated and user.is_staff:
        return True
    if world.is_public:
        return True
    if not user.is_authenticated:
        return False
    if world.author_id == user.pk:
        return True
    if WorldBuilder.objects.filter(world=world, user=user).exists():
        return True
    return Player.objects.filter(world__context=world, user=user).exists()


def delete_character(player):
    """Soft-delete a character, as the lobby has always done."""
    if player.in_game:
        raise ValidationError("Cannot delete a character who is in the game.")
    if player.instance_participations.filter(exited_at__isnull=True).exists():
        raise ValidationError("Cannot delete a character with live instances.")
    player.name = "%s%s" % (player.name, player.id)
    player.pending_deletion_ts = timezone.now()
    player.save(update_fields=['name', 'pending_deletion_ts', 'modified_ts'])


def _world_summary(world):
    config = world.config
    return {
        'id': world.id,
        'name': world.name,
        'small_background': getattr(config, 'small_background', '') or '',
        'large_background': getattr(config, 'large_background', '') or '',
    }


def _headlines(player, base_world):
    equipment = player.equipment
    worn = []
    if equipment:
        for slot in adv_consts.EQUIPMENT_SLOTS:
            item = getattr(equipment, slot, None)
            if item is not None:
                worn.append({'slot': slot, 'name': resolve_item_name(item), 'quality': item.quality})
    return {
        'id': player.id,
        'name': player.name,
        'title': player.title,
        'level': player.level,
        'archetype': player.archetype,
        'gender': player.gender,
        'core_faction': player.core_faction.name if player.core_faction_id else '',
        'description': player.description,
        'glory': player.glory,
        'is_builder': player.is_builder,
        'created_ts': player.created_ts,
        'world': _world_summary(base_world),
        'equipment': worn,
    }


def private_payload(player_id):
    player = get_player_with_related(player_id)
    base_world = base_world_of(player)
    return {
        'view': 'private',
        **_headlines(player, base_world),
        # When someone last played is only shown to the owner.
        'last_connection_ts': player.last_connection_ts,
        'in_game': player.in_game,
        'can_manage_items': not player.in_game,
        'can_transfer': player.world.lifecycle == adv_consts.WORLD_STATE_COMPLETE,
        'world_config': serialize_world(player.world),
        'character': serialize_actor(player, None).model_dump(mode='json'),
    }


class CharacterView(APIView):
    permission_classes = (permissions.AllowAny,)

    def get_player(self, pk, *, owner_only=False, lock=False):
        qs = Player.objects.filter(pending_deletion_ts__isnull=True).select_related(
            'world__context__instance_of__config', 'world__context__config',
            'world__config', 'core_faction', 'equipment', 'user',
        )
        if lock:
            qs = qs.select_for_update(of=('self',))
        player = qs.filter(pk=pk).first()
        if player is None:
            raise NotFound("Character not found.")
        is_owner = self.request.user.is_authenticated and player.user_id == self.request.user.pk
        if owner_only and not is_owner:
            # Don't reveal whether someone else's character exists.
            raise NotFound("Character not found.")
        return player, is_owner


class CharacterDetail(CharacterView):
    """GET a character page; PATCH or DELETE your own character."""

    def get(self, request, pk):
        player, is_owner = self.get_player(pk)
        if is_owner:
            return Response(private_payload(player.pk))
        base_world = base_world_of(player)
        hidden = player.is_invisible and not (request.user.is_authenticated and request.user.is_staff)
        if hidden or not can_view_world(request.user, base_world):
            raise NotFound("Character not found.")
        load_equipment_items([player])
        return Response({'view': 'public', **_headlines(player, base_world)})

    def patch(self, request, pk):
        player, _ = self.get_player(pk, owner_only=True)
        description = request.data.get('description')
        if not isinstance(description, str):
            raise ValidationError({'description': 'Must be text.'})
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError({'description': f'Keep it under {MAX_DESCRIPTION_LENGTH} characters.'})
        player.description = description.strip()
        player.save(update_fields=['description', 'modified_ts'])
        return Response(private_payload(player.pk))

    def delete(self, request, pk):
        with transaction.atomic():
            player, _ = self.get_player(pk, owner_only=True, lock=True)
            delete_character(player)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CharacterItems(CharacterView):
    """
    Move a character's items while they are out of the game: equip, remove,
    put into a bag they carry, or take out of one. Reuses the game's item
    actions, so the same equipment rules apply. Offline/ownership validation
    and item changes share the player lock; game event payloads are skipped.
    """

    ACTIONS = ('equip', 'remove', 'put', 'take')

    def post(self, request, pk):
        action = request.data.get('action')
        if action not in self.ACTIONS:
            raise ValidationError({'action': f'Must be one of {", ".join(self.ACTIONS)}.'})

        # Entering the game and item actions lock this same row. Keep the
        # offline and ownership checks valid until the item change commits.
        with transaction.atomic():
            player, _ = self.get_player(pk, owner_only=True, lock=True)
            if player.in_game:
                raise ValidationError({'detail': f'{player.name} is in the game. Manage items there.'})
            item = self._owned_item(player, request.data.get('item'))
            container = None
            if action in ('put', 'take'):
                container = self._owned_item(player, request.data.get('container'))
                if not self._carried(player, container) or container.type != adv_consts.ITEM_TYPE_CONTAINER:
                    raise ValidationError({'container': 'Choose a bag your character is carrying.'})

            try:
                if action == 'equip':
                    EquipAction().execute(player.pk, item.key, build_events=False)
                elif action == 'remove':
                    RemoveEquipmentAction().execute(player.pk, item.key, build_events=False)
                elif action == 'put':
                    PutAction().execute(player.pk, item.key, container.key, build_events=False)
                else:
                    GetAction().execute(player.pk, item.key, container.key, build_events=False)
            except ActionError as error:
                raise ValidationError({'detail': error.message})
        return Response(private_payload(player.pk))

    def _owned_item(self, player, key):
        prefix = 'item.'
        if not isinstance(key, str) or not key.startswith(prefix) or not key[len(prefix):].isdigit():
            raise ValidationError({'item': 'Unknown item.'})
        item = Item.objects.filter(pk=int(key[len(prefix):]), is_pending_deletion=False).first()
        if item is None or not self._belongs_to(player, item):
            raise ValidationError({'item': 'Unknown item.'})
        return item

    @staticmethod
    def _carried(player, item):
        return item.container_type and item.container_type.model == 'player' and item.container_id == player.pk

    def _belongs_to(self, player, item):
        if self._carried(player, item):
            return True
        model = item.container_type.model if item.container_type else None
        if model == 'equipment':
            return item.container_id == player.equipment_id
        if model == 'item':
            # One level down: the contents of a bag the character carries.
            bag = Item.objects.filter(pk=item.container_id).first()
            return bag is not None and self._carried(player, bag)
        return False
