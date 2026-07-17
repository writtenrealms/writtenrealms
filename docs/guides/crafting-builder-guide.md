# Crafting Builder Guide

WR2 crafting keeps item power in `itemdefinition` manifests and puts only the
cost and availability of that output in a recipe. A craft always creates one
copy of the referenced definition, so its existing guided-random ranges roll
once in the normal item spawn path.

The complete design and economy rationale are in
[crafting-system.md](../architecture/crafting-system.md). This guide is the
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
apiVersion: v1alpha1
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
apiVersion: v1alpha1
kind: craftingrecipe
metadata:
  slug: reinforced-helm
spec:
  group: armor
  order: 10
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

## Profiles And Providers

A profile is an explicit, ordered recipe catalog:

```yaml
apiVersion: v1alpha1
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

Any directly carried item with at least one yield appears in the player's bare
`salvage` list and can be selected by its displayed number or name.

Use `only: true` for captured spoils. Salvage-only definitions must be inert,
must have at least one yield, cannot be equipped, are selected by `salvage
spoils`, and cannot be sold to merchants.
