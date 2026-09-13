"""Batched deletion policies for retained runtime records."""


def deactivate_combat_actor(collector, field, sub_objs, using):
    """Retain combat history without leaving an active member with no actor."""
    # Django executes these collected updates in insertion order, within the
    # deletion transaction. Deactivate before SET_NULL to satisfy the actor
    # check even when the participant will also be cascade-deleted afterward.
    # Collector combines batches, so this adds no per-actor signal or query.
    collector.add_field_update(
        field.model._meta.get_field('is_active'), False, sub_objs,
    )
    collector.add_field_update(field, None, sub_objs)


deactivate_combat_actor.lazy_sub_objs = True
