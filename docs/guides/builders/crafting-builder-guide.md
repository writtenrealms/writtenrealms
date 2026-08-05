# Crafting Builder Guide

WR2 crafting keeps item power in `itemdefinition` manifests and puts only the
cost and availability of that output in a recipe. A craft always creates one
copy of the referenced definition, so its existing guided-random ranges roll
once in the normal item spawn path.

The complete design and economy rationale are in
[the crafting-system architecture notes](https://github.com/writtenrealms/writtenrealms/blob/main/docs/architecture/crafting-system.md). This guide is the
short authoring checklist.

## Authoring Order

Apply documents in this order when references are new:

1. output `itemdefinition` manifests
2. `craftmaterial` manifests
3. item-definition salvage patches
4. `craftingrecipe` manifests
5. a `craftingprofile` manifest
6. a room or mob-definition workshop attachment

The multi-document endpoint applies documents sequentially. Keep the order
above so every portable reference exists when it is resolved.

For deletion, reverse the dependent portion of that order: delete crafting
recipes before deleting their output item definitions. Recipe deletion also
removes the recipe from crafting profiles. A recipe delete manifest must
identify an existing recipe; an unknown recipe slug returns a not-found error
for the selected world, while omitted `id`, `key`, and `slug` metadata returns
a required-field error. Multi-document deletion is atomic, so any failed
document rolls back the whole batch.

## Builder UI

World Config links to the world's Craft Materials, Crafting Recipes, and
Crafting Profiles. Each list page shows the authored definitions for that
world. `Add` opens manifest import with starter YAML for the selected kind.

Selecting a definition opens its detail page. The canonical YAML editor is the
editing surface there, with actions to copy the canonical YAML or a separate
delete manifest. Runtime instances inherit their authored definitions and are
read-only; change the source world's definition rather than trying to edit an
instance.

## Materials

Materials are world-scoped ledger resources, not physical items or currencies.

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: craftmaterial
metadata:
  slug: bronze
  name: Bronze
spec:
  description: Usable bronze recovered from armor, weapons, and spoils.
  order: 10
```

`order` controls player-facing display. Material quantities must be positive
integers wherever they appear.

## Recipes

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: craftingrecipe
metadata:
  slug: reinforced-helm
spec:
  group: armor
  order: 10
  cost: 150
  currency: obol
  output:
    item_definition: itemdefinition.reinforced-helm
  inputs:
    - material: craftmaterial.bronze
      quantity: 8
    - material: craftmaterial.leather
      quantity: 2
  conditions:
    gte: [actor.level, 20]
  failure_message: You are not yet ready to craft this armor.
```

The output name, slot, combat values, and random ranges all come from the item
definition. `group` is presentation metadata used by recipe filters; it does
not restrict a recipe to one player class. `conditions` uses the shared WR2
condition DSL.

`cost` is an optional currency fee paid in addition to every material input.
It is a whole number from `0` through `9,007,199,254,740,991`. On a new recipe,
or when adding a cost to an existing unpriced recipe, an omitted `currency`
resolves to the world's current default and stores that concrete currency.
Canonical exports include both fields for a priced recipe, so changing the
world default never silently retargets an existing fee. Cost-only authoring is
rejected when the world has no default currency to resolve.

For recipe patches:

- omitting both `cost` and `currency` preserves the existing fee
- setting `cost` updates the amount and preserves an existing currency, or
  resolves the default when the recipe was previously unpriced
- setting `currency` without `cost` is invalid
- setting `cost: null` removes both the amount and currency

The referenced currency must belong to the recipe's world. Negative,
fractional, boolean, oversized, unknown-currency, and cross-world values are
rejected. A zero fee remains an explicitly authored fee; omit the fields or
use `cost: null` when the recipe should have no monetary requirement.

## Profiles And Providers

A profile is an explicit, ordered recipe catalog:

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: craftingprofile
metadata:
  slug: town-forge
  name: Town Forge
spec:
  keywords: town workshop forge armory
  recipes:
    - craftingrecipe.reinforced-helm
    - craftingrecipe.reinforced-spear
```

Attach it directly to a room for a dependable workshop:

```yaml
spec:
  crafting:
    profile: craftingprofile.town-forge
```

A direct room attachment automatically exposes `craft` and `salvage` actions
in the room display. The `salvage` action opens the same read-only list as the
bare command; it does not make salvage workshop-only. Builders do not need to
create duplicate room actions or command triggers for workshop discoverability.

Or attach it to a mob definition when that NPC's presence should control
access:

```yaml
spec:
  crafting:
    profile: craftingprofile.town-forge
    availability: alive_and_present
```

Use only one attachment for an ordinary workshop. Attaching the same catalog
to both the room and a decorative NPC makes provider selection ambiguous.

## Salvage

Salvage is intrinsic to an item definition and uses fixed yields:

```yaml
spec:
  salvage:
    only: false
    yields:
      - material: craftmaterial.bronze
        quantity: 2
```

Omitting `salvage` preserves an existing specification during a patch. An
explicit empty or null salvage value clears it.

An item created from a definition with at least one authored yield displays a
**SALVAGEABLE** indicator in item look and lookup views. The indicator describes
the definition's salvage capability, not the item's immediate command
eligibility.

Any directly carried, otherwise eligible item with at least one yield appears
in the player's bare `salvage` list and can be selected by its displayed number
or name. Quest items and nonempty containers are excluded.

Use `only: true` for captured spoils. Salvage-only definitions must be inert,
must have at least one yield, cannot be equipped, are selected by `salvage
spoils`, and cannot be sold to merchants.
