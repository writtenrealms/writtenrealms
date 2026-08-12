from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import connection
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, QuerySet, Value, When

from builders.models import AbilityDefinition, TrainerProfileAbility
from core.abilities import ability_allows_actor, definition_world
from core.condition_dsl import (
    ConditionContext,
    evaluate_condition,
    validate_candidate_condition_payload,
)
from spawns.models import Mob, Player
from worlds.models import Room


TRAINER_PROVIDER_LIMIT = 100
TRAINER_PROFILE_ABILITY_LIMIT = 100
TRAINER_CATALOG_LIMIT = 100


@dataclass(frozen=True)
class TrainingProvider:
    type: str
    id: int
    key: str
    name: str
    profile_id: int
    profile_key: str
    profile_slug: str
    profile_name: str
    profile_learning: Any

    def payload(self, *, learning: dict[str, Any] | None = None) -> dict:
        payload = {
            "type": self.type,
            "id": self.id,
            "key": self.key,
            "name": self.name,
            "profile": {
                "id": self.profile_id,
                "key": self.profile_key,
                "slug": self.profile_slug,
                "name": self.profile_name,
            },
        }
        if learning is not None:
            payload["learning"] = learning
        return payload


def _room_provider(room: Room) -> TrainingProvider | None:
    profile = room.trainer_profile
    if profile is None:
        return None
    return TrainingProvider(
        type="room",
        id=room.id,
        key=room.key,
        name=profile.name or room.name,
        profile_id=profile.id,
        profile_key=profile.key,
        profile_slug=profile.slug,
        profile_name=profile.name,
        profile_learning=(
            profile.learning if profile.learning is not None else {}
        ),
    )


def _mob_provider(mob: Mob) -> TrainingProvider | None:
    definition = mob.definition
    profile = definition.trainer_profile if definition else None
    if profile is None or mob.is_pending_deletion:
        return None
    if definition.trainer_availability == "alive_and_present" and int(mob.health or 0) <= 0:
        return None
    return TrainingProvider(
        type="mob",
        id=mob.id,
        key=mob.key,
        name=mob.name,
        profile_id=profile.id,
        profile_key=profile.key,
        profile_slug=profile.slug,
        profile_name=profile.name,
        profile_learning=(
            profile.learning if profile.learning is not None else {}
        ),
    )


def _normalized_learning_policy(
    provider: TrainingProvider,
) -> tuple[Any, int | None, bool, bool]:
    learning = provider.profile_learning
    if not isinstance(learning, dict):
        return {}, None, True, False
    if not learning:
        return {}, None, False, True
    if set(learning) - {"conditions", "max_known"}:
        return {}, None, True, False
    conditions = learning.get("conditions", {})
    if conditions is None:
        conditions = {}
    try:
        validate_candidate_condition_payload(
            conditions,
            field_name="trainer learning conditions",
        )
    except ValueError:
        return conditions, None, True, False
    raw_max_known = learning.get("max_known")
    if isinstance(raw_max_known, str) and raw_max_known.strip().lower() == "uncapped":
        return conditions, None, True, True
    if (
        isinstance(raw_max_known, int)
        and not isinstance(raw_max_known, bool)
        and raw_max_known > 0
    ):
        return conditions, raw_max_known, True, True
    # Authored manifests cannot reach this state. Fail closed for learning so
    # malformed direct database writes cannot bypass an intended restriction.
    return conditions, None, True, False


def learning_statuses_for_providers(
    player: Player,
    providers: list[TrainingProvider],
    *,
    known_slugs: list[str] | tuple[str, ...] | set[str],
) -> dict[int, dict[str, Any]]:
    """Return one bounded, query-free-at-evaluation status per profile."""

    provider_by_profile: dict[int, TrainingProvider] = {}
    for provider in providers:
        provider_by_profile.setdefault(provider.profile_id, provider)
    if not provider_by_profile:
        return {}

    policies = {
        profile_id: _normalized_learning_policy(provider)
        for profile_id, provider in provider_by_profile.items()
    }
    capped_profile_ids = {
        profile_id
        for profile_id, (_conditions, max_known, _configured, valid)
        in policies.items()
        if valid and max_known is not None
    }

    known = {
        str(slug or "").strip().lower()
        for slug in known_slugs
        if str(slug or "").strip()
    }
    known_by_profile: dict[int, set[str]] = {}
    invalid_membership_profiles: set[int] = set()
    if known and capped_profile_ids:
        max_memberships = (
            len(capped_profile_ids) * TRAINER_PROFILE_ABILITY_LIMIT
        )
        memberships = list(TrainerProfileAbility.objects.filter(
            profile_id__in=capped_profile_ids,
        ).order_by(
            "profile_id",
            "order",
            "id",
        ).values_list(
            "profile_id",
            "ability__slug",
        )[:max_memberships + 1])
        if len(memberships) > max_memberships:
            # Valid authored data cannot overflow this global bound. Deny all
            # configured profiles rather than return an under-counted quota.
            invalid_membership_profiles.update(capped_profile_ids)
            memberships = memberships[:max_memberships]
        membership_counts: dict[int, int] = {}
        for profile_id, ability_slug in memberships:
            membership_counts[profile_id] = membership_counts.get(profile_id, 0) + 1
            if membership_counts[profile_id] > TRAINER_PROFILE_ABILITY_LIMIT:
                invalid_membership_profiles.add(profile_id)
            if ability_slug in known:
                known_by_profile.setdefault(profile_id, set()).add(ability_slug)

    context = ConditionContext(
        actor=player,
        player=player,
        world=getattr(player, "world", None),
    )
    statuses: dict[int, dict[str, Any]] = {}
    for profile_id, provider in provider_by_profile.items():
        conditions, max_known, configured, valid = policies[profile_id]
        valid = valid and profile_id not in invalid_membership_profiles
        try:
            condition_met = valid and evaluate_condition(
                conditions,
                context=context,
            )
        except (TypeError, ValueError, OverflowError):
            # Some structurally valid comparisons can still be nonsensical at
            # runtime (for example, actor.level >= "not-a-number"). Access
            # policies must deny rather than fail open or abort the command.
            valid = False
            condition_met = False
        known_count = len(known_by_profile.get(profile_id, set()))
        remaining = (
            None if max_known is None else max(0, max_known - known_count)
        )
        if not valid:
            status = "denied"
        elif not condition_met:
            status = "denied"
        elif max_known is not None and known_count >= max_known:
            status = "limit_reached"
        elif configured:
            status = "available"
        else:
            status = "unrestricted"
        statuses[profile_id] = {
            "status": status,
            "eligible": status in {"available", "unrestricted"},
            "max_known": max_known,
            "known": known_count,
            "remaining": remaining,
        }
        if not valid:
            statuses[profile_id]["reason"] = "invalid_policy"
    return statuses


def learning_status_payloads(
    providers: list[TrainingProvider],
    statuses: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_profile_ids: set[int] = set()
    for provider in providers:
        if provider.profile_id in seen_profile_ids:
            continue
        seen_profile_ids.add(provider.profile_id)
        result.append({
            "profile_id": provider.profile_id,
            "profile_key": provider.profile_key,
            "profile_slug": provider.profile_slug,
            "profile_name": provider.profile_name,
            **statuses[provider.profile_id],
        })
    return result


def learning_status_after_delta(
    status: dict[str, Any],
    delta: int,
) -> dict[str, Any]:
    updated = dict(status)
    max_known = updated.get("max_known")
    if max_known is None:
        updated["known"] = 0
        updated["remaining"] = None
        return updated
    known = max(0, int(updated.get("known") or 0) + int(delta))
    remaining = max(0, int(max_known) - known)
    if updated.get("status") != "denied":
        if max_known is not None and known >= int(max_known):
            status_name = "limit_reached"
        elif updated.get("status") == "unrestricted":
            status_name = "unrestricted"
        else:
            status_name = "available"
        updated["status"] = status_name
        updated["eligible"] = status_name in {"available", "unrestricted"}
    updated["known"] = known
    updated["remaining"] = remaining
    return updated


def discover_training_providers(
    player: Player,
) -> tuple[list[TrainingProvider], bool]:
    """Return bounded providers in deterministic room-first, then mob-id order."""

    if not player.room_id:
        return [], False
    source_world = definition_world(player.world)
    providers: list[TrainingProvider] = []

    room = (
        Room.objects.filter(
            pk=player.room_id,
            trainer_profile__world_id=source_world.id,
        )
        .select_related("trainer_profile")
        .first()
    )
    if room is not None:
        provider = _room_provider(room)
        if provider is not None:
            providers.append(provider)

    remaining = TRAINER_PROVIDER_LIMIT - len(providers)
    if remaining <= 0:
        return providers, True
    mobs = list(
        Mob.objects.filter(
            world_id=player.world_id,
            room_id=player.room_id,
            is_pending_deletion=False,
            definition__world_id=source_world.id,
            definition__trainer_profile__world_id=source_world.id,
        )
        .filter(
            ~Q(definition__trainer_availability="alive_and_present")
            | Q(health__gt=0)
        )
        .select_related("definition", "definition__trainer_profile")
        .order_by("id")[:remaining + 1]
    )
    truncated = len(mobs) > remaining
    providers.extend(
        provider
        for mob in mobs[:remaining]
        if (provider := _mob_provider(mob)) is not None
    )
    return providers, truncated


def available_training_providers(player: Player) -> list[TrainingProvider]:
    providers, _truncated = discover_training_providers(player)
    return providers


def _attached_profile_filter(*, source_world_id: int) -> Q:
    return (
        Q(profile__rooms__isnull=False)
        | Q(profile__mob_definitions__world_id=source_world_id)
    )


def ability_has_attached_trainer(world, ability: AbilityDefinition) -> bool:
    source_world = definition_world(world)
    return (
        TrainerProfileAbility.objects.filter(
            ability_id=ability.id,
            profile__world_id=source_world.id,
        )
        .filter(_attached_profile_filter(source_world_id=source_world.id))
        .exists()
    )


def with_attached_trainer_flag(
    queryset: QuerySet[AbilityDefinition],
    *,
    world,
) -> QuerySet[AbilityDefinition]:
    source_world = definition_world(world)
    memberships = (
        TrainerProfileAbility.objects.filter(
            ability_id=OuterRef("pk"),
            profile__world_id=source_world.id,
        )
        .filter(_attached_profile_filter(source_world_id=source_world.id))
    )
    return queryset.annotate(has_attached_trainer=Exists(memberships))


def providers_by_ability_id(
    providers: list[TrainingProvider],
    ability_ids: list[int] | tuple[int, ...] | set[int],
) -> dict[int, TrainingProvider]:
    """Map abilities to the first provider without per-provider queries."""

    bounded_ids = list(dict.fromkeys(int(value) for value in ability_ids))[
        :TRAINER_PROFILE_ABILITY_LIMIT
    ]
    if not providers or not bounded_ids:
        return {}
    profile_ids = list(dict.fromkeys(provider.profile_id for provider in providers))
    memberships = list(
        TrainerProfileAbility.objects.filter(
            profile_id__in=profile_ids,
            ability_id__in=bounded_ids,
        ).values_list("profile_id", "ability_id")
    )
    ability_ids_by_profile: dict[int, set[int]] = {}
    for profile_id, ability_id in memberships:
        ability_ids_by_profile.setdefault(profile_id, set()).add(ability_id)

    result: dict[int, TrainingProvider] = {}
    for provider in providers:
        for ability_id in ability_ids_by_profile.get(provider.profile_id, set()):
            result.setdefault(ability_id, provider)
    return result


def taught_abilities_for_providers(
    providers: list[TrainingProvider],
    *,
    eligible_profile_ids: set[int] | None = None,
    actor_type: str | None = None,
) -> tuple[list[tuple[AbilityDefinition, TrainingProvider]], bool]:
    """Load a hard-bounded curriculum batch and preserve provider/profile order."""

    if not providers:
        return [], False
    profile_ids = list(dict.fromkeys(
        provider.profile_id
        for provider in providers
        if eligible_profile_ids is None or provider.profile_id in eligible_profile_ids
    ))
    if not profile_ids:
        return [], False
    profile_order = Case(
        *[
            When(profile_id=profile_id, then=Value(index))
            for index, profile_id in enumerate(profile_ids)
        ],
        default=Value(len(profile_ids)),
        output_field=IntegerField(),
    )
    memberships_queryset = TrainerProfileAbility.objects.filter(
        profile_id__in=profile_ids,
        ability__is_active=True,
    )
    if actor_type is not None and connection.vendor == "postgresql":
        memberships_queryset = memberships_queryset.filter(
            Q(ability__availability__actors__contains=[actor_type])
            | ~Q(ability__availability__has_key="actors")
        )
    ordered_memberships = (
        memberships_queryset
        .select_related("ability")
        .annotate(provider_order=profile_order)
        .order_by("provider_order", "order", "id")
    )
    if connection.vendor == "postgresql":
        memberships = list(ordered_memberships[:TRAINER_CATALOG_LIMIT + 1])
    else:
        memberships = [
            membership
            for membership in ordered_memberships
            if actor_type is None
            or ability_allows_actor(membership.ability, actor_type)
        ][:TRAINER_CATALOG_LIMIT + 1]
    if actor_type is not None:
        memberships = [
            membership
            for membership in memberships
            if ability_allows_actor(membership.ability, actor_type)
        ]
    truncated = len(memberships) > TRAINER_CATALOG_LIMIT
    memberships = memberships[:TRAINER_CATALOG_LIMIT]
    entries_by_profile: dict[int, list[TrainerProfileAbility]] = {}
    for membership in memberships:
        entries_by_profile.setdefault(membership.profile_id, []).append(membership)

    result: list[tuple[AbilityDefinition, TrainingProvider]] = []
    seen_ability_ids: set[int] = set()
    for provider in providers:
        for membership in entries_by_profile.get(provider.profile_id, []):
            if membership.ability_id in seen_ability_ids:
                continue
            seen_ability_ids.add(membership.ability_id)
            result.append((membership.ability, provider))
    return result, truncated


def provider_for_ability_change(
    player: Player,
    ability: AbilityDefinition,
) -> TrainingProvider | None:
    providers = providers_for_ability_change(player, ability)
    return providers[0] if providers else None


def providers_for_ability_change(
    player: Player,
    ability: AbilityDefinition,
) -> list[TrainingProvider]:
    """Return exact local curriculum matches in deterministic provider order."""

    if not player.room_id:
        return []
    source_world = definition_world(player.world)
    providers: list[TrainingProvider] = []
    room = (
        Room.objects.filter(
            pk=player.room_id,
            trainer_profile__world_id=source_world.id,
            trainer_profile__ability_entries__ability_id=ability.id,
        )
        .select_related("trainer_profile")
        .first()
    )
    if room is not None:
        provider = _room_provider(room)
        if provider is not None:
            providers.append(provider)

    remaining = TRAINER_PROVIDER_LIMIT - len(providers)
    if remaining <= 0:
        return providers
    mobs = list(
        Mob.objects.filter(
            world_id=player.world_id,
            room_id=player.room_id,
            is_pending_deletion=False,
            definition__world_id=source_world.id,
            definition__trainer_profile__world_id=source_world.id,
            definition__trainer_profile__ability_entries__ability_id=ability.id,
        )
        .filter(
            ~Q(definition__trainer_availability="alive_and_present")
            | Q(health__gt=0)
        )
        .select_related("definition", "definition__trainer_profile")
        .order_by("id")
        [:remaining]
    )
    providers.extend(
        provider
        for mob in mobs
        if (provider := _mob_provider(mob)) is not None
    )
    return providers
