# WR1 Skill to WR2 Ability Export

This note documents the WR1-to-WR2 migration boundary for skills. The intent is
to keep WR1 compatibility logic in the Advent exporter, not in the WR2 runtime,
so WR2 can continue replacing legacy skill concepts with abilities.

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

Useful options:

- `--exact-world` exports the provided world directly instead of resolving a
  spawned world to its context.
- `--settings <module>` overrides the Django settings module.
- `--no-triggers` skips WR1 room actions and mob reactions.
- `--no-skills` skips WR1 builder skills and therefore emits no ability
  manifests.

## Current Export Shape

The script emits a YAML stream of WR2 manifest documents. It currently exports:

- currencies
- item templates
- WR1 builder skills as WR2 `kind: ability` documents
- zones
- rooms, details, exits, doors, and room inventory references
- mob templates
- room action and mob reaction triggers
- the world document

The skill export only covers world-authored `builders.Skill` records. It does
not yet export WR1 hard-coded core or flex class skills from the Advent Python
skill modules. Those can be mapped later if we decide they are worth carrying
forward.

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
- `cooldown` seconds become `spec.cooldown.rounds`.

Timing conversion uses `WR1_SECONDS_PER_WR2_ROUND = 3.0` in the exporter:

```text
rounds = ceil(seconds / 3.0)
```

This is only a migration heuristic. Round cooldowns should be reviewed during
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
this repo can delete legacy skill references without losing migration context.

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
through the WR2 manifest flow, and inspect any ability documents with
`wr1_export.notes`. Those notes are the practical checklist for manually tuning
abilities after import.
