# Item Definition Builder Guide

Item definitions are the WR2 item authoring path. Builders create them with
YAML in **World > Edit**. The **World > Items** screen lists item definitions and
can copy or prefill the YAML for a definition.

Use `kind: itemdefinition` for one authored item. Use `kind: itembundle` when a
mob, merchant, or loader should choose from a weighted set of item definitions.

## Plain Stackable Items

An item definition with no `randomization` creates stable items. Stable,
non-container items stack in inventories and room views.

```yaml
kind: itemdefinition
metadata:
  slug: iron-ration
  name: an iron ration
spec:
  type: food
  description: A compact ration wrapped in waxed cloth.
  ground_description: An iron ration lies here.
  keywords: ration food
  food_value: 10
```

This is the right shape for coins, rations, keys, ammunition, simple quest
objects, and any other item where every copy should be identical.

Containers, corpses, trash containers, upgraded items, augmented items, and
randomized items do not stack.

## Fixed Stat Items

Fixed stats are still stable. If there is no `randomization` block, two copies
from the same definition should stack as long as neither copy has been upgraded
or augmented.

```yaml
kind: itemdefinition
metadata:
  slug: bronze-sword
  name: a bronze sword
spec:
  type: equippable
  description: A practical bronze blade.
  ground_description: A bronze sword lies here.
  keywords: bronze sword blade weapon
  equipment_type: weapon_1h
  weapon_damage: 4
  attributes:
    strength: 2
```

Use `attributes` for world-defined attributes such as `strength`,
`dexterity`, `intelligence`, or `constitution`. Use direct item properties such
as `weapon_damage`, `equipment_type`, `armor_class`, `cost`, `currency`, and
`food_value` for ordinary item fields.

Worlds can define different attribute names, but the common WR1-style
names are good defaults for most fantasy worlds. If the world does not define an
attribute, that key contributes nothing to combat. This is intentional:
item YAML should not make the world fail to boot because an attribute was
renamed or removed.

## Random Stat Items

Add `randomization.attributes` when each spawned copy should roll different
attribute values. Randomized items are always shown as separate lines, even
if two copies happen to roll the same numbers.

```yaml
kind: itemdefinition
metadata:
  slug: scavenged-sword
  name: a scavenged sword
spec:
  type: equippable
  description: A blade assembled from mismatched salvage.
  ground_description: A scavenged sword lies here.
  keywords: scavenged sword blade weapon
  equipment_type: weapon_1h
  weapon_damage: 4
  attributes:
    strength: 1
  randomization:
    attributes:
      - key: strength
        min: 1
        max: 4
        mode: favor_low
```

The fixed `attributes` value is added to the roll. In the example above,
`strength` is always at least `2`: fixed `1` plus a random `1-4`.

Supported randomization modes:

- `uniform`: every value in the range is equally likely.
- `favor_low`: lower values are more likely.
- `favor_high`: higher values are more likely.

Use `curve` to make `favor_low` or `favor_high` stronger. `curve: 1.0` is the
default. Higher values make the favored side more likely.

```yaml
randomization:
  attributes:
    - key: intelligence
      min: 1
      max: 10
      mode: favor_high
      curve: 1.5
```

## Definition Edits

Stable definition-backed items are meant to behave like authored copies of the
definition. When a stable item definition is edited, existing unmodified spawned
items are resynced to the new definition values.

Randomized items keep their rolled attributes. Upgraded and augmented
items are treated as modified instances and do not stack or get reset by later
definition edits.

The UI stacks by a backend-provided `stack_key`. Builders do not need to manage
that key in YAML.

## Item Bundles

Use an item bundle when a source should choose among multiple item definitions.

```yaml
kind: itembundle
metadata:
  slug: bandit-weapon-drop
  name: Bandit weapon drop
spec:
  entries:
    - item_definition: bronze-sword
      weight: 5
      min_quantity: 1
      max_quantity: 1
      probability: 100
    - item_definition: scavenged-sword
      weight: 2
      min_quantity: 1
      max_quantity: 1
      probability: 100
```

`weight` controls which entry is chosen relative to the other entries.
`probability` controls whether that entry is eligible at all.

## Loading And Granting Item Definitions

After applying an item definition manifest, builders can test it with `/load`:

```text
/load item bronze-sword
```

When a builder player runs that command directly, the spawned item is added to
the builder's inventory. The selector can be an item template id, an item
template slug, an item definition id, or an item definition slug.

Trigger scripts usually should not use direct `/load` when the item is meant
for the triggering player. Use `/grantitem` instead:

```yaml
script: /cmd room -- /grantitem {{ actor_key }} bronze-sword
```

`/grantitem <target> <item>` resolves the target in the issuer's current room
and puts the item into that target's inventory. This is the preferred command
for pledge rewards, starter gear, quest rewards, and other item grants.

For multi-item rewards, use the delimited form:

```yaml
script: /cmd room -- /grantitem {{ actor_key }} -- bronze-sword bronze-helm bronze-boots
```

The target is before `--`; item selectors are listed after it. The grant is
all-or-nothing: every item selector must resolve before any item is spawned.

Use room `/load` only when the item should appear on the room floor:

```yaml
script: /cmd room -- /load item bronze-sword
```

For full trigger scripting behavior, see
[trigger-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/trigger-builder-guide.md).
