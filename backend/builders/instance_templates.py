from __future__ import annotations

import copy

from django.core.exceptions import ValidationError
from django.db import transaction

from core.world_config import INSTANCE_INHERITED_CONFIG_FIELDS
from worlds.models import World, WorldConfig


_INSTANCE_CONFIG_LOCAL_ROOM_FIELDS = {
    "starting_room",
    "death_room",
    "exits_to",
}
_WORLD_CONFIG_CLONE_SKIP_FIELDS = {
    "id",
    "created_ts",
    "modified_ts",
    *_INSTANCE_CONFIG_LOCAL_ROOM_FIELDS,
    *INSTANCE_INHERITED_CONFIG_FIELDS,
    "death_routing_generation",
    "death_routing_source",
    "death_routing_source_generation",
}


def clone_world_config_for_instance(
    base_config: WorldConfig,
) -> WorldConfig:
    values = {}
    for field in WorldConfig._meta.fields:
        if (
            field.primary_key
            or field.name in _WORLD_CONFIG_CLONE_SKIP_FIELDS
        ):
            continue
        values[field.name] = copy.deepcopy(
            getattr(base_config, field.name)
        )
    return WorldConfig.objects.create(**values)


@transaction.atomic
def create_instance_template(
    *,
    base_world: World,
    author,
    name: str,
    instance_slug: str | None = None,
) -> World:
    if base_world.context_id or base_world.instance_of_id:
        raise ValidationError(
            "Instance templates must belong directly to an authored base "
            "world."
        )
    if not base_world.is_multiplayer:
        raise ValidationError(
            "Instance templates require a multiplayer base world."
        )
    if base_world.config_id:
        config = clone_world_config_for_instance(base_world.config)
    else:
        config = WorldConfig.objects.create()
    return World.objects.new_world(
        name=name,
        author=author,
        config=config,
        instance_of=base_world,
        instance_slug=instance_slug,
        is_multiplayer=True,
    )
