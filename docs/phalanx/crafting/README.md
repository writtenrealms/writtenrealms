# Phalanx Crafting Manifests

This directory contains the authored crafting catalog for the forty-five T2
Phalanx item definitions in `docs/phalanx/gear/t2/` and the thirty-five T3
definitions for the four remort classes in `docs/phalanx/gear/t3/`.

The catalog consists of:

- four shared crafting materials in `materials.yaml`
- one recipe for every T2 item, split into five class-oriented files
- one recipe for every remort-class T3 item, split into four class files
- one `camp-workshop` profile exposing all forty-five recipes
- a Phalanx-specific room manifest placing that profile in Kerameikos
- T2 and T3 item-definition overlays that make crafted gear salvageable
- `oath-court-services.yaml`, which adds four room-provided T1 shops, eight
  class-and-tier-specific workshops, and optional decorative quartermasters
  to `zone@9`
- five optional Persian spoils definitions that supply crafting materials
- an optional Persian mob loot-table replacement that drops captured spoils

Accessories are intentionally absent from the catalog.

## Application order

Apply the manifests in this order:

1. Apply `docs/phalanx/phalanx-currency.yaml` so `obol` exists and is selected as
   the default.
2. Apply the T1, T2, and T3 item definitions in `docs/phalanx/gear/` and, if
   using the optional material-source content, the base
   `mobdefinition.persian` manifest.
3. Apply `materials.yaml`.
4. Apply the five `t2-*-recipes.yaml` files.
5. Apply the four remort-class `t3-*-recipes.yaml` files.
6. Apply `t2-salvage.yaml`, `t3-salvage.yaml`, and, optionally,
   `persian-spoils.yaml`.
7. Optionally apply `persian-loot.yaml` after both the Persian mob and all five
   spoils definitions exist.
8. Apply `t2-workshop.yaml` for the shared Kerameikos catalog.
9. In Phalanx world 23, apply `oath-court-services.yaml` after `zone@9` and its
   service rooms exist.
10. Apply `phalanx-workshop-room.yaml` if the shared Kerameikos workshop is
    desired.

The salvage files contain patch-style `itemdefinition` manifests. Each patch
identifies an existing definition by slug and adds only `spec.salvage`; it does
not duplicate or replace the item's combat data.

`oath-court-services.yaml` gives each remort wing one T1 field outfitter, one
T2 workshop, and one T3 workshop. The four Merchant Profiles are attached
directly to their exchange rooms, so players can shop there without a mob or
Spawn Plan. The retained nonattackable quartermasters are decorative only,
and their zone-local Spawn Plan is inactive by default. Enable it when that
flavor is wanted; the room remains the sole merchant provider. Crafting
profiles are attached directly to the eight rooms named by the `zone@9`
builder notes. Merchant rooms automatically expose `list` and `offer`, while
workshop rooms expose `craft` and `salvage`; no duplicate room actions or
triggers are required. `shop` remains an alias for `list`.

The catalog currently has four Oath Court T1 merchants: Warlord, Tidecaller,
Mystic, and Moonstalker. Hoplite T1 gear exists, but no Hoplite Merchant
Profile or dedicated shop room has been authored yet. Add both before treating
the services manifest as a five-shop catalog.

`persian-spoils.yaml` and `persian-loot.yaml` are local starting content rather
than required crafting contracts. The spoils are short, inspectable physical
items that cannot be equipped and are selected by `salvage spoils`. The loot
manifest gives `mobdefinition.persian` one independent `captured-spoils` roll.
On a successful roll it chooses one definition from the weighted pool.

`spec.loot` is replacement-oriented in mob-definition manifests. Applying
`persian-loot.yaml` therefore replaces the Persian definition's complete loot
table; it does not merge only `captured-spoils`. The supplied base Persian has
no other loot, so the file is safe as written. If other drops are added first,
copy those entries into this manifest (or copy `captured-spoils` into the full
mob manifest) before applying it.

The initial loot probability is 35 percent. Builders should tune that chance,
the source weights, and the fixed salvage yields from observed acquisition
pace. Omitting `persian-loot.yaml` leaves the Persian mob's loot unchanged and
allows another encounter, quest, or drop table to provide the same spoils.

`t2-workshop.yaml` keeps the recipe profile reusable rather than attaching it
to an arbitrary room. For Phalanx world 23,
`phalanx-workshop-room.yaml` creates `Armorer's Workshop` north of room 311,
connects it to `Lane by the Clay-Washing Yard`, and attaches the profile. This
places the service just inside the Thriasian Gate, near the barracks, kilns,
water, and fuel traffic, without turning a road junction into a crafting room.

For another world, select or create its intended workshop room and attach the
profile explicitly:

```yaml
spec:
  crafting:
    profile: craftingprofile.camp-workshop
```

Until a room or eligible NPC exposes that profile, the recipes exist but are
not available through player crafting commands.

## Material and Obol normalization

T2 uses two material units per documented crafting-cost weight:

| Output | Weight | Material units | Crafting fee |
|---|---:|---:|---:|
| Body armor or two-handed weapon | 10 | 20 | 300 Obols |
| Head, legs, one-handed weapon, or shield | 5 | 10 | 150 Obols |
| Arms, hands, waist, or feet | 3 | 6 | 90 Obols |

Each recipe declares the fee with `spec.cost` and identifies the custom
currency with `spec.currency: obol`. Apply `docs/phalanx/phalanx-currency.yaml`
before these recipes so the referenced currency exists.

Every seven-piece armor set therefore costs 64 material units. Every supported
weapon loadout costs 20 units, whether it is one two-handed weapon, a weapon
and shield, or two one-handed weapons. A complete armor-and-weapon loadout
costs 84 material units and 1,260 Obols for every class. A seven-piece armor
set costs 960 Obols and each supported weapon loadout costs 300 Obols.

The Obol fee is paid for every craft, including attempts to improve a random
roll. Salvaging returns only the authored materials and never refunds the fee.
Crafted output definitions retain zero merchant value so unwanted rolls do not
undo the intended currency sink.

T2 salvage returns 20 units from a complete armor set and 6 from a complete
weapon loadout, or 26 of 84 units overall (about 31 percent). Salvage yield is
fixed by the item definition and does not depend on the item's random roll.

T3 starts from a simple two-times-T2 baseline. Every corresponding T2 material
quantity, Obol fee, and fixed salvage yield is doubled:

| Output | Weight | Material units | Crafting fee | Salvage yield |
|---|---:|---:|---:|---:|
| Body armor or two-handed weapon | 10 | 40 | 600 Obols | 12 |
| Head, legs, one-handed weapon, or shield | 5 | 20 | 300 Obols | 6 |
| Arms, hands, waist, or feet | 3 | 12 | 180 Obols | 4 |

A complete T3 armor-and-weapon loadout costs 168 material units and 2,520
Obols, then salvages for 52 material units. This preserves the T2 construction
mixes, weapon-build parity, and roughly 31 percent recovery ratio. It is an
initial T3 tuning baseline rather than an established economy rule and should
be adjusted from playtest data as a single coefficient.
