"""Runtime helpers for WR2 crafting catalogs and material balances."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from django.db.models import Prefetch

from builders.item_definitions import normalize_item_randomization
from builders.models import (
    CraftingIngredient,
    CraftingProfileRecipe,
    CraftingRecipe,
)
from config import constants as adv_consts
from core.abilities import definition_world
from core.condition_dsl import ConditionContext, evaluate_condition
from spawns.actions.base import ActionError
from spawns.models import Mob, Player, PlayerMaterialBalance


BUILTIN_RECIPE_FILTERS = (
    "armor",
    "weapons",
    "ready",
)

_ARTICLE_RE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_RECIPE_INDEX_RE = re.compile(r"^-?[0-9]+$")
_MAX_DATABASE_ID = (1 << 63) - 1


def _normalize_text(value: object) -> str:
    return " ".join(_TOKEN_RE.findall(str(value or "").lower()))


def _without_article(value: object) -> str:
    return _ARTICLE_RE.sub("", str(value or "").strip()).strip()


def _selector_tokens(value: object) -> set[str]:
    return set(_TOKEN_RE.findall(str(value or "").lower()))


def _bounded_unsigned_int(value: object, *, maximum: int) -> int | None:
    """Parse ASCII digits without crossing Python or database integer bounds."""
    raw = str(value or "")
    if not raw or not raw.isascii() or not raw.isdigit():
        return None
    canonical = raw.lstrip("0") or "0"
    maximum_text = str(maximum)
    if len(canonical) > len(maximum_text):
        return None
    if len(canonical) == len(maximum_text) and canonical > maximum_text:
        return None
    return int(canonical)


def _number(value):
    """Keep authored numeric values JSON-safe without needless .0 suffixes."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def split_provider_suffix(text: object) -> tuple[str, str | None]:
    """Split the last explicit `at`/`with` provider suffix candidate."""
    padded = f" {str(text or '').strip()} "
    lowered = padded.lower()
    candidates = []
    for marker in (" at ", " with "):
        index = lowered.rfind(marker)
        if index >= 0:
            candidates.append((index, marker))
    if not candidates:
        return str(text or "").strip(), None
    index, marker = max(candidates, key=lambda candidate: candidate[0])
    before = padded[:index].strip()
    after = padded[index + len(marker):].strip()
    return before, after or None


@dataclass(frozen=True)
class CraftingProvider:
    provider_type: str
    provider_id: int
    key: str
    name: str
    keywords: str
    profile: object

    def payload(self) -> dict:
        return {
            "type": self.provider_type,
            "id": self.provider_id,
            "key": self.key,
            "name": self.name,
        }

    def profile_payload(self) -> dict:
        return {
            "id": self.profile.id,
            "key": f"craftingprofile.{self.profile.id}",
            "slug": self.profile.slug,
            "name": self.profile.name,
        }


@dataclass(frozen=True)
class CraftingOffer:
    provider: CraftingProvider
    membership: CraftingProfileRecipe
    recipe: CraftingRecipe
    catalog_number: int


def _definition_source_world(player: Player):
    return definition_world(player.world)


def available_crafting_providers(player: Player) -> list[CraftingProvider]:
    """Return room and NPC providers visible in this player's runtime world."""
    if not player.room_id:
        return []

    source_world = _definition_source_world(player)
    providers: list[CraftingProvider] = []
    room = player.room
    room_profile = getattr(room, "crafting_profile", None)
    if room_profile and room_profile.world_id == source_world.id:
        providers.append(
            CraftingProvider(
                provider_type="room",
                provider_id=room.id,
                key=room.key,
                name=room_profile.name or room.name,
                keywords=" ".join(
                    part
                    for part in (
                        getattr(room_profile, "keywords", ""),
                        room.name,
                        "workshop forge",
                    )
                    if part
                ),
                profile=room_profile,
            )
        )

    # Authored rooms are shared by multiple spawned runtime worlds. The world
    # predicate is therefore required to keep one running copy's NPCs from
    # becoming providers in another copy.
    mobs = (
        Mob.objects.filter(
            room_id=room.id,
            world_id=player.world_id,
            is_pending_deletion=False,
            definition__crafting_profile__isnull=False,
            definition__crafting_profile__world_id=source_world.id,
        )
        .select_related("definition", "definition__crafting_profile")
        .order_by("id")
    )
    for mob in mobs:
        definition = mob.definition
        if (
            definition.crafting_availability == "alive_and_present"
            and int(mob.health or 0) <= 0
        ):
            continue
        profile = definition.crafting_profile
        providers.append(
            CraftingProvider(
                provider_type="mob",
                provider_id=mob.id,
                key=mob.key,
                name=mob.name or definition.name,
                keywords=" ".join(
                    part
                    for part in (
                        mob.keywords,
                        definition.keywords,
                        profile.keywords,
                        mob.name,
                    )
                    if part
                ),
                profile=profile,
            )
        )
    return providers


def _provider_matches(provider: CraftingProvider, selector: str) -> bool:
    normalized = str(selector or "").strip().lower()
    if not normalized:
        return False
    if normalized == provider.key.lower():
        return True
    normalized_text = _normalize_text(normalized)
    if normalized_text in {
        _normalize_text(provider.name),
        _normalize_text(provider.profile.name),
        _normalize_text(provider.profile.slug),
    }:
        return True
    tokens = _selector_tokens(provider.keywords)
    return bool(normalized_text) and _selector_tokens(normalized_text).issubset(tokens)


def resolve_crafting_provider(
    providers: list[CraftingProvider],
    selector: str | None,
) -> CraftingProvider:
    if not selector:
        if len(providers) == 1:
            return providers[0]
        if not providers:
            raise ActionError(
                "There is no workshop here.",
                code="no_crafting_provider",
            )
        raise ActionError(
            "Which workshop do you mean?",
            code="ambiguous_crafting_provider",
            data={"providers": [provider.payload() for provider in providers]},
        )

    matches = [
        provider for provider in providers
        if _provider_matches(provider, selector)
    ]
    if not matches:
        raise ActionError(
            "You don't see that workshop here.",
            code="crafting_provider_not_found",
        )
    if len(matches) > 1:
        raise ActionError(
            "Which workshop do you mean?",
            code="ambiguous_crafting_provider",
            data={"providers": [provider.payload() for provider in matches]},
        )
    return matches[0]


def crafting_offers(
    player: Player,
    *,
    provider_selector: str | None = None,
) -> tuple[list[CraftingProvider], list[CraftingOffer]]:
    available_providers = available_crafting_providers(player)
    if not available_providers:
        raise ActionError("There is no workshop here.", code="no_crafting_provider")
    selected_providers = available_providers
    if provider_selector:
        selected_providers = [
            resolve_crafting_provider(available_providers, provider_selector)
        ]

    # Load every local provider's memberships once so recipe ordinals remain
    # canonical when a command narrows the view to one provider.
    profile_ids = sorted({provider.profile.id for provider in available_providers})
    ingredient_qs = CraftingIngredient.objects.select_related("material").order_by(
        "material__order",
        "material__name",
        "material_id",
    )
    memberships = list(
        CraftingProfileRecipe.objects.filter(profile_id__in=profile_ids)
        .select_related(
            "profile",
            "recipe",
            "recipe__world",
            "recipe__output_item_definition",
        )
        .prefetch_related(
            Prefetch("recipe__ingredients", queryset=ingredient_qs),
        )
        .order_by("profile_id", "order", "id")
    )
    memberships_by_profile: dict[int, list[CraftingProfileRecipe]] = {}
    for membership in memberships:
        memberships_by_profile.setdefault(membership.profile_id, []).append(membership)

    all_offers: list[CraftingOffer] = []
    catalog_numbers: dict[int, int] = {}
    for provider in available_providers:
        for membership in memberships_by_profile.get(provider.profile.id, []):
            catalog_number = catalog_numbers.setdefault(
                membership.recipe_id,
                len(catalog_numbers) + 1,
            )
            all_offers.append(
                CraftingOffer(
                    provider=provider,
                    membership=membership,
                    recipe=membership.recipe,
                    catalog_number=catalog_number,
                )
            )
    selected_provider_keys = {
        (provider.provider_type, provider.provider_id)
        for provider in selected_providers
    }
    offers = [
        offer for offer in all_offers
        if (offer.provider.provider_type, offer.provider.provider_id)
        in selected_provider_keys
    ]
    return selected_providers, offers


def _recipe_exact_keys(recipe: CraftingRecipe) -> set[str]:
    output = recipe.output_item_definition
    return {
        _normalize_text(recipe.slug),
        _normalize_text(str(recipe.slug).replace("-", " ")),
        _normalize_text(output.slug),
        _normalize_text(str(output.slug).replace("-", " ")),
        _normalize_text(output.name),
        _normalize_text(_without_article(output.name)),
    }


def _recipe_tokens(recipe: CraftingRecipe) -> set[str]:
    output = recipe.output_item_definition
    tokens = set()
    for value in (
        recipe.slug,
        str(recipe.slug).replace("-", " "),
        output.slug,
        str(output.slug).replace("-", " "),
        output.name,
        _without_article(output.name),
        output.keywords,
        recipe.group,
    ):
        tokens.update(_selector_tokens(value))
    return tokens


def _ordered_numbered_recipes(
    offers: Iterable[CraftingOffer],
) -> list[tuple[int, CraftingRecipe]]:
    recipes: dict[int, tuple[int, CraftingRecipe]] = {}
    for offer in offers:
        recipes.setdefault(
            offer.recipe.id,
            (offer.catalog_number, offer.recipe),
        )
    return sorted(recipes.values(), key=lambda entry: entry[0])


def _recipe_index_not_found(number: int | None = None) -> ActionError:
    label = f" #{number}" if number is not None else ""
    return ActionError(
        f"There is no recipe{label}. Type recipes to list your options.",
        code="recipe_index_not_found",
        data={"number": number} if number is not None else {},
    )


def resolve_recipe(
    offers: Iterable[CraftingOffer],
    selector: str | None,
) -> tuple[CraftingRecipe, list[CraftingOffer]]:
    normalized_raw = str(selector or "").strip().lower()
    if not normalized_raw:
        raise ActionError("Which item do you want to craft?", code="missing_recipe")

    offer_list = list(offers)
    numbered_recipes = _ordered_numbered_recipes(offer_list)
    ordered_recipes = [recipe for _number, recipe in numbered_recipes]
    unique_recipes = {recipe.id: recipe for recipe in ordered_recipes}

    explicit_id = None
    if normalized_raw.startswith("craftingrecipe."):
        suffix = normalized_raw.split(".", 1)[1]
        if suffix.isascii() and suffix.isdigit():
            explicit_id = _bounded_unsigned_int(
                suffix,
                maximum=_MAX_DATABASE_ID,
            )
            if explicit_id is None:
                raise ActionError(
                    "That recipe is not offered here.",
                    code="recipe_not_found",
                )
        else:
            normalized_raw = suffix
    if explicit_id is not None:
        recipe = unique_recipes.get(explicit_id)
        if recipe:
            return recipe, [offer for offer in offer_list if offer.recipe.id == recipe.id]
        raise ActionError("That recipe is not offered here.", code="recipe_not_found")

    if _RECIPE_INDEX_RE.fullmatch(normalized_raw):
        if normalized_raw.startswith("-"):
            magnitude = _bounded_unsigned_int(
                normalized_raw[1:],
                maximum=_MAX_DATABASE_ID,
            )
            raise _recipe_index_not_found(
                -magnitude if magnitude is not None else None
            )
        maximum_number = max(
            (number for number, _recipe in numbered_recipes),
            default=0,
        )
        index = _bounded_unsigned_int(
            normalized_raw,
            maximum=maximum_number,
        )
        if index is None or index < 1:
            display_number = _bounded_unsigned_int(
                normalized_raw,
                maximum=_MAX_DATABASE_ID,
            )
            raise _recipe_index_not_found(display_number)
        recipe = next(
            (
                candidate
                for number, candidate in numbered_recipes
                if number == index
            ),
            None,
        )
        if recipe is None:
            raise _recipe_index_not_found(index)
        return recipe, [offer for offer in offer_list if offer.recipe.id == recipe.id]

    normalized = _normalize_text(normalized_raw)
    exact = [
        recipe for recipe in unique_recipes.values()
        if normalized in _recipe_exact_keys(recipe)
    ]
    if len(exact) == 1:
        recipe = exact[0]
        return recipe, [offer for offer in offer_list if offer.recipe.id == recipe.id]
    if len(exact) > 1:
        matches = exact
    else:
        selector_tokens = _selector_tokens(normalized)
        matches = [
            recipe for recipe in unique_recipes.values()
            if selector_tokens and selector_tokens.issubset(_recipe_tokens(recipe))
        ]

    if not matches:
        raise ActionError("That recipe is not offered here.", code="recipe_not_found")
    if len(matches) > 1:
        raise ActionError(
            "That recipe name is ambiguous.",
            code="ambiguous_recipe",
            data={
                "recipes": [
                    {
                        "id": recipe.id,
                        "key": f"craftingrecipe.{recipe.id}",
                        "slug": recipe.slug,
                        "name": recipe.output_item_definition.name,
                    }
                    for recipe in matches
                ]
            },
        )
    recipe = matches[0]
    return recipe, [offer for offer in offer_list if offer.recipe.id == recipe.id]


def resolve_recipe_with_provider_suffix(
    providers: list[CraftingProvider],
    offers: Iterable[CraftingOffer],
    selector: str | None,
    *,
    allow_provider_suffix: bool,
) -> tuple[CraftingRecipe, list[CraftingOffer]]:
    """Resolve a full recipe name before interpreting a text provider suffix."""
    offer_list = list(offers)
    try:
        return resolve_recipe(offer_list, selector)
    except ActionError as error:
        if not allow_provider_suffix or error.code != "recipe_not_found":
            raise
        recipe_selector, provider_selector = split_provider_suffix(selector)
        if not recipe_selector or not provider_selector:
            raise
        provider = resolve_crafting_provider(providers, provider_selector)
        provider_key = (provider.provider_type, provider.provider_id)
        provider_offers = [
            offer for offer in offer_list
            if (offer.provider.provider_type, offer.provider.provider_id)
            == provider_key
        ]
        return resolve_recipe(provider_offers, recipe_selector)


def resolve_recipe_provider(
    matching_offers: list[CraftingOffer],
) -> CraftingOffer:
    providers: dict[tuple[str, int], CraftingOffer] = {}
    for offer in matching_offers:
        key = (offer.provider.provider_type, offer.provider.provider_id)
        providers.setdefault(key, offer)
    if not providers:
        raise ActionError("That recipe is not offered here.", code="recipe_not_found")
    if len(providers) > 1:
        raise ActionError(
            "More than one workshop offers that recipe. Specify where to craft it.",
            code="ambiguous_crafting_provider",
            data={
                "providers": [offer.provider.payload() for offer in providers.values()],
            },
        )
    return next(iter(providers.values()))


def recipe_providers(
    matching_offers: Iterable[CraftingOffer],
) -> list[CraftingProvider]:
    providers: dict[tuple[str, int], CraftingProvider] = {}
    for offer in matching_offers:
        key = (offer.provider.provider_type, offer.provider.provider_id)
        providers.setdefault(key, offer.provider)
    return list(providers.values())


def list_material_balances(player: Player) -> list[dict]:
    source_world = _definition_source_world(player)
    balances = (
        PlayerMaterialBalance.objects.filter(
            player_id=player.id,
            material__world_id=source_world.id,
            quantity__gt=0,
        )
        .select_related("material")
        .order_by("material__order", "material__name", "material_id")
    )
    return [
        {
            "id": balance.material_id,
            "key": f"craftmaterial.{balance.material_id}",
            "slug": balance.material.slug,
            "name": balance.material.name,
            "description": balance.material.description or "",
            "order": int(balance.material.order or 0),
            "quantity": int(balance.quantity or 0),
        }
        for balance in balances
    ]


def material_balance_map(player: Player) -> dict[int, int]:
    source_world = _definition_source_world(player)
    return {
        material_id: int(quantity or 0)
        for material_id, quantity in PlayerMaterialBalance.objects.filter(
            player_id=player.id,
            material__world_id=source_world.id,
        ).values_list("material_id", "quantity")
    }


def item_definition_preview(definition) -> dict:
    base = definition.base_properties or {}
    fixed_attributes = {
        str(key): value
        for key, value in (definition.attributes or {}).items()
    }
    ranges = {
        key: {"min": _number(value), "max": _number(value)}
        for key, value in fixed_attributes.items()
    }
    randomization = normalize_item_randomization(definition.randomization or {})
    for entry in randomization.get("attributes", []):
        key = entry["key"]
        fixed = fixed_attributes.get(key, 0)
        ranges[key] = {
            "min": _number(fixed + entry["min"]),
            "max": _number(fixed + entry["max"]),
            "mode": entry.get("mode", "uniform"),
        }

    return {
        "definition_id": definition.id,
        "definition_slug": definition.slug,
        "name": definition.name,
        "description": definition.description or "",
        "type": definition.item_type,
        "level": int(base.get("level") or 1),
        "quality": base.get("quality") or adv_consts.ITEM_QUALITY_NORMAL,
        "equipment_type": base.get("equipment_type") or "",
        "armor_class": base.get("armor_class") or "",
        "weapon_type": base.get("weapon_type") or "",
        "weapon_damage": _number(base.get("weapon_damage") or 0),
        "armor": _number(base.get("armor") or 0),
        "attributes": ranges,
    }


def recipe_condition_met(player: Player, recipe: CraftingRecipe) -> bool:
    return evaluate_condition(
        recipe.conditions,
        context=ConditionContext(
            actor=player,
            player=player,
            room=player.room,
            zone=getattr(player.room, "zone", None),
            world=player.world,
            template=recipe,
        ),
    )


def recipe_payload(
    player: Player,
    recipe: CraftingRecipe,
    *,
    providers: Iterable[CraftingProvider],
    balances: dict[int, int] | None = None,
) -> dict:
    balances = balances if balances is not None else material_balance_map(player)
    inputs = []
    total_missing = 0
    for ingredient in recipe.ingredients.all():
        owned = int(balances.get(ingredient.material_id, 0))
        required = int(ingredient.quantity)
        missing = max(0, required - owned)
        total_missing += missing
        inputs.append(
            {
                "material": {
                    "id": ingredient.material_id,
                    "key": f"craftmaterial.{ingredient.material_id}",
                    "slug": ingredient.material.slug,
                    "name": ingredient.material.name,
                },
                "owned": owned,
                "required": required,
                "missing": missing,
            }
        )

    conditions_met = recipe_condition_met(player, recipe)
    provider_payloads = [provider.payload() for provider in providers]
    output = item_definition_preview(recipe.output_item_definition)
    return {
        "id": recipe.id,
        "key": f"craftingrecipe.{recipe.id}",
        "slug": recipe.slug,
        "name": recipe.output_item_definition.name,
        "group": recipe.group,
        "order": int(recipe.order or 0),
        "output": output,
        "inputs": inputs,
        "conditions_met": conditions_met,
        "failure_message": recipe.failure_message or "",
        "missing": total_missing,
        "ready": conditions_met and total_missing == 0,
        "providers": provider_payloads,
        "provider": provider_payloads[0] if len(provider_payloads) == 1 else None,
    }


def recipe_matches_filter(recipe_data: dict, filter_name: str | None) -> bool:
    if not filter_name:
        return True
    equipment_type = (recipe_data.get("output") or {}).get("equipment_type") or ""
    if filter_name == "armor":
        return equipment_type in adv_consts.EQUIPMENT_ARMOR
    if filter_name == "weapons":
        return equipment_type in {
            adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
            adv_consts.EQUIPMENT_TYPE_WEAPON_2H,
            adv_consts.EQUIPMENT_TYPE_SHIELD,
        }
    if filter_name == "ready":
        return bool(recipe_data.get("ready"))
    return str(recipe_data.get("group") or "").strip().lower() == filter_name


def recipe_filters(offers: Iterable[CraftingOffer]) -> tuple[str, ...]:
    groups = []
    for _number, recipe in _ordered_numbered_recipes(offers):
        group = str(recipe.group or "").strip().lower()
        if group and group not in groups and group not in BUILTIN_RECIPE_FILTERS:
            groups.append(group)
    return tuple(groups) + BUILTIN_RECIPE_FILTERS


def ordered_recipe_payloads(
    player: Player,
    offers: Iterable[CraftingOffer],
    *,
    filter_name: str | None = None,
) -> list[dict]:
    offer_list = list(offers)
    balances = material_balance_map(player)
    providers_by_recipe: dict[int, list[CraftingProvider]] = {}
    for offer in offer_list:
        providers_by_recipe.setdefault(offer.recipe.id, [])
        provider_key = (offer.provider.provider_type, offer.provider.provider_id)
        if all(
            (provider.provider_type, provider.provider_id) != provider_key
            for provider in providers_by_recipe[offer.recipe.id]
        ):
            providers_by_recipe[offer.recipe.id].append(offer.provider)

    payloads = []
    for number, recipe in _ordered_numbered_recipes(offer_list):
        # Numbers are assigned from the complete local catalog above. Cheap
        # presentation filters can then reject a recipe before evaluating its
        # conditions or assembling ingredient/output payloads. The `ready`
        # filter necessarily needs that full runtime evaluation.
        if filter_name and filter_name != "ready":
            static_recipe_data = {
                "group": recipe.group,
                "output": {
                    "equipment_type": (
                        recipe.output_item_definition.base_properties or {}
                    ).get("equipment_type") or "",
                },
            }
            if not recipe_matches_filter(static_recipe_data, filter_name):
                continue
        payload = recipe_payload(
            player,
            recipe,
            providers=providers_by_recipe[recipe.id],
            balances=balances,
        )
        payload["number"] = number
        if filter_name != "ready" or recipe_matches_filter(payload, filter_name):
            payloads.append(payload)
    return payloads


def aggregate_material_payload(
    material_amounts: dict[int, int],
    *,
    materials_by_id: dict[int, object],
) -> list[dict]:
    entries = []
    for material_id, quantity in material_amounts.items():
        material = materials_by_id[material_id]
        entries.append(
            {
                "material": {
                    "id": material.id,
                    "key": f"craftmaterial.{material.id}",
                    "slug": material.slug,
                    "name": material.name,
                },
                "quantity": int(quantity),
            }
        )
    entries.sort(
        key=lambda entry: (
            int(materials_by_id[entry["material"]["id"]].order or 0),
            entry["material"]["name"],
            entry["material"]["id"],
        )
    )
    return entries
