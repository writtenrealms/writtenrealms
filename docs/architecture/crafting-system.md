# WR2 Crafting And Salvage System

Status as of 2026-07-17: the initial crafting and salvage slice is
implemented. Deferred extensions remain listed at the end of this document.

## Purpose

WR2 needs a crafting loop that makes guided-random progression gear a
repeatable target without filling player inventories and the database with
hundreds of individual material items.

The intended player loop is:

> Loot physical equipment -> salvage it into tracked materials -> visit a
> workshop -> choose an authored recipe -> receive one randomly rolled item.

This document specifies:

- how builders author materials, recipes, workshops, and salvage yields
- how players inspect material balances and recipe requirements
- how crafting providers relate to rooms and NPCs
- how physical salvage-only loot becomes crafting material
- how recipe costs can be normalized across armor and weapon loadouts
- how crafting and salvage should execute safely under concurrent load

Reference docs:

- [guided-random-item-definitions.md](guided-random-item-definitions.md)
- [merchant-system.md](merchant-system.md)
- [trigger-event-subscriptions.md](trigger-event-subscriptions.md)
- [yaml-manifest-system.md](yaml-manifest-system.md)
- [attack-routines-and-dual-wielding.md](attack-routines-and-dual-wielding.md)
- [condition-builder-guide.md](../guides/condition-builder-guide.md)
- [player-command-flow.md](../flows/player-command-flow.md)

## Implemented Design

Separate six concepts:

- `CraftMaterial`: an authored bulk resource such as Bronze or Linen
- `CraftingRecipe`: one exact item-definition output and its required inputs
- `CraftingProfile`: an authored catalog of recipes exposed by a provider
- crafting provider: a room workshop or, optionally, an NPC
- `PlayerMaterialBalance`: a player's current unspent amount of one material
- salvage specification: the materials an item definition returns when
  destroyed

Recipes must reference existing `ItemDefinition` rows. They must not copy
armor, weapon damage, attributes, descriptions, or randomization ranges. The
item definition remains the sole owner of the resulting item's power and
guided-random behavior.

Common materials are compact ledger balances rather than concrete
`spawns.Item` rows. Dropped gear remains physical. Salvaging that gear converts
it into material balances.

Crafting requires an available workshop, but it does not require a
particular mob. An NPC may provide or decorate a workshop when that is useful
for the story.

## Current WR2 Baseline

WR2 now has first-class crafting models, manifests, transactional actions, and
player commands. The WR1 crafter/upgrader flags, workshop room flags, craft
endpoints, and item upgrade counters remain intentionally removed. Current
crafting is built on WR2 item definitions and manifests rather than restoring
the WR1 tables.

The relevant existing systems are:

- `ItemDefinition` is the authoritative authored item model.
- Spawning an item definition already rolls its guided random attributes once
  and persists the roll on the concrete item.
- `CraftingProfile` is a reusable authored catalog attached to a room or mob
  provider.
- crafting text commands follow the Command -> Action -> Event pattern.
- structured recipe access restrictions use the shared WR2 condition DSL.
- every physical item copy is a separate `spawns.Item` row. Stable items may
  look stacked in the frontend, but the backend has no quantity column for an
  item stack.
- the existing custom-currency balance is serialized on the player and does
  not provide a complete generic, transactional balance service. Crafting
  materials should not be added to that field as-is.

Crafting is implemented as first-class commands and transactional actions. It
is not assembled from room triggers. Trigger scripts are not an
appropriate place to atomically count and consume ingredients.

## Goals

- Make authored progression gear a deterministic crafting target with bounded
  RNG on the result.
- Let builders author exact costs without duplicating item combat data.
- Make captured or broken equipment useful as physical, salvage-only loot.
- Keep the player command surface short and readable in a text client.
- Support one convenient workshop now without forcing all future content to use
  one NPC or one location.
- Keep common material storage compact and safe under concurrent commands.
- Preserve a path to learned recipes, rare catalysts, multiple workshops, and
  higher-tier crafting later.
- Emit stable gameplay events for quests, analytics, and future achievements.

## Non-Goals For The Initial Slice

- crafting professions or crafting skill levels
- craft failure or loss of materials without an output
- timed crafting jobs or production queues
- item-quality minigames
- random output bundles
- recipe-scroll drops for ordinary recipes
- player-owned workshops
- common material items occupying inventory slots
- unrestricted bulk salvage
- automatic cost formulas derived from an item's armor or attributes

## Player Loop

The initial crafting loop is:

1. Enemies drop physical equipment and salvage-only spoils.
2. The player picks up and inspects those items normally.
3. Bare `salvage` lists eligible items; `salvage <number>`, `salvage <item>`,
   or `salvage spoils` destroys selected items and credits crafting materials.
4. `materials` shows the player's current material balances from anywhere.
5. At a workshop, `recipes`, bare `recipe`, or bare `craft` shows the numbered
   recipe catalog.
6. `recipe <number>` or `recipe <item>` shows the output range and
   owned/required materials.
7. `craft <number>` or `craft <item>` atomically spends materials and spawns one
   item definition.
8. The item definition's existing randomization rolls once and persists.
9. An unwanted craft may be salvaged for a minority refund and crafted again.

Crafting always produces the selected item. The roll on that item is the source
of replayability; a second chance for the entire craft to fail is unnecessary.

## Player Command Surface

The initial command set is:

```text
materials
recipes [filter]
recipe
recipe <number>
recipe <item>
craft
craft <number>
craft <item>
salvage
salvage <number>
salvage <item>
salvage spoils
```

All commands should return both text suitable for the MUD console and a
structured payload suitable for richer frontend rendering.

### `materials`

`materials` is the player's crafting-material wallet display.

It lists the player's **current unspent balances** of accumulated crafting
materials. It does not show lifetime totals. If a player has earned 30 Bronze
and spent 12, `materials` shows 18 Bronze.

The command:

- is available anywhere; no workshop or NPC is required
- reads material balances only and does not change state
- shows materials with a positive current balance
- sorts them by authored display order and then name
- reports a simple empty state when the player owns none

Example:

```text
> materials

Crafting materials:
  Bronze   18
  Leather   6
  Linen    12
  Wood      4
```

Empty state:

```text
> materials

You have no crafting materials.
```

The structured response should have a stable shape such as:

```json
{
  "materials": [
    {"slug": "bronze", "name": "Bronze", "quantity": 18},
    {"slug": "leather", "name": "Leather", "quantity": 6}
  ]
}
```

`materials` specifically does **not** list:

- recipes
- the ingredients required by a selected recipe
- every material type defined in the world with zero beside it
- physical salvageable items in inventory
- lifetime material income or spending
- gold, medals, glory, or combat stats

Recipe requirements belong to `recipe <number>` or `recipe <item>`. Physical
loot belongs to the normal inventory command. Lifetime source and sink totals
belong in analytics, not in the player command.

A future `materials all` command could include zero balances if that proves
useful, but it is not needed for the first slice.

### `recipes [filter]`

`recipes` lists the recipes exposed by an available crafting profile at the
player's current location.

Useful filters include builder-authored recipe groups as well as:

- `armor`
- `weapons`
- `ready`

Filters affect presentation only. They are not class restrictions.

Example:

```text
> recipes armor

Armor recipes at the Town Forge:
  1. a reinforced helm       ready
  2. a plated cuirass        need 4 Bronze
  3. a pair of sturdy boots  ready

Use: recipe <number> to inspect; craft <number> to make.
```

Large recipe catalogs are too long for one useful ungrouped text block. The
response should group or page entries and should always advertise available
filters.

Bare `recipe` and bare `craft` act as aliases for `recipes`. The canonical
listing command remains `recipes` so that listing and spending have distinct
names.

Each recipe receives a one-based number from the deterministic, deduplicated
catalog across all currently local providers. Assign numbers before applying a
provider or presentation filter. Narrowed views therefore retain canonical
numbers and may show gaps, allowing a later `recipe <number>` or `craft
<number>` to reproduce the selection without storing per-player list state.
Adding or omitting `at <workshop>` can narrow provider choice but cannot retarget
the number to a different recipe. Numbers are view positions rather than
durable recipe identifiers and may change when authored memberships or
available providers change.

Read-only inspection may aggregate every local provider offering the selected
recipe. Only `craft` must resolve exactly one provider for provenance and must
return an ambiguity error when more than one remains.

### `recipe`, `recipe <number>`, and `recipe <item>`

Bare `recipe` lists the catalog. `recipe <number>` and `recipe <item>` inspect
one recipe without spending anything. They should show:

- the output item name
- equipment slot and armor class or weapon type
- fixed combat values
- minimum and maximum randomized attributes
- each required material
- owned and required quantities
- a concise missing-material summary
- any unmet non-material condition

Example:

```text
> recipe reinforced helm

A reinforced helm
Heavy head armor
Armor: 163
Constitution: 2-3

Bronze:  8 / 8
Leather: 1 / 2

Missing: 1 Leather
```

The first number is owned and the second number is required. The output should
use one convention everywhere.

### `craft`, `craft <number>`, and `craft <item>`

Bare `craft` lists the catalog. `craft <number>` and `craft <item>` are explicit
state-changing actions. They should:

1. resolve an offered recipe
2. recheck provider availability and recipe conditions
3. recheck current material balances
4. atomically consume the materials
5. spawn the referenced item definition into player inventory
6. report the item's actual persisted roll

Example:

```text
> craft reinforced helm

You spend 8 Bronze and 2 Leather.
You craft a reinforced helm.
Roll: Constitution 3.
```

No additional confirmation is required. Typing the complete craft command is
the player's confirmation. A failed requirement must consume nothing.

Item and recipe selectors should ignore leading articles. The item definition
may correctly be named `a reinforced helm`, while the player types
`reinforced helm`.

### `salvage`, `salvage <number>`, and `salvage <item>`

Bare `salvage` is a read-only inventory view. It lists only directly carried
items that are currently eligible for ordinary salvage, in item-id order, and
assigns one-based numbers:

```text
> salvage

You can salvage:
1. a battered scale coat
2. a dented bronze helm
Use: salvage <number>
```

`salvage <number>` resolves against that same filtered order. The number is a
convenient view index, not a permanent item identifier; inventory changes may
renumber the list. The implementation limits the view to the first 100
eligible items to bound query work, payload size, and console output. Players
may still select other carried items by name or exact item key.

`salvage <item>` destroys one specifically selected carried item and credits
the authored yields from its item definition.

Example:

```text
> salvage battered scale coat

You salvage a battered scale coat.
You recover 4 Bronze and 2 Leather.
```

Salvage should be available anywhere. Crafting remains workshop-bound. This
keeps a hunting trip from accumulating excessive salvage-only item rows and
does not force players to carry unusable spoils back to a particular NPC.

The command must reject:

- equipped items
- items without an authored salvage specification
- protected or favorited items, once item protection exists
- nonempty containers
- quest-owned items that existing item rules prevent from being destroyed
- an ambiguous selector

The selected item's rolled attributes do not change its salvage yield.

### `salvage spoils`

`salvage spoils` is a safe bulk command. It selects only carried items whose
definitions explicitly declare `salvage.only: true`.

It must not include ordinary equipment merely because that equipment is
salvageable. This makes captured or broken equipment convenient to process
without creating a dangerous general `salvage all` command.

The result should aggregate materials into one compact message:

```text
> salvage spoils

You salvage 6 captured items.
You recover 15 Bronze, 7 Leather, and 4 Linen.
```

## Material Representation

### Decision

Common materials are ledger balances, presented to players as materials. They
are not combat attributes, physical inventory items, or money.

For example, a compact material palette might be:

- Bronze
- Leather
- Linen
- Wood

These names are short, familiar, and sufficient for a broad equipment catalog
without turning the system into an inventory of rivets, glue, thread, wax,
nails, hides, ingots, scales, and planks. Each world owns its material catalog
and may choose a different palette.

Material names are mass nouns rather than item names, so the item-name article
rule does not apply to them. `Bronze` is correct; `a Bronze` is not.

### Why Common Materials Are Not Physical Items

Every current physical item copy is a separate database row. Frontend stacking
groups compatible items visually but does not turn them into a single row with
a quantity. Modeling 100 scraps as physical items would therefore create:

- 100 runtime item rows
- noisy inventory payloads
- ambiguous item selectors
- larger scans during crafting and salvage
- unnecessary database growth
- cumbersome trading and dropping commands

A compact balance row avoids those costs.

### Why Materials Do Not Reuse Current Custom Currencies

WR2 can author custom currency definitions, but player custom balances are
currently serialized into a player field and are not backed by a complete
generic atomic-balance service. Merchant configuration can name currencies,
but the current transaction path still specializes important behavior around
gold.

Crafting should therefore introduce a dedicated normalized material balance
rather than silently treating Bronze as money or extending the existing
serialized currency field.

### Material Persistence And Trading

For the first slice:

- materials persist through logout
- materials survive death
- materials cannot be dropped
- materials cannot be sold to merchants
- materials cannot be directly traded between players

Crafted equipment remains an ordinary physical item and may use the normal
trade rules. If a player material market becomes desirable, add explicit
material transfer and trade-escrow support. Do not convert common materials
back into thousands of physical item rows merely to make them tradeable.

### Rare Physical Catalysts

Rare, low-volume, narratively important ingredients may be physical items in a
future slice, for example:

- a divine ember
- an ancient insignia
- a deepwater pearl
- a boss relic

The recipe model should retain a path to typed physical inputs, but ordinary
recipes should use only material balances. Do not implement alternative inputs,
tools, or catalysts until content needs them.

## Builder Authoring Model

The examples in this section are the implemented manifest contracts.

### Craft Material

`kind: craftmaterial` defines a world-scoped resource.

```yaml
kind: craftmaterial
metadata:
  slug: bronze
  name: Bronze
spec:
  description: Usable bronze recovered from armor, weapons, and captured spoils.
  order: 10
---
kind: craftmaterial
metadata:
  slug: leather
  name: Leather
spec:
  description: Cleaned hide suitable for straps, liners, grips, and light armor.
  order: 20
---
kind: craftmaterial
metadata:
  slug: linen
  name: Linen
spec:
  description: Woven fiber suitable for clothing, padding, and layered armor.
  order: 30
---
kind: craftmaterial
metadata:
  slug: wood
  name: Wood
spec:
  description: Seasoned timber suitable for shafts, staves, and shield cores.
  order: 40
```

The slug is the stable manifest and runtime reference. The name is the short
player-facing label used by `materials`, recipes, crafting, and salvage.

### Crafting Recipe

`kind: craftingrecipe` selects one exact output definition and declares its
inputs.

```yaml
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
    gte:
      - actor.level
      - 20

  failure_message: You are not yet ready to craft this armor.
```

The recipe display name should default to the referenced item definition's
name. Builders should not have to maintain a duplicate item name on the recipe.

For equippable gear, output quantity is always one. The initial system should
not allow item bundles or multiple possible outputs. A player chooses the item;
only its bounded attributes vary.

`group` is presentation metadata used by authored filters such as
`recipes armorer` or `recipes expedition`. Armor/weapon filters can be inferred
from the output item's equipment type.

`conditions` uses the shared structured WR2 condition DSL. It can gate a recipe
by level, quest completion, world state, or similar authored facts. Material
quantities are not conditions; they are checked transactionally from `inputs`.

### Crafting Profile

`kind: craftingprofile` is a reusable ordered catalog of recipe references.

```yaml
kind: craftingprofile
metadata:
  slug: town-forge
  name: Town Forge
spec:
  keywords: town workshop forge armory
  recipes:
    - craftingrecipe.reinforced-helm
    - craftingrecipe.plated-cuirass
    - craftingrecipe.reinforced-spear
```

A profile should explicitly reference every recipe it offers. Explicit
references are more predictable than discovering recipes dynamically from slug
prefixes or notes.

### Room Workshop Attachment

A room should be able to expose one crafting profile directly:

```yaml
kind: room
metadata:
  ref: room@0,0,0
  name: Town Forge
spec:
  crafting:
    profile: craftingprofile.town-forge
```

The room is the provider. An NPC may be described in the room for flavor, but
its presence is not required for this profile to function.

### Optional NPC Attachment

The same profile may optionally be attached to a mob definition:

```yaml
kind: mobdefinition
metadata:
  slug: town-armorer
  name: an armorer
spec:
  room_description: An armorer directs the work around a smoking forge.
  keywords: armorer smith crafter
  combat:
    attackable: false
  crafting:
    profile: craftingprofile.town-forge
    availability: alive_and_present
```

Use the NPC attachment when the NPC's presence should mechanically control
access, such as a traveling artisan or a specialist who can be rescued. Do not
make an ordinary progression workshop depend on an attackable or wandering mob.

### Salvage Specification

Salvage belongs to the item definition because it is intrinsic to the item,
not to the recipe that may have created it.

Craftable item:

```yaml
kind: itemdefinition
metadata:
  slug: reinforced-helm
  name: a reinforced helm
spec:
  # Existing item fields remain authoritative.
  salvage:
    only: false
    yields:
      - material: craftmaterial.bronze
        quantity: 2
      - material: craftmaterial.leather
        quantity: 1
```

Salvage-only item:

```yaml
kind: itemdefinition
metadata:
  slug: battered-scale-coat
  name: a battered scale coat
spec:
  description: Battered scales hang from a backing of leather and linen.
  type: inert
  keywords: battered scale coat armor spoils
  salvage:
    only: true
    yields:
      - material: craftmaterial.bronze
        quantity: 4
      - material: craftmaterial.leather
        quantity: 2
      - material: craftmaterial.linen
        quantity: 1
```

`only: true` means the item exists primarily as physical spoils:

- it may be picked up and inspected
- it cannot be equipped
- it is eligible for `salvage spoils`
- it should not participate in normal merchant sale loops

Initial salvage quantities should be fixed. Random salvage ranges can be added
later if they prove valuable, but they would make economy tuning and player
expectations less clear.

## Workshop And NPC Design

### Provider, Not Mandatory Mob

Runtime code should resolve a crafting provider rather than assuming every
craft has a mob target. A provider exposes one `CraftingProfile` and supplies a
display name and keywords.

Supported provider types should be:

- room workshop: initial default
- nearby NPC: optional

Portable stations and anywhere-available recipe books are deferred.

### Profile Scope

For an initial crafting loop, prefer one convenient room-level workshop when
the recipe catalog remains manageable.

Do not split belts, sandals, armor, and weapons among separate mobs merely
because historical crafts used specialists. That adds commands and travel
without creating a meaningful build decision.

Content may split profiles when it serves geography or progression, for
example:

- a city armory offering common weapons and heavy armor
- a leatherworker offering light armor
- a sanctuary offering ritual implements
- a remote forge offering one special learned recipe

Recipes remain independent and reusable, so this split requires no recipe
duplication.

### Provider Resolution

For a recipe or craft command:

1. collect the room-level profile and profiles on available nearby NPCs
2. retain providers that expose the selected recipe
3. use the provider automatically if exactly one matches
4. reject with a useful ambiguity message if more than one matches
5. allow explicit `at <provider>` or `with <npc>` syntax when needed

Examples:

```text
recipe reinforced helm at city forge
craft reinforced helm with armorer
```

The ordinary one-workshop case should not require either suffix.

## Recipe Visibility And Discovery

All ordinary recipes should be automatically available whenever their
crafting profile is accessible. The material hunt and the output roll already
provide progression and replayability; random recipe scrolls would add another
grind before the base loop has been balanced.

Recipe conditions may be displayed as locked requirements. They must be
rechecked when crafting.

Future progression may add learned recipes with a dedicated
`PlayerKnownCraftingRecipe` relation and a first-class `learn_recipe` effect.
Worlds could then mix automatically available workshop recipes with recipes
learned from quests, trainers, or special content.

Do not encode learning by running arbitrary trigger command text.

## Recipe Scope

The crafting system does not prescribe a world-specific equipment catalog.
Builders choose which item definitions have recipes and which profiles expose
those recipes. Recipes are not inherently class-specific: every player may
inspect and craft every offered recipe unless a recipe condition says
otherwise. Existing item proficiency and equip rules determine whether a
particular character can use the result.

A world may deliberately omit equipment categories such as accessories from a
particular tier's crafting balance without requiring a runtime restriction.

## Crafting Cost Balance

Builders author exact material quantities. Runtime must not derive costs from
armor, attributes, quality, or item budget automatically.

Use these relative cost weights as the starting point:

| Output | Cost weight |
|---|---:|
| Body armor | 10 |
| Two-handed weapon | 10 |
| Head armor | 5 |
| Leg armor | 5 |
| One-handed weapon | 5 |
| Shield | 5 |
| Arms | 3 |
| Hands | 3 |
| Waist | 3 |
| Feet | 3 |

This makes the three normal weapon configurations comparable:

- one two-handed weapon: 10
- one one-handed weapon and one shield: 10
- two one-handed weapons: 10

A dual-wield loadout therefore does not cost twice as much as a comparable
two-handed or weapon-and-shield loadout. A complete seven-piece armor set has
weight 32 before its weapon loadout.

Allocate materials according to item construction and authored identity:

- plate or scale armor: primarily metal, then leather and cloth
- light armor: primarily leather and cloth, with metal where appropriate
- robes and padded armor: primarily cloth and leather
- spears and staves: meaningful Wood plus Bronze where appropriate
- shields: Wood, Leather, and Bronze according to construction

The cost weight is a balancing relationship, not necessarily the literal sum
of the listed ingredient quantities. One project-wide conversion should be
used consistently when the first recipes are authored.

Actual costs should ultimately be tuned from desired acquisition pace:

- how many ordinary salvage drops fund a small armor piece
- how many fund a head or leg piece
- how many fund body armor or a complete weapon loadout
- how long a typical player takes to assemble a complete crafted loadout

Do not tune only for aesthetically pleasing ingredient numbers.

## Randomization And Bad-Luck Loop

Crafting selects an exact item definition. The normal item spawn path then:

1. copies the definition's fixed properties
2. rolls each authored random attribute once
3. persists the rolled attributes and roll metadata
4. places the concrete item in the player's inventory

The recipe inspection view shows minimum and maximum results. The success view
shows the actual persisted result.

For the initial slice:

- crafting cannot fail
- no hidden quality roll is added
- no crafting skill modifies the range
- no reroll command exists
- no timer or asynchronous craft job exists

An unwanted result can be salvaged, but crafted gear should return only about
25-35% of its original material cost. That creates a resource sink and prevents
free infinite rerolls.

Salvage yield is based on the item definition, never the rolled stats. A maximum
roll and a minimum roll return the same materials.

Later deterministic refinement may let a rare catalyst raise one rolled value
without lowering another, up to the definition's authored maximum. That is
preferable to adding an unbounded full-reroll system.

## Runtime Data Model

Recommended authored models:

```text
CraftMaterial
  world
  slug
  name
  description
  order

CraftingRecipe
  world
  slug
  output_item_definition
  group
  order
  conditions
  failure_message

CraftingIngredient
  recipe
  material
  quantity

CraftingProfile
  world
  slug
  name
  keywords

CraftingProfileRecipe
  profile
  recipe
  order

ItemSalvageYield
  item_definition
  material
  quantity

ItemDefinition.salvage_only
```

Recommended runtime balance model:

```text
PlayerMaterialBalance
  player
  material
  quantity

CraftingActionReceipt
  player
  request_id
  segment
  action
  result
```

Database requirements:

- unique constraint and index on `(player_id, material_id)`
- unique world-scoped slugs for materials, recipes, and profiles
- unique `(recipe_id, material_id)` ingredient rows
- unique `(profile_id, recipe_id)` membership rows
- nonnegative balance constraint
- positive ingredient and salvage quantities
- protected deletion for referenced output definitions and materials
- unique `(player_id, request_id, segment)` replay receipts, pruned after
  a bounded retry window

Conditions may remain JSON. Recipes, ingredients, profile membership, and
balances should be relational so references and quantities can be validated and
queried efficiently.

## Runtime Craft Transaction

Crafting must use one database transaction:

1. lock the player row
2. resolve the current provider and numbered or named recipe against the locked
   player state
3. load and lock relevant balance rows in material-id order
4. re-evaluate recipe conditions
5. verify every balance
6. subtract all costs
7. spawn the referenced item definition into player inventory
8. record the source recipe/provider in roll or provenance metadata
9. publish state and domain events after successful commit

Any failure rolls back both resource changes and item creation.

The WebSocket command boundary accepts or generates a UUID request id and keeps
a bounded hierarchical segment path for chained text commands. Craft and
salvage store compact transactional receipts keyed by player, request, and
segment path, so task redelivery or a client retry cannot spend or grant
materials twice. The receipt also records the action: the same action replays,
while a dynamically changed alias or history entry that resolves the same
request path to a different mutation fails with an idempotency conflict.
Replayed responses rebuild current actor/material state instead of returning a
stale inventory snapshot. A daily task removes receipts after seven days.
Receipts are checked before resolving a recipe number, so retrying a completed
request cannot retarget a recipe after catalog order changes.

Crafting is immediate. Do not add a queue, polling job, or one Celery task per
material or recipe.

## Runtime Salvage Transaction

Single-item salvage must:

1. lock the player
2. resolve the current numbered index or item selector and lock that item
3. recheck ownership, equipment state, quest restrictions, and salvage data
4. mark or remove the item using the existing item lifecycle rules
5. add all yields to material balances
6. publish the updated inventory and balances after commit

Bulk spoils salvage must:

- select only `salvage.only: true` items
- lock selected item ids in a stable order
- aggregate yields by material before updating balances
- perform bounded bulk item updates
- use one transaction for the batch

It must not call the single-item action once per item.

The bare salvage list is not part of this mutation transaction. It uses one
bounded eligibility query and does not create a receipt. A subsequent numeric
command resolves the current list only after the player lock is held, then the
normal mutation path rechecks the selected item. Idempotency receipts are
checked before numeric resolution, so retrying a completed request cannot
retarget an item that later moved into the same list position.

## Manifest Validation

Manifest application should reject:

- missing or cross-world references
- duplicate world-scoped slugs
- an output other than an item definition
- more than one output
- an equippable output quantity other than one
- zero, negative, fractional, or nonnumeric quantities
- duplicate material inputs in one recipe
- duplicate recipes in one profile
- malformed condition payloads
- a profile, recipe, material, and output from incompatible authored worlds
- salvage-only definitions that are still equippable
- unknown fields

Builder tooling should warn, but not necessarily reject, when:

- total salvage yield approaches or exceeds the corresponding craft cost
- a crafted item has a merchant value that could create a material-to-gold loop
- a recipe is not exposed by any profile
- a profile contains an unusually large ungrouped catalog
- a recipe group does not match the intended recipe family

Multi-document application should validate references before mutating data or
document that materials and outputs must be applied before recipes, and recipes
before profiles. A future validate-all/apply-atomically path is preferable for
large crafting catalogs.

## Events And Analytics

Recommended domain events:

- `crafting.item.crafted`
- `crafting.item.salvaged`
- `crafting.material.changed`
- `crafting.recipe.learned` when learned recipes are implemented

Craft events should include:

- actor/player id
- provider type and id
- profile and recipe slugs
- output item id and item-definition slug
- actual rolled attributes
- consumed material quantities

Salvage events should include:

- actor/player id
- salvaged item id and item-definition slug
- whether the definition was salvage-only
- yielded material quantities

Balance actual gameplay from aggregate source and sink metrics:

- materials earned by source definition
- materials spent by recipe
- items crafted per recipe
- salvage counts per definition
- distribution of crafted rolls
- average crafts before a player keeps an item
- average time from first material income to a complete crafted loadout

Do not calculate lifetime totals by scanning gameplay events during ordinary
commands. Analytics should use asynchronous event consumers or aggregates.

## Performance And Scalability

Crafting work is naturally isolated per player and should not introduce global
locks.

Required query behavior:

- load a profile's recipes, outputs, and ingredients with bounded
  `select_related`/`prefetch_related` queries
- fetch all relevant player balances in one query
- never issue one balance query per recipe in the recipe list
- derive displayed recipe numbers and numeric resolution from the same
  deterministic, deduplicated catalog order
- never scan every item definition in the world to discover workshop recipes
- never materialize an entire inventory when one indexed salvage target is
  sufficient
- list salvage candidates with one bounded query, a hard result cap, and no
  per-item eligibility queries
- aggregate batch salvage before balance updates

Cache the immutable authored catalog by world/profile revision when recipe
volume warrants it. Invalidate that cache when material, recipe, output, or
profile manifests change.

Player row locking serializes conflicting commands for one character while
allowing different players to craft concurrently. Balance locks must follow a
stable material-id order, and item locks must follow a stable item-id order.

## Safety And Economy Rules

- Crafting or salvage failure consumes nothing.
- Crafted gear should have no vendor value, or merchants must explicitly
  reject it, until material-to-gold loops are evaluated.
- Salvage never considers equipped gear.
- Bulk salvage initially processes only explicitly marked spoils.
- Materials survive death in the first slice.
- Material balance may never become negative.
- Item roll quality never increases salvage yield.
- A recipe cannot override its output definition's stat ranges.
- Changing an item definition automatically changes future crafted output;
  recipes do not carry stale stat copies.

## Testing Requirements

Automated coverage for the implemented slice should include:

- material, recipe, profile, and salvage manifest create/update/export
- strict reference, quantity, duplicate, and condition validation
- room and NPC provider resolution
- `materials` positive-balance ordering and empty state
- recipe filters and owned/required counts without N+1 queries
- bare `recipe`/`craft` listing, canonical numbering across filters, numeric
  inspection/crafting, duplicate-name disambiguation, and invalid indices
- numeric craft retry after catalog reordering without retargeting
- successful craft with a persisted guided-random result
- insufficient-material craft with no partial deduction
- condition failure with no deduction
- idempotent retry behavior
- two concurrent craft attempts against the same balance
- single-item salvage safety checks
- bare salvage list eligibility, one-based numeric selection, empty state,
  result cap, and bounded query count
- numeric salvage retry after list renumbering without retargeting
- `salvage spoils` selecting only salvage-only definitions
- batch salvage aggregation and bounded query counts
- no craft/salvage material-profit cycle in the authored recipe catalog
- state payload and domain-event publication

Representative query-count tests should use a realistically large profile with
dozens of recipes, not a one-recipe fixture.

## Implemented Initial Slice

The initial implementation includes:

1. authored material, recipe, ingredient, profile, profile-membership, and
   relational salvage-yield models plus `PlayerMaterialBalance`
2. strict manifest parsing, serialization, export, and read APIs plus World
   Config list/detail pages, canonical YAML editing, and copy-delete-manifest
   controls
3. room and optional NPC workshop providers
4. the `materials`, `recipes`, `recipe`, `craft`, and `salvage` command surface
5. atomic material spending and granting, persisted guided-random output,
   request-id receipts, and bounded bulk salvage
6. aggregate durable domain events and actor/material state payloads
7. builder and player guides

## Settled Initial Decisions

- `materials` shows current unspent player material balances.
- Common materials are normalized balances, not physical items or combat stats.
- Physical gear is the primary salvage input.
- Captured or broken equipment may be physical but salvage-only.
- Crafting requires a provider, with a room workshop as the default provider.
- A particular NPC is not required.
- A crafting profile explicitly controls which recipes a provider exposes.
- Recipes in an accessible profile are available unless their conditions fail.
- Recipes select exact item definitions.
- Crafting always succeeds and item-definition RNG rolls once.
- Salvage yield ignores rolled stats.
- `salvage spoils` is the only initial bulk salvage operation.

## Deferred Decisions

- learned recipe acquisition
- material trading
- rare physical catalysts
- alternative ingredient groups
- crafting tools
- deterministic item refinement
- portable crafting stations
- player-owned workshops
- direct material drops that bypass physical salvage
- world-configurable material loss on death
- randomized salvage yields
