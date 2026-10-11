"""Read-only, batched source previews for an expanded builder spawn plan."""

from django.db.models import Q

from builders.models import ItemBundle, ItemDefinition, MobDefinition


MOB_PREVIEW_FIELDS = (
    'id', 'slug', 'name', 'mob_type', 'description', 'room_description',
    'base_properties', 'attributes', 'traits', 'randomization',
)


def serialize_spawn_plan_preview(plan, *, world):
    # The detail endpoint prefetches entries and joins their destinations.
    entries = list(plan.entries.all())
    source_rows = []
    references = set()
    for entry in entries:
        source = entry.source
        pool = source.get('pool', source.get('source_pool')) if isinstance(source, dict) else None
        choices = pool if pool is not None else [source]
        rows = []
        for choice in choices:
            ref = (choice.get('ref') or choice.get('source') or '') if isinstance(choice, dict) else choice
            ref = str(ref).strip()
            rows.append({'ref': ref, 'weight': choice.get('weight', 1) if isinstance(choice, dict) else 1})
            references.add(ref)
        source_rows.append((rows, pool is not None))

    # Fetch only referenced definitions, once per source kind. Instance plans
    # inherit their source library from the base world, just like the runtime.
    source_world_id = world.instance_of_id or world.id
    resolved = {}
    mobs = {}
    for prefix, model in (
        ('mobdefinition', MobDefinition),
        ('itemdefinition', ItemDefinition),
        ('itembundle', ItemBundle),
    ):
        requested = {}
        for ref in references:
            kind, _, value = ref.partition('.')
            if kind.lower().replace('_', '').replace('-', '') == prefix and value.strip():
                requested[ref] = value.strip()
        if not requested:
            continue
        ids = {int(value) for value in requested.values() if value.isdigit()}
        slugs = {value for value in requested.values() if not value.isdigit()}
        queryset = model.objects.filter(world_id=source_world_id).filter(Q(id__in=ids) | Q(slug__in=slugs))
        if model is MobDefinition:
            queryset = queryset.only(*MOB_PREVIEW_FIELDS)
        else:
            queryset = queryset.only('id', 'slug', 'name')
        definitions = list(queryset)
        by_id = {str(definition.id): definition for definition in definitions}
        by_slug = {definition.slug: definition for definition in definitions}
        for ref, value in requested.items():
            definition = (by_id if value.isdigit() else by_slug).get(value)
            if definition is not None:
                resolved[ref] = {
                    'name': definition.name,
                    'mob_id': definition.id if model is MobDefinition else None,
                }
        if model is MobDefinition:
            for definition in definitions:
                mob = {field: getattr(definition, field) for field in MOB_PREVIEW_FIELDS}
                mob['type'] = mob.pop('mob_type')
                mobs[definition.id] = mob

    payload = []
    for entry, (sources, pooled) in zip(entries, source_rows):
        if entry.target_room_id:
            target = f'room@{entry.target_room.relative_id}'
            target_name = entry.target_room.name
        elif entry.target_zone_id:
            target = f'zone@{entry.target_zone.relative_id}'
            target_name = entry.target_zone.name
        elif entry.target_path_id:
            target = f'path@{entry.target_path.relative_id}'
            target_name = entry.target_path.name
        else:
            target = f'entry.{entry.target_entry.slug}'
            target_name = entry.target_entry.name or entry.target_entry.slug
        payload.append({
            'slug': entry.slug,
            'name': entry.name,
            'is_active': entry.is_active,
            'conditional': bool(entry.conditions),
            'count': entry.count,
            'target': target,
            'target_name': target_name,
            'pooled': pooled,
            'sources': [{**source, **resolved.get(source['ref'], {})} for source in sources],
        })
    return {'entries': payload, 'mobs': mobs}
