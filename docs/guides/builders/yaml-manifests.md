# YAML Manifest Guide

Use **World > Edit** to paste and apply one or more Written Realms YAML
manifests. **World > Config** and entity detail screens such as Rooms, Mobs,
Items, Factions, and Abilities also expose canonical YAML that is safe to copy,
edit, and apply directly.

## Basic Shape

A manifest is a YAML mapping with a `kind`, identity under `metadata`, and
authored values under `spec`:

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: currency
metadata:
  code: obol
spec:
  name: Obol
  plural_name: Obols
  description: The common coin of Phalanx.
```

`apiVersion` is optional. New examples and copied canonical YAML use
`writtenrealms.com/v1alpha3`. The `kind` value is case-insensitive, although
lowercase is the canonical style.

When a save reloads canonical YAML, multiline text is emitted as a literal
`|` block and Unicode punctuation remains readable rather than becoming escape
sequences. Ordinary prose fields also remain on one line when they fit within
the canonical 120-column width. This keeps descriptions convenient to edit
while preserving their paragraph breaks.

## Applying Multiple Documents

Separate documents with `---`. They are applied in source order, so place
definitions before documents that depend on them:

```yaml
apiVersion: writtenrealms.com/v1alpha3
kind: currency
metadata:
  code: obol
spec:
  name: Obol
  plural_name: Obols
---
apiVersion: writtenrealms.com/v1alpha3
kind: world
spec:
  default_currency: obol
  starting_balances:
    obol: 12
```

A multi-document request is atomic. If any document fails validation or
permission checks, changes made by earlier documents in that request are
rolled back. Error messages identify the failing document number and kind.

## Supported Kinds

| Kind | What it authors | Guide |
| --- | --- | --- |
| `worldbundle` | A complete base world and instance-family import wrapper | [Instances](instance-builder-guide.md#moving-a-family-from-development-to-production) |
| `world` | World configuration | [World Config](world-config-builder-guide.md) |
| `currency` | Currency definitions | [Currencies](currency-builder-guide.md) |
| `zone` | World zones | [Rooms and Doors](room-builder-guide.md) |
| `room` | Rooms, exits, details, flags, and doors | [Rooms and Doors](room-builder-guide.md) |
| `path` | Ordered movement and patrol paths | [Spawn Plans](spawn-plan-builder-guide.md#path-manifests) |
| `itemdefinition` | Item definitions | [Items](item-definition-builder-guide.md) |
| `itembundle` | Weighted item-definition choices | [Item Bundles](item-definition-builder-guide.md#item-bundles) |
| `merchantprofile` | Merchant inventory and behavior | [Merchants](merchant-builder-guide.md) |
| `craftmaterial` | Crafting materials | [Crafting](crafting-builder-guide.md#materials) |
| `craftingrecipe` | Crafting recipes | [Crafting](crafting-builder-guide.md#recipes) |
| `craftingprofile` | Crafting providers and capabilities | [Crafting](crafting-builder-guide.md#profiles-and-providers) |
| `trainerprofile` | Reusable ability-training catalogs and profile-scoped choice limits | [Ability Trainers](ability-builder-guide.md#ability-trainers) |
| `faction` | Core and reputation factions | [Factions](faction-builder-guide.md) |
| `mobdefinition` | Mob definitions | [Mobs](mob-definition-builder-guide.md) |
| `spawnplan` | Mob and item population plans | [Spawn Plans](spawn-plan-builder-guide.md) |
| `ability` | One ability definition | [Abilities](ability-builder-guide.md) |
| `abilities` | A coordinated ability set | [Abilities](ability-builder-guide.md) |
| `social` | Social and emote commands | [Socials](social-builder-guide.md) |
| `quest` | A quest template | [Quests](quest-builder-guide.md) |
| `questarc` | A coordinated quest arc | [Quest Reference](quest-reference.md) |
| `trigger` | Command, event, or policy triggers | [Triggers](trigger-builder-guide.md) |

## Apply And Delete

`operation` defaults to `apply`. Depending on the kind, applying a document
creates a definition or updates the existing entity identified by its portable
metadata.

```yaml
operation: apply
```

Many detail screens can also copy a deletion manifest. Deletion rules are
kind-specific and may reject removal while other content still references the
entity. Start with the generated delete YAML or the relevant kind guide rather
than constructing one from memory.

## Portable References

Prefer stable references from copied canonical YAML:

- `room@42`, `zone@2`, and `path@7` identify authored spatial content.
- `itemdefinition.training-spear`, `mobdefinition.guard`, and similar slug
  references identify definitions.
- Currency and faction references use their authored codes.

Database IDs such as `room.123` may be accepted as import aliases in some
contexts, but they are installation-specific and should not be used in
portable source files. Bare room numbers are ambiguous and rejected in
persisted authored data; write `room@123` when `123` is the room's relative
id. Keep an entity's stable reference unchanged when editing it.

## Scope And Permissions

The editor applies ordinary documents to the world currently selected in the
builder. Normal builder rank and assignment checks still apply. Definitions
owned by a base world may be read-only from an instance template.

### World Bundle Imports

`worldbundle` is not an ordinary content document. It must be the first
document in a complete family stream and wraps the base world, its authored
instance templates, and cross-scope links. Importing one requires a rank 3 or
higher builder and an authored base world. See the [Instance Builder
Guide](instance-builder-guide.md#moving-a-family-from-development-to-production)
before importing a family bundle.

## Troubleshooting

- Copy the current YAML from the entity detail screen before making a large
  edit. It gives you the canonical field and reference shapes.
- In a batch error, start with the reported document number. Earlier documents
  may be valid even though the whole request was rolled back.
- If a reference cannot be resolved, verify its kind prefix and define the
  dependency earlier in the stream.
- If deletion is blocked, search the relevant guide for dependency and cleanup
  rules before retrying.
