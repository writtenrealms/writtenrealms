# Item Definition Builder Guide

Item definitions are the WR2 item authoring path. Builders create them with
YAML in **World > Edit**. The **World > Items** screen lists item definitions and
can copy or prefill the YAML for a definition. Its Type and Equipment Type
filters can be combined to narrow the list to a specific equipment slot or
weapon category. Both filters remain side by side at mobile widths.

Use `kind: itemdefinition` for one authored item. Use `kind: itembundle` when a
mob, merchant, or spawn plan should choose from a weighted set of item definitions.

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
  room_description: An iron ration lies here.
  keywords: ration food
  food_value: 10
```

`room_description` is the line shown for the item in room look output, matching
the field name used by mob definitions.

This is the right shape for coins, rations, keys, ammunition, simple quest
objects, and any other item where every copy should be identical.

Containers, corpses, trash containers, augmented items, and randomized items do
not stack.

## Fixed Stat Items

Fixed stats are still stable. If there is no `randomization` block, two copies
from the same definition should stack as long as neither copy has been
augmented.

```yaml
kind: itemdefinition
metadata:
  slug: bronze-sword
  name: a bronze sword
spec:
  type: equippable
  description: A practical bronze blade.
  room_description: A bronze sword lies here.
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

## Monetary Value

An item value is one amount plus one authored currency:

```yaml
kind: itemdefinition
metadata:
  slug: bronze-sword
  name: a bronze sword
spec:
  type: equippable
  equipment_type: weapon_1h
  weapon_damage: 4
  cost: 25
  currency: obol
```

`cost` must be a whole number from `0` through `9,007,199,254,740,991`.
On create, omitting `currency` uses the world's current default and stores that
concrete relation. Canonical YAML exports the code explicitly so a later
default change cannot reinterpret the item. `currency` without `cost` is
invalid; set `cost: null` to remove both parts of the monetary value.

For world defaults, starting balances, merchants, rewards, and deletion rules,
see [currency-builder-guide.md](/Users/teebes/code/writtenrealms/docs/guides/currency-builder-guide.md).

## Basic Attack Messages

Weapon item definitions can customize the basic-attack verb or verb phrase with
`hit_msg_first` and `hit_msg_third`:

```yaml
kind: itemdefinition
metadata:
  slug: bronze-sword
  name: a bronze sword
spec:
  type: equippable
  equipment_type: weapon_1h
  weapon_damage: 4
  hit_msg_first: slash
  hit_msg_third: slashes
```

`hit_msg_first` is the first-person form that follows `You` and
`hit_msg_third` is the third-person form that follows the attacker's name. WR2
does not conjugate one form into the other, so author both forms. With the
example above, the attacker's combat message starts `You slash a dog`, while
another player's starts `Thibaud slashes a dog`; the normal damage suffix
follows each phrase.

Messages can be multiword phrases, such as `lash at` and `lashes at`. Author
only the phrase: do not include the actor, target, damage amount, or
punctuation. On a new definition, an omitted or blank field uses its
corresponding `hit` or `hits` default. When updating an existing definition,
omitting a field preserves its current value; set it to `""` or `null` to
restore the default.

The message comes from the equipped weapon selected for that basic-attack
strike. Normal basic attacks use the main-hand weapon; an attack-routine
offhand strike uses the offhand weapon's message. A weapon that is merely in
the inventory has no effect, and unarmed attacks use `hit` / `hits`. These
fields do not replace ability names or periodic-effect text.

## Armor Classes And Armor Values

Armor classes and armor values are separate systems.

- `armor_class` is an equip permission gate for armor and shield slots.
- `armor` is the final defensive rating the item grants in combat.

Worlds opt into authored armor classes in the world manifest:

```yaml
kind: world
spec:
  equipment:
    armor_classes:
      - key: light
        label: Light Armor
        description: Cloth, leather, linen, wicker, and flexible field gear.
        armor_multiplier: 1.0
      - key: heavy
        label: Heavy Armor
        description: Bronze helmets, reinforced cuirasses, greaves, and heavy shields.
        armor_multiplier: 1.35
    default_armor_class: light
    armor_suggestions:
      full_set_scale: 0.35
      slot_weights:
        head: 0.15
        body: 0.30
        arms: 0.10
        hands: 0.10
        waist: 0.10
        legs: 0.15
        feet: 0.10
        shield: 0.35
```

Once a world defines `spec.equipment.armor_classes`, every non-empty item
`armor_class` must match one of those keys. If the world has no authored armor
classes, WR2 keeps the legacy `heavy` armor gate for compatibility.

Class proficiencies live on the world stat profiles:

```yaml
kind: world
spec:
  stats:
    default_profile:
      armor_proficiencies: [light]
      attribute_weights:
        constitution: 3
        strength: 2
    class_profiles:
      hoplite:
        label: Hoplite
        armor_proficiencies: [light, heavy]
        attribute_weights:
          constitution: 4
          strength: 3
      mystic:
        label: Mystic
        armor_proficiencies: [light]
        attribute_weights:
          constitution: 2
          intelligence: 2
```

If a class omits `armor_proficiencies`, it inherits the `default_profile`
proficiencies. If neither the class nor the default profile declares
proficiencies, the class is unrestricted for compatibility. An explicit empty
list means that class is proficient with no authored armor classes.

Use `armor_class` and `armor` on armor and shield item definitions:

```yaml
kind: itemdefinition
metadata:
  slug: salt-stained-linothorax
  name: a salt-stained linothorax
spec:
  type: equippable
  level: 20
  equipment_type: body
  armor_class: light
  armor: 4
  resilience: 2
  attributes:
    constitution: 2
```

The equip command checks armor-class proficiency only for `head`, `body`,
`arms`, `hands`, `waist`, `legs`, `feet`, and `shield`. Weapons and accessories
ignore `armor_class` for equip permission, so omit it there unless you need it
for display or migration notes.

The `armor` value is always direct and final. A heavy item with `armor: 5`
grants 5 armor, not `5 * 1.35`. The `armor_multiplier` setting is only a
builder suggestion input for generated defaults; it does not multiply item
stats at runtime. Builders can always edit `armor` in YAML before applying the
definition.

When hand-authoring starter gear, this is the default suggestion formula:

```text
level_scale = combat.level_scale(level)
full_set_armor = ceil(level_scale * armor_suggestions.full_set_scale)
armor = ceil(full_set_armor * slot_weight * armor_multiplier)
```

With the default combat level scale and the default suggestion settings, level
20 starter gear usually lands around these values:

| Slot | Light | Heavy |
| --- | ---: | ---: |
| Head | 2 | 3 |
| Body | 4 | 5 |
| Arms | 2 | 2 |
| Hands | 2 | 2 |
| Waist | 2 | 2 |
| Legs | 2 | 3 |
| Feet | 2 | 2 |
| Shield | 5 | 6 |

## Power Analysis

The item definition details screen has a **POWER** action. It opens an
advisory analysis modal for the current item definition.

The analysis uses the world's combat formulas, rating curves, item level, slot,
fixed stats, fixed attributes, and `equipment.armor_suggestions.slot_weights`.
It reports category scores, the strongest stat drivers, basic combat metrics,
and slot reference values such as expected armor for armor and shield slots.

Power analysis does not change item stats, item quality, or runtime combat. It
is a builder aid for comparing definitions before applying further YAML edits.
Randomized attribute ranges are not included in the first pass; the modal
analyzes the fixed values on the definition.

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
  room_description: A scavenged sword lies here.
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
items are resynced to the new descriptive and gameplay values. Their `cost` and
`currency` remain concrete spawn-time snapshots; repricing a definition affects
new items only and cannot retroactively reprice player inventory or shop stock.

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

Item bundles can be used anywhere a loot source accepts `itembundle.<slug>`.
Use bundles when several mobs or spawn plans should reuse the same weighted item
choice.

WR2 has no Random Item Profile authoring model. Use a definition's
`spec.randomization` for bounded attribute rolls and an item bundle for weighted
choice among explicit definitions. Broad WR1 procedural equipment profiles do
not have a direct WR2 manifest equivalent.

## Loading And Granting Item Definitions

After applying an item definition manifest, builders can test it with `/load`:

```text
/load item bronze-sword
```

When a builder player runs that command directly, the spawned item is added to
the builder's inventory. The selector can be an item definition id or an
item definition slug.

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
