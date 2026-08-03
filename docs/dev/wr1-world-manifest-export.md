# WR1 World Manifest Export

This note documents the optional WR1-to-WR2 authored-world conversion boundary.
WR2 launches with a clean, empty database; this utility does not migrate users,
players, balances, inventories, quest progress, runtime entities, or any other
live state. WR1 conversion logic belongs in the Advent exporter, not in the WR2
runtime.

The default export contains the baseline authored-world definitions listed
below. It intentionally excludes WR1 skills as WR2 abilities and excludes
triggers. Those lossy conversions are available only through explicit opt-in
flags and are not part of the default authored-world export contract.

## Exporter Location

The exporter script lives in the Advent checkout:

```text
/Users/teebes/code/Advent/api/scripts/wr2_manifest_export.py
```

Run it from the Advent API directory:

```bash
cd /Users/teebes/code/Advent/api
python scripts/wr2_manifest_export.py <world_id> > /tmp/world.yaml
```

The positional world ID remains required for a full-world export. To include
skills and triggers in the same stream:

```bash
python scripts/wr2_manifest_export.py <world_id> \
  --include-abilities --include-triggers > /tmp/world-with-logic.yaml
```

Useful options:

- `--exact-world` exports the provided world directly instead of resolving a
  spawned world to its context.
- `--settings <module>` overrides the Django settings module.
- `--include-abilities` exports WR1 builder skills as ability manifests.
  `--include-skills` is an equivalent alias.
- `--include-triggers` exports WR1 room actions and mob reactions as trigger
  manifests.
- `--zone-id <id>` exports one WR1 zone and its rooms instead of a full world.

## Default Export Shape

The script emits a YAML manifest stream. By default, a full-world export
contains:

- currencies
- item definitions
- zones
- rooms, details, exits, and doors
- mob definitions
- the world document

These use the current WR2 kinds and references: `itemdefinition`,
`mobdefinition`, `room@<relative_id>`, `zone@<relative_id>`, and
`itemdefinition.<slug>`. Rooms include explicit `spec.coordinates`, and the
world document selects `gold` as its default currency and carries WR1 starting
Gold under `starting_balances.gold`. The exporter writes YAML only to stdout;
lossy conversion warnings go to stderr, so normal shell redirection produces a
clean manifest file.

The exporter also materializes WR1's runtime-computed item weapon damage and
armor, renames Mana fields to Energy, moves fixed item attributes under
`spec.attributes`, and preserves astral Unicode as literal UTF-8. Unsupported
legacy template-inventory, procedural-drop, merchant, crafter/upgrader, elite,
and teaching behavior is omitted with a review warning instead of being
written as fields that WR2 rejects.

Logic conversion is opt-in:

- `--include-abilities` adds WR1 builder skills as `kind: ability` documents.
- `--include-triggers` adds room action and mob reaction trigger documents.

The skill export only covers world-authored `builders.Skill` records. It does
not yet export WR1 hard-coded core or flex class skills from the Advent Python
skill modules. Those can be mapped later if we decide they are worth carrying
forward.

## Legacy Death-Destination Boundary

WR1 `Procession` records linked factions to one or more death rooms and were
exposed through Zone Config. They are historical conversion inputs only: WR2
has no procession manifest kind or runtime contract, and neither the default
export nor the optional logic flags should emit them.

A converter may preserve one fixed authored WR1 death room as the world
`spec.death_room` fail-safe when its meaning is unambiguous. If every legacy
route can be proven to converge on one room, that room may be used as the
fail-safe; otherwise keep the global death room and emit a builder-review
warning. Do not infer `spec.death_routing` from legacy `death_route` values,
spatial modes, procession destinations, player marks, historical deaths,
runtime faction assignments, or any other live state.

Converted instance templates always emit `death_routing_source: local`.
Builders who want conditional routing after import must author a new ordered
WR2 policy using the shared condition framework and explicit character-state,
core-faction, class/archetype, player-level, or origin-zone selectors.

## Skill Mapping Strategy

WR1 custom skills were real-time and second-based. WR2 abilities are
round-based and manifest-driven, so this mapping is intentionally best effort.
The exporter preserves lossy WR1 details under
`spec.requirements.wr1_export` for later review.

Direct field mappings:

- `code` becomes `metadata.slug` and the first command verb. Manifest slugs use
  hyphens; command verbs use underscores because WR2 command verbs do not allow
  hyphens.
- `name` becomes `metadata.name` and component display text.
- `level` becomes `spec.availability.min_level`, with WR1 values below `1`
  exported as `1`.
- `intent` selects the WR2 target:
  - `damage` becomes a hostile current-target ability.
  - `healing` becomes an ally ability that defaults to self.
  - `self_healing` becomes a self-targeted ability.
- `cost_type`, `cost`, and `cost_calc` become `spec.cost`.
- WR1 `mana` costs become WR2 `energy` costs; `perc_base` and `perc_max`
  become `percent_base` and `percent_max` respectively.
- `cast_time` seconds become `spec.cast_time.rounds`.
- `cooldown` seconds become `spec.cooldown.rounds`.

Timing conversion uses `WR1_SECONDS_PER_WR2_ROUND = 3.0` in the exporter:

```text
rounds = ceil(seconds / 3.0)
```

This is only a conversion heuristic. Round cooldowns should be reviewed during
world tuning because WR1 cooldown seconds and WR2 encounter rounds are not
equivalent pacing models.

Damage and healing mapping:

- WR1 physical damage uses `basic_physical`.
- WR1 magical damage uses `basic_ability`.
- WR1 healing and self-healing use `basic_heal`.
- WR1 `damage_calc: normal` exports `damage` as a WR2 profile `multiplier`.
- WR1 `damage_calc: fixed` exports a profile override that zeros normal scaling
  and uses `minimum` to approximate a fixed output amount.

Effect mapping:

- `dot`, `hot`, and `stun` are exported as WR2 effect components.
- DOT and HOT effects tick once per WR2 round.
- Unsupported WR1 effects such as `sleep`, `absorb`, `buff`, `debuff`, `haste`,
  `invisibility`, `stealth`, `thorns`, `summon`, `dispel`, and `purge` are not
  translated yet. The exporter records a review note in `wr1_export.notes`.

If a skill has no supported direct output or effect, the exporter emits a
zero-output placeholder component so the ability manifest still validates. This
is a review flag, not a finished ability.

## Preserved WR1 Metadata

Every exported ability includes a `spec.requirements.wr1_export` block with the
source data needed to revisit the mapping:

- WR1 source model, id, and code
- original intent, arguments, requirements, learn conditions, help text
- original damage, cost, cooldown, cast time, and effect fields
- consumed item template reference when available
- exporter notes for lossy or unsupported mappings

The WR2 runtime currently ignores this metadata. It exists so cleanup work in
this repo can delete legacy skill references without losing conversion context.

## Iteration Notes

When ability schema or combat formula behavior changes in WR2, update the
mapping in `serialize_skill_ability()` and the small helpers near it in the
Advent exporter.

Validate exporter changes at minimum with:

```bash
python -m py_compile /Users/teebes/code/Advent/api/scripts/wr2_manifest_export.py
python /Users/teebes/code/Advent/api/scripts/wr2_manifest_export.py --help
```

For behavioral validation, export a known WR1 world, import the resulting YAML
through **Create World** followed by **World > Edit World**, and inspect any
ability documents with `wr1_export.notes`. Converted room references remain the
stable WR1-relative identities; they do not need to contain `room@1`. On a
pristine target, WR2 replaces its scaffold room and moves the Lobby's offline
Builder character and editor bookmark to the manifest's declared starting
room. Those notes are the practical checklist for manually tuning abilities
after import.
