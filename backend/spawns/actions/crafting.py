"""Player actions for WR2 crafting materials, recipes, crafting, and salvage."""

from __future__ import annotations

from copy import deepcopy
import json
import re
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Exists, OuterRef

from builders.item_definitions import spawn_item_from_definition
from builders.models import ItemSalvageYield
from config import constants as adv_consts
from core.economy import money_payload
from spawns.actions.base import ActionError, ActionResult
from spawns.crafting import (
    aggregate_material_payload,
    crafting_offers,
    list_material_balances,
    ordered_recipe_payloads,
    recipe_condition_met,
    recipe_filters,
    recipe_payload,
    recipe_providers,
    resolve_recipe_provider,
    resolve_recipe_with_provider_suffix,
)
from spawns.events import GameEvent, enqueue_game_events
from spawns.models import (
    CraftingActionReceipt,
    Item,
    Player,
    PlayerCurrencyBalance,
    PlayerMaterialBalance,
)
from spawns.request_segments import normalize_request_segment
from spawns.wallet import WalletError, mutate_balances


MAX_SPOILS_PER_COMMAND = 100
MAX_SALVAGE_LIST_ITEMS = 100

_ARTICLE_RE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_COUNTED_SELECTOR_RE = re.compile(r"^(?P<count>[0-9]+)\.(?P<token>.+)$")
_ITEM_KEY_RE = re.compile(r"^item\.(?P<id>[0-9]+)$", re.IGNORECASE)
_SALVAGE_INDEX_RE = re.compile(r"^-?[0-9]+$")
_MAX_DATABASE_ID = (1 << 63) - 1


# State payloads import trigger plumbing, which imports the handler package.
# Keep these behind call-time wrappers so registering the crafting handler does
# not create a state_payloads -> handlers -> crafting -> state_payloads cycle.
def get_player_with_related(*args, **kwargs):
    from spawns.state_payloads import get_player_with_related as implementation

    return implementation(*args, **kwargs)


def resolve_item_name(*args, **kwargs):
    from spawns.state_payloads import resolve_item_name as implementation

    return implementation(*args, **kwargs)


def serialize_actor(*args, **kwargs):
    from spawns.state_payloads import serialize_actor as implementation

    return implementation(*args, **kwargs)


def serialize_item(*args, **kwargs):
    from spawns.state_payloads import serialize_item as implementation

    return implementation(*args, **kwargs)


def _json_safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _request_uuid(value) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _request_segment(value) -> str:
    return normalize_request_segment(value)


def _existing_receipt(
    *,
    player: Player,
    request_id,
    request_segment,
    action: str,
) -> CraftingActionReceipt | None:
    parsed_request_id = _request_uuid(request_id)
    if parsed_request_id is None:
        return None
    segment = _request_segment(request_segment)
    receipt = CraftingActionReceipt.objects.filter(
        player=player,
        request_id=parsed_request_id,
        segment=segment,
    ).first()
    if receipt is not None and receipt.action != action:
        raise ActionError(
            "That request path was already committed as a different action.",
            code="idempotency_conflict",
            data={
                "original_action": receipt.action,
                "requested_action": action,
                "request_segment": segment,
            },
        )
    return receipt


def _store_receipt(
    *,
    player: Player,
    request_id,
    request_segment,
    action: str,
    result: dict,
) -> None:
    parsed_request_id = _request_uuid(request_id)
    if parsed_request_id is None:
        return
    CraftingActionReceipt.objects.create(
        player=player,
        request_id=parsed_request_id,
        segment=_request_segment(request_segment),
        action=action,
        result=_json_safe(result),
    )


def _player_queryset():
    return Player.objects.select_related(
        "world",
        "world__context",
        "world__context__instance_of",
        "room",
        "room__zone",
        "room__crafting_profile",
    )


def _lock_player_with_related(player_id: int) -> Player:
    # Lock only the concrete player table. PostgreSQL rejects FOR UPDATE when
    # select_related introduces nullable outer joins (room/profile/context).
    locked = Player.objects.select_for_update().get(pk=player_id)
    return _player_queryset().get(pk=locked.pk)


def _material_phrase(entries: list[dict], *, quantity_key: str = "quantity") -> str:
    parts = []
    for entry in entries:
        material = entry.get("material") or {}
        parts.append(f"{int(entry.get(quantity_key) or 0)} {material.get('name') or 'material'}")
    if not parts:
        return "nothing"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _materials_text(data: dict) -> str:
    materials = data.get("materials") or []
    if not materials:
        return "You have no crafting materials."
    lines = ["Crafting materials:"]
    lines.extend(
        f"  {entry.get('name') or 'Material'}  {int(entry.get('quantity') or 0)}"
        for entry in materials
    )
    return "\n".join(lines)


def _missing_text(recipe: dict) -> str:
    missing = [entry for entry in recipe.get("inputs") or [] if entry.get("missing")]
    if not missing:
        return ""
    return _material_phrase(missing, quantity_key="missing")


def _missing_requirements_text(recipe: dict) -> str:
    parts = []
    material_text = _missing_text(recipe)
    if material_text:
        parts.append(material_text)
    currency_missing = int(recipe.get("currency_missing") or 0)
    cost = recipe.get("cost") or {}
    if currency_missing > 0 and cost:
        parts.append(
            recipe.get("currency_missing_display")
            or f"{currency_missing} {cost.get('currency') or 'currency'}"
        )
    if not parts:
        return "requirements"
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _recipes_text(data: dict) -> str:
    recipes = data.get("recipes") or []
    providers = data.get("providers") or []
    provider_name = providers[0].get("name") if len(providers) == 1 else "local workshops"
    if not recipes:
        if len(providers) == 1:
            return f"{provider_name or 'The workshop'} offers no matching recipes."
        return "Local workshops offer no matching recipes."
    heading = f"Recipes at {provider_name or 'the workshop'}:"
    lines = [heading]
    for fallback_number, recipe in enumerate(recipes, start=1):
        if not recipe.get("conditions_met"):
            status = "locked"
        elif recipe.get("ready"):
            status = "ready"
        else:
            status = f"need {_missing_requirements_text(recipe)}"
        number = int(recipe.get("number") or fallback_number)
        fee = (recipe.get("cost") or {}).get("display")
        fee_text = f"; fee {fee}" if fee else ""
        lines.append(f"{number}. {recipe['name']}  {status}{fee_text}")
    lines.append("Use: recipe <number> to inspect; craft <number> to make.")
    return "\n".join(lines)


def _recipe_text(data: dict) -> str:
    recipe = data["recipe"]
    output = recipe.get("output") or {}
    lines = [output.get("name") or recipe.get("name") or "Recipe"]
    equipment_type = str(output.get("equipment_type") or "").replace("_", " ")
    armor_class = str(output.get("armor_class") or "")
    if equipment_type:
        label = f"{armor_class} {equipment_type}".strip()
        lines.append(label.capitalize())
    if output.get("armor"):
        lines.append(f"Armor: {output['armor']}")
    if output.get("weapon_damage"):
        lines.append(f"Weapon damage: {output['weapon_damage']}")
    for key, value_range in (output.get("attributes") or {}).items():
        minimum = value_range.get("min", 0)
        maximum = value_range.get("max", minimum)
        value_text = str(minimum) if minimum == maximum else f"{minimum}-{maximum}"
        lines.append(f"{key.replace('_', ' ').title()}: {value_text}")
    if recipe.get("inputs"):
        lines.append("")
    for entry in recipe.get("inputs") or []:
        material_name = (entry.get("material") or {}).get("name") or "Material"
        lines.append(f"{material_name}: {entry.get('owned', 0)} / {entry.get('required', 0)}")
    cost = recipe.get("cost") or {}
    if cost:
        lines.append(f"Cost: {cost.get('display') or cost.get('amount')}")
        _amount, separator, unit = str(cost.get("display") or "").partition(" ")
        currency_label = unit if separator and unit else cost.get("currency") or "Funds"
        lines.append(
            f"{currency_label}: {int(recipe.get('currency_owned') or 0)} / "
            f"{int(cost.get('amount') or 0)}"
        )
    if not recipe.get("conditions_met"):
        lines.append(recipe.get("failure_message") or "You do not meet this recipe's requirements.")
    elif recipe.get("missing") or recipe.get("currency_missing"):
        lines.append(f"Missing: {_missing_requirements_text(recipe)}")
    return "\n".join(lines)


def _craft_text(data: dict) -> str:
    item = data.get("item") or {}
    if data.get("replayed"):
        lines = [f"Craft already completed: {item.get('name') or 'an item'}."]
    else:
        consumed = _material_phrase(data.get("consumed") or [])
        lines = [f"You spend {consumed}."]
        cost = data.get("cost") or {}
        if int(cost.get("amount") or 0) > 0:
            lines.append(f"You pay {cost.get('display') or cost.get('amount')}.")
        lines.append(f"You craft {item.get('name') or 'an item'}.")
    rolled = (data.get("roll") or {}).get("attributes") or {}
    if rolled:
        roll_text = ", ".join(
            f"{str(key).replace('_', ' ').title()} {value}"
            for key, value in sorted(rolled.items())
        )
        lines.append(f"Roll: {roll_text}.")
    return "\n".join(lines)


def _salvage_text(data: dict) -> str:
    items = data.get("items") or []
    if data.get("replayed"):
        item_text = (
            items[0].get("name") or "an item"
            if len(items) == 1
            else f"{len(items)} captured items"
        )
        return (
            f"Salvage already completed: {item_text}; "
            f"recovered {_material_phrase(data.get('yielded') or [])}."
        )
    if len(items) == 1:
        item_name = items[0].get("name") or "an item"
        lines = [f"You salvage {item_name}."]
    else:
        lines = [f"You salvage {len(items)} captured items."]
    lines.append(f"You recover {_material_phrase(data.get('yielded') or [])}.")
    remaining = int(data.get("remaining_spoils") or 0)
    if remaining:
        lines.append(f"You have {remaining} more captured items to salvage.")
    return "\n".join(lines)


def _salvage_items_text(data: dict) -> str:
    items = data.get("items") or []
    if not items:
        return "You have nothing you can salvage."
    lines = ["You can salvage:"]
    lines.extend(
        f"{int(item.get('number') or 0)}. {item.get('name') or 'an item'}"
        for item in items
    )
    if data.get("truncated"):
        lines.append(f"Only the first {MAX_SALVAGE_LIST_ITEMS} items are shown.")
    lines.append("Use: salvage <number>")
    return "\n".join(lines)


class ListMaterialsAction:
    def execute(self, player_id: int) -> ActionResult:
        player = _player_queryset().get(pk=player_id)
        data = {"materials": list_material_balances(player)}
        return ActionResult(
            data=data,
            events=[
                GameEvent(
                    type="cmd.materials.success",
                    recipients=[player.key],
                    data=data,
                    text=_materials_text(data),
                )
            ],
        )


class ListSalvageItemsAction:
    def execute(self, player_id: int) -> ActionResult:
        player = Player.objects.only("id").get(pk=player_id)
        candidates = list(
            _salvage_candidate_queryset(player)[:MAX_SALVAGE_LIST_ITEMS + 1]
        )
        truncated = len(candidates) > MAX_SALVAGE_LIST_ITEMS
        items = candidates[:MAX_SALVAGE_LIST_ITEMS]
        item_payloads = []
        for index, item in enumerate(items, start=1):
            item_payload = serialize_item(item).model_dump()
            item_payload.update(
                {
                    "number": index,
                    "salvage_only": bool(item.definition.salvage_only),
                }
            )
            item_payloads.append(item_payload)
        data = {
            "operation": "list",
            "items": item_payloads,
            "truncated": truncated,
            "limit": MAX_SALVAGE_LIST_ITEMS,
        }
        data["count"] = len(data["items"])
        data = _json_safe(data)
        return ActionResult(
            data=data,
            events=[
                GameEvent(
                    type="cmd.salvage.success",
                    recipients=[player.key],
                    data=data,
                    text=_salvage_items_text(data),
                )
            ],
        )


class ListRecipesAction:
    def execute(
        self,
        player_id: int,
        filter_name: str | None = None,
        provider_selector: str | None = None,
    ) -> ActionResult:
        filter_name = str(filter_name or "").strip().lower() or None
        player = _player_queryset().get(pk=player_id)
        providers, offers = crafting_offers(
            player,
            provider_selector=provider_selector,
        )
        filters = recipe_filters(offers)
        if filter_name and filter_name not in filters:
            raise ActionError(
                "Unknown recipe filter.",
                code="unknown_recipe_filter",
                data={"filters": list(filters)},
            )
        profiles = {}
        for provider in providers:
            profiles.setdefault(provider.profile.id, provider.profile_payload())
        recipes = ordered_recipe_payloads(
            player,
            offers,
            filter_name=filter_name,
        )
        data = {
            "operation": "list",
            "providers": [provider.payload() for provider in providers],
            "profiles": list(profiles.values()),
            "filter": filter_name,
            "filters": list(filters),
            "recipes": recipes,
            "count": len(recipes),
        }
        return ActionResult(
            data=data,
            events=[
                GameEvent(
                    type="cmd.recipes.success",
                    recipients=[player.key],
                    data=data,
                    text=_recipes_text(data),
                )
            ],
        )


class InspectRecipeAction:
    def execute(
        self,
        player_id: int,
        recipe_selector: str | None,
        provider_selector: str | None = None,
    ) -> ActionResult:
        player = _player_queryset().get(pk=player_id)
        _providers, offers = crafting_offers(
            player,
            provider_selector=provider_selector,
        )
        recipe, matching_offers = resolve_recipe_with_provider_suffix(
            _providers,
            offers,
            recipe_selector,
            allow_provider_suffix=provider_selector is None,
        )
        data = {
            "recipe": recipe_payload(
                player,
                recipe,
                providers=recipe_providers(matching_offers),
            ),
        }
        return ActionResult(
            data=data,
            events=[
                GameEvent(
                    type="cmd.recipe.success",
                    recipients=[player.key],
                    data=data,
                    text=_recipe_text(data),
                )
            ],
        )


class CraftItemAction:
    def execute(
        self,
        player_id: int,
        recipe_selector: str | None,
        provider_selector: str | None = None,
        *,
        request_id=None,
        request_segment="r",
    ) -> ActionResult:
        with transaction.atomic():
            player = _lock_player_with_related(player_id)
            receipt = _existing_receipt(
                player=player,
                request_id=request_id,
                request_segment=request_segment,
                action="craft",
            )
            if receipt is not None:
                data = deepcopy(receipt.result or {})
                updated_player = get_player_with_related(player.id)
                data["actor"] = serialize_actor(
                    updated_player,
                    updated_player.room,
                ).model_dump()
                data["materials"] = list_material_balances(updated_player)
                data["replayed"] = True
                data = _json_safe(data)
                return ActionResult(
                    data=data,
                    events=[
                        GameEvent(
                            type="cmd.craft.success",
                            recipients=[player.key],
                            data=data,
                            text=_craft_text(data),
                        )
                    ],
                )

            _providers, offers = crafting_offers(
                player,
                provider_selector=provider_selector,
            )
            recipe, matching_offers = resolve_recipe_with_provider_suffix(
                _providers,
                offers,
                recipe_selector,
                allow_provider_suffix=provider_selector is None,
            )
            offer = resolve_recipe_provider(matching_offers)
            ingredients = list(recipe.ingredients.all())
            if not ingredients:
                raise ActionError(
                    "That recipe has no crafting materials configured.",
                    code="invalid_recipe",
                )
            if not recipe_condition_met(player, recipe):
                raise ActionError(
                    recipe.failure_message or "You do not meet this recipe's requirements.",
                    code="recipe_conditions_not_met",
                )

            material_ids = sorted({ingredient.material_id for ingredient in ingredients})
            locked_balances = list(
                PlayerMaterialBalance.objects.select_for_update()
                .filter(player=player, material_id__in=material_ids)
                .select_related("material")
                .order_by("material_id")
            )
            balance_by_material = {
                balance.material_id: balance for balance in locked_balances
            }
            missing = []
            for ingredient in ingredients:
                owned = int(
                    getattr(balance_by_material.get(ingredient.material_id), "quantity", 0)
                    or 0
                )
                required = int(ingredient.quantity)
                if owned < required:
                    missing.append(
                        {
                            "material": {
                                "id": ingredient.material_id,
                                "key": f"craftmaterial.{ingredient.material_id}",
                                "slug": ingredient.material.slug,
                                "name": ingredient.material.name,
                            },
                            "owned": owned,
                            "required": required,
                            "missing": required - owned,
                        }
                    )
            if missing:
                raise ActionError(
                    f"You still need {_material_phrase(missing, quantity_key='missing')}.",
                    code="insufficient_materials",
                    data={"missing": missing},
                )

            cost = None
            if recipe.cost is not None and recipe.currency_id is not None:
                cost_amount = int(recipe.cost)
                cost = money_payload(cost_amount, recipe.currency)
                try:
                    mutate_balances(
                        player,
                        {recipe.currency: -cost_amount},
                        reason="crafting.recipe_cost",
                    )
                except WalletError as error:
                    if error.code == "insufficient_funds":
                        owned = int(
                            PlayerCurrencyBalance.objects.filter(
                                player=player,
                                currency_id=recipe.currency_id,
                            ).values_list("amount", flat=True).first()
                            or 0
                        )
                        missing_amount = max(0, cost_amount - owned)
                        missing_money = money_payload(missing_amount, recipe.currency)
                        raise ActionError(
                            f"You still need {missing_money['display']}.",
                            code="insufficient_currency",
                            data={
                                "cost": cost,
                                "owned": owned,
                                "missing": missing_amount,
                            },
                        )
                    raise ActionError(str(error), code=error.code)

            consumed_amounts = {}
            materials_by_id = {}
            for ingredient in ingredients:
                balance = balance_by_material[ingredient.material_id]
                balance.quantity = int(balance.quantity) - int(ingredient.quantity)
                consumed_amounts[ingredient.material_id] = int(ingredient.quantity)
                materials_by_id[ingredient.material_id] = ingredient.material
            PlayerMaterialBalance.objects.bulk_update(locked_balances, ["quantity"])

            item = spawn_item_from_definition(
                recipe.output_item_definition,
                player,
                player.world,
                extra_roll_metadata={
                    "source_recipe_slug": recipe.slug,
                    "source_crafting_profile_slug": offer.provider.profile.slug,
                    "source_crafting_provider": offer.provider.key,
                },
            )
            consumed = aggregate_material_payload(
                consumed_amounts,
                materials_by_id=materials_by_id,
            )
            domain_base = {
                "actor": {"id": player.id, "key": player.key},
                "provider": offer.provider.payload(),
                "profile": offer.provider.profile_payload(),
                "recipe": {
                    "id": recipe.id,
                    "slug": recipe.slug,
                },
            }
            enqueue_game_events(
                [
                    GameEvent(
                        type="crafting.item.crafted",
                        recipients=[],
                        data={
                            **domain_base,
                            "item": {
                                "id": item.id,
                                "key": item.key,
                                "definition_id": item.definition_id,
                                "definition_slug": item.definition_slug_snapshot,
                                "attributes": item.attributes or {},
                            },
                            "consumed": consumed,
                            "cost": cost,
                        },
                    ),
                    GameEvent(
                        type="crafting.material.changed",
                        recipients=[],
                        data={
                            **domain_base,
                            "reason": "craft",
                            "changes": [
                                {
                                    **entry,
                                    "delta": -int(entry["quantity"]),
                                }
                                for entry in consumed
                            ],
                        },
                    ),
                ]
            )

            updated_player = get_player_with_related(player.id)
            data = {
                "actor": serialize_actor(updated_player, updated_player.room).model_dump(),
                "provider": offer.provider.payload(),
                "profile": offer.provider.profile_payload(),
                "recipe": {
                    "id": recipe.id,
                    "key": f"craftingrecipe.{recipe.id}",
                    "slug": recipe.slug,
                    "name": recipe.output_item_definition.name,
                    "cost": cost,
                },
                "item": serialize_item(item, viewer=updated_player).model_dump(),
                "consumed": consumed,
                "cost": cost,
                "materials": list_material_balances(updated_player),
                "roll": {
                    "attributes": item.attributes or {},
                    "randomized": bool((item.roll_metadata or {}).get("randomized")),
                },
                "replayed": False,
            }
            data = _json_safe(data)
            receipt_data = deepcopy(data)
            receipt_data.pop("actor", None)
            receipt_data.pop("materials", None)
            _store_receipt(
                player=player,
                request_id=request_id,
                request_segment=request_segment,
                action="craft",
                result=receipt_data,
            )

        return ActionResult(
            data=data,
            events=[
                GameEvent(
                    type="cmd.craft.success",
                    recipients=[player.key],
                    data=data,
                    text=_craft_text(data),
                )
            ],
        )


def _item_tokens(item: Item) -> set[str]:
    definition = item.definition
    values = [
        item.key,
        resolve_item_name(item),
        _ARTICLE_RE.sub("", resolve_item_name(item)),
        item.keywords,
        getattr(definition, "slug", ""),
        str(getattr(definition, "slug", "")).replace("-", " "),
        getattr(definition, "keywords", ""),
    ]
    tokens = set()
    for value in values:
        tokens.update(_TOKEN_RE.findall(str(value or "").lower()))
    return tokens


def _normalized_item_keys(item: Item) -> set[str]:
    name = resolve_item_name(item)
    definition = item.definition
    normalize = lambda value: " ".join(_TOKEN_RE.findall(str(value or "").lower()))
    return {
        item.key.lower(),
        normalize(name),
        normalize(_ARTICLE_RE.sub("", name)),
        normalize(getattr(definition, "slug", "")),
        normalize(str(getattr(definition, "slug", "")).replace("-", " ")),
    }


def _matching_inventory_items(items: list[Item], selector: str) -> list[Item]:
    normalized_raw = str(selector or "").strip().lower()
    normalize = lambda value: " ".join(_TOKEN_RE.findall(str(value or "").lower()))
    exact = [item for item in items if normalized_raw in _normalized_item_keys(item)]
    if exact:
        return exact
    selector_tokens = set(_TOKEN_RE.findall(normalize(normalized_raw)))
    return [
        item for item in items
        if selector_tokens and selector_tokens.issubset(_item_tokens(item))
    ]


def _salvage_candidate_queryset(player: Player):
    """Return directly carried items eligible for the numbered salvage list."""
    item_content_type = ContentType.objects.get_for_model(Item)
    salvage_yield_exists = ItemSalvageYield.objects.filter(
        item_definition_id=OuterRef("definition_id"),
    )
    child_item_exists = Item.objects.filter(
        container_type=item_content_type,
        container_id=OuterRef("pk"),
        is_pending_deletion=False,
    )
    return (
        player.inventory.filter(is_pending_deletion=False)
        .exclude(type=adv_consts.ITEM_TYPE_QUEST)
        .annotate(
            _payload_is_salvageable=Exists(salvage_yield_exists),
            has_contents=Exists(child_item_exists),
        )
        .filter(_payload_is_salvageable=True, has_contents=False)
        .select_related("definition", "currency")
        .order_by("id")
    )


def _salvage_index_not_found(number: int | None = None) -> ActionError:
    label = f" #{number}" if number is not None else ""
    return ActionError(
        f"There is no salvage item{label}. Type salvage to list your options.",
        code="salvage_index_not_found",
        data={"number": number} if number is not None else {},
    )


def _bounded_unsigned_int(value: str, *, maximum: int) -> int | None:
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


def _resolve_salvage_item(player: Player, selector: str | None) -> Item:
    normalized = str(selector or "").strip().lower()
    if not normalized:
        raise ActionError("Salvage what?", code="missing_item")
    if normalized.startswith("item."):
        match = _ITEM_KEY_RE.fullmatch(normalized)
        item = None
        if match:
            item_id = _bounded_unsigned_int(
                match.group("id"),
                maximum=_MAX_DATABASE_ID,
            )
            if item_id is not None:
                item = (
                    player.inventory.filter(
                        pk=item_id,
                        is_pending_deletion=False,
                    )
                    .select_related("definition", "currency")
                    .first()
                )
        if item is not None:
            return item
        raise ActionError("You are not carrying that.", code="item_not_found")

    if _SALVAGE_INDEX_RE.fullmatch(normalized):
        # The largest valid value is 100. Reject an unreasonably long numeric
        # selector before int() so hostile input cannot hit Python's digit cap.
        if len(normalized.lstrip("-")) > 9:
            raise _salvage_index_not_found()
        number = int(normalized)
        if number < 1 or number > MAX_SALVAGE_LIST_ITEMS:
            raise _salvage_index_not_found(number)
        matches = list(
            _salvage_candidate_queryset(player)[number - 1:number]
        )
        if matches:
            return matches[0]
        raise _salvage_index_not_found(number)

    counted = _COUNTED_SELECTOR_RE.match(normalized)
    counted_index = None
    if counted:
        counted_index = _bounded_unsigned_int(
            counted.group("count"),
            maximum=_MAX_DATABASE_ID,
        )
        if counted_index is None or counted_index < 1:
            raise ActionError("You are not carrying that.", code="item_not_found")

    items = list(
        player.inventory.filter(is_pending_deletion=False)
        .select_related("definition", "currency")
        .order_by("id")
    )

    if counted:
        matches = _matching_inventory_items(items, counted.group("token"))
        if counted_index <= len(matches):
            return matches[counted_index - 1]
        raise ActionError("You are not carrying that.", code="item_not_found")

    matches = _matching_inventory_items(items, normalized)
    if not matches:
        raise ActionError("You are not carrying that.", code="item_not_found")
    if len(matches) > 1:
        raise ActionError(
            "That item name is ambiguous. Use item.<id> or a numbered selector.",
            code="ambiguous_item",
            data={
                "items": [
                    {"id": item.id, "key": item.key, "name": resolve_item_name(item)}
                    for item in matches
                ]
            },
        )
    return matches[0]


class SalvageItemAction:
    def execute(
        self,
        player_id: int,
        item_selector: str | None = None,
        *,
        spoils: bool = False,
        request_id=None,
        request_segment="r",
    ) -> ActionResult:
        spoils = spoils is True
        with transaction.atomic():
            player = _lock_player_with_related(player_id)
            receipt = _existing_receipt(
                player=player,
                request_id=request_id,
                request_segment=request_segment,
                action="salvage",
            )
            if receipt is not None:
                data = deepcopy(receipt.result or {})
                updated_player = get_player_with_related(player.id)
                data["actor"] = serialize_actor(
                    updated_player,
                    updated_player.room,
                ).model_dump()
                data["materials"] = list_material_balances(updated_player)
                data["replayed"] = True
                data = _json_safe(data)
                return ActionResult(
                    data=data,
                    events=[
                        GameEvent(
                            type="cmd.salvage.success",
                            recipients=[player.key],
                            data=data,
                            text=_salvage_text(data),
                        )
                    ],
                )

            remaining_spoils = 0
            if spoils:
                spoils_qs = player.inventory.filter(
                    is_pending_deletion=False,
                    definition__salvage_only=True,
                ).order_by("id")
                total_spoils = spoils_qs.count()
                if total_spoils == 0:
                    raise ActionError(
                        "You have no captured spoils to salvage.",
                        code="no_salvage_spoils",
                    )
                selected_ids = list(
                    spoils_qs.values_list("id", flat=True)[:MAX_SPOILS_PER_COMMAND]
                )
                remaining_spoils = max(0, total_spoils - len(selected_ids))
            else:
                selected = _resolve_salvage_item(player, item_selector)
                selected_ids = [selected.id]

            player_content_type = ContentType.objects.get_for_model(Player)
            locked_item_ids = list(
                Item.objects.select_for_update()
                .filter(
                    pk__in=sorted(selected_ids),
                    container_type=player_content_type,
                    container_id=player.id,
                    is_pending_deletion=False,
                )
                .order_by("id")
                .values_list("id", flat=True)
            )
            locked_items = list(
                Item.objects.filter(pk__in=locked_item_ids)
                .select_related("definition", "currency")
                .prefetch_related("definition__salvage_yields__material")
                .order_by("id")
            )
            if len(locked_items) != len(selected_ids):
                raise ActionError(
                    "One of those items is no longer in your inventory.",
                    code="item_not_found",
                )

            item_content_type = ContentType.objects.get_for_model(Item)
            if Item.objects.filter(
                container_type=item_content_type,
                container_id__in=selected_ids,
                is_pending_deletion=False,
            ).exists():
                raise ActionError(
                    "Empty that container before salvaging it.",
                    code="container_not_empty",
                )

            yielded_amounts: dict[int, int] = {}
            materials_by_id = {}
            item_snapshots = []
            for item in locked_items:
                if item.type == adv_consts.ITEM_TYPE_QUEST:
                    raise ActionError(
                        "Quest items cannot be salvaged.",
                        code="quest_item_bound",
                    )
                if item.definition is None:
                    raise ActionError(
                        f"{resolve_item_name(item).capitalize()} cannot be salvaged.",
                        code="item_not_salvageable",
                    )
                yields = list(item.definition.salvage_yields.all())
                if not yields:
                    raise ActionError(
                        f"{resolve_item_name(item).capitalize()} cannot be salvaged.",
                        code="item_not_salvageable",
                    )
                if spoils and not item.definition.salvage_only:
                    raise ActionError(
                        "Only captured spoils can be salvaged in bulk.",
                        code="not_salvage_spoils",
                    )
                item_snapshots.append(
                    {
                        "id": item.id,
                        "key": item.key,
                        "name": resolve_item_name(item),
                        "definition_id": item.definition_id,
                        "definition_slug": (
                            item.definition_slug_snapshot or item.definition.slug
                        ),
                        "salvage_only": bool(item.definition.salvage_only),
                    }
                )
                for salvage_yield in yields:
                    yielded_amounts[salvage_yield.material_id] = (
                        yielded_amounts.get(salvage_yield.material_id, 0)
                        + int(salvage_yield.quantity)
                    )
                    materials_by_id[salvage_yield.material_id] = salvage_yield.material

            material_ids = sorted(yielded_amounts)
            balances = list(
                PlayerMaterialBalance.objects.select_for_update()
                .filter(player=player, material_id__in=material_ids)
                .order_by("material_id")
            )
            balance_by_material = {balance.material_id: balance for balance in balances}
            new_balances = []
            for material_id in material_ids:
                if material_id not in balance_by_material:
                    new_balances.append(
                        PlayerMaterialBalance(
                            player=player,
                            material_id=material_id,
                            quantity=yielded_amounts[material_id],
                        )
                    )
                else:
                    balance_by_material[material_id].quantity = (
                        int(balance_by_material[material_id].quantity)
                        + yielded_amounts[material_id]
                    )
            if balances:
                PlayerMaterialBalance.objects.bulk_update(balances, ["quantity"])
            if new_balances:
                PlayerMaterialBalance.objects.bulk_create(new_balances)

            # Salvage is an intentional destructive action. Delete the rows
            # outright so stale client extraction cannot revive them by
            # clearing the pending-deletion bit.
            Item.objects.filter(pk__in=selected_ids).delete()

            yielded = aggregate_material_payload(
                yielded_amounts,
                materials_by_id=materials_by_id,
            )
            domain_base = {
                "actor": {"id": player.id, "key": player.key},
                "items": item_snapshots,
                "bulk": bool(spoils),
            }
            enqueue_game_events(
                [
                    GameEvent(
                        type="crafting.item.salvaged",
                        recipients=[],
                        data={
                            **domain_base,
                            "yielded": yielded,
                        },
                    ),
                    GameEvent(
                        type="crafting.material.changed",
                        recipients=[],
                        data={
                            **domain_base,
                            "reason": "salvage",
                            "changes": [
                                {
                                    **entry,
                                    "delta": int(entry["quantity"]),
                                }
                                for entry in yielded
                            ],
                        },
                    ),
                ]
            )

            updated_player = get_player_with_related(player.id)
            data = {
                "actor": serialize_actor(updated_player, updated_player.room).model_dump(),
                "items": item_snapshots,
                "count": len(item_snapshots),
                "yielded": yielded,
                "materials": list_material_balances(updated_player),
                "remaining_spoils": remaining_spoils,
                "replayed": False,
            }
            data = _json_safe(data)
            receipt_data = deepcopy(data)
            receipt_data.pop("actor", None)
            receipt_data.pop("materials", None)
            _store_receipt(
                player=player,
                request_id=request_id,
                request_segment=request_segment,
                action="salvage",
                result=receipt_data,
            )

        return ActionResult(
            data=data,
            events=[
                GameEvent(
                    type="cmd.salvage.success",
                    recipients=[player.key],
                    data=data,
                    text=_salvage_text(data),
                )
            ],
        )
