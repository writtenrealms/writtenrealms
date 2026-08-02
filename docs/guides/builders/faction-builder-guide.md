# Faction Builder Guide

Factions are authored WR2 world content. Use them when identity, diplomacy,
standing, starting rooms, or faction-aware mob behavior needs to be shared
across systems.

Use **core factions** for major identity such as race, nation, origin, order, or
campaign side. A character or mob should have at most one core faction.

Use **reputation factions** for mutable standing with towns, guilds, enemy
forces, institutions, or NPC groups. A character or mob may have many reputation
factions.

Do not use reputation factions as one-off spawn labels. Use spawn entry slugs,
traits, or instance goal metadata for temporary cohorts.

## Builder UI

Open **Config -> Factions** to list faction definitions for a world. The list
can be filtered by faction type and playable status.

Open a faction to inspect its YAML, copy a delete manifest, or edit the YAML
inline. Saving applies the manifest through the same WR2 manifest loader used by
the world Edit screen.

Use **Add** on the Factions list to start from a reputation faction template.
For core factions, change `spec.type` to `core` and add any playable, starting
room, death room, or language fields the world needs.

## Create A Core Faction

```yaml
kind: faction
metadata:
  code: human
  name: Human
spec:
  type: core
  description: Humans are adaptable and numerous.
  playable: true
  starting_room: room@1
  death_room: room@1
  default_languages:
    - common
```

Fields:

- `metadata.code` is the stable builder-facing faction code.
- `spec.type` is `core` or `reputation`.
- `playable` controls whether character creation may offer this core faction.
- `starting_room` and `death_room` are optional stable
  `room@<relative_id>` references. They continue to identify the same rooms
  when those rooms move.
- `default_languages` records language seeds for future communication behavior.

## Create A Reputation Faction

```yaml
kind: faction
metadata:
  code: ashwick
  name: Ashwick
spec:
  type: reputation
  description: The town council and its watch.
  ranks:
    - standing: -100
      name: Hated
    - standing: 0
      name: Neutral
    - standing: 100
      name: Trusted
```

Ranks are display thresholds. Reputation values themselves live on players,
mobs, and mob definitions as faction assignments.

## Configure Character Creation

World config controls player core faction policy:

```yaml
kind: world
spec:
  player_creation:
    core_faction:
      mode: choose_required
      default: human
      options:
        - human
        - elf
        - orc
```

Modes:

| Mode | Behavior |
| --- | --- |
| `none` | New players receive no core faction. |
| `fixed_default` | New players receive `default`; no choice is shown. |
| `choose_required` | New players must choose one available core faction. |
| `choose_optional` | New players may choose one core faction or no core faction. |

If `options` is omitted, character creation uses every playable core faction.
If `options` is present, only those playable core factions are offered, in that
order.

## Assign Mob Definition Factions

Mob definitions can declare authored faction assignments directly:

```yaml
kind: mobdefinition
metadata:
  slug: blackfin-raider
  name: a Blackfin raider
spec:
  type: humanoid
  aggression: normal
  factions:
    core: orc
    reputation:
      blackfin: 100
      town_watch: -50
```

Rules:

- `core` must reference a core faction code.
- `reputation` keys must reference reputation faction codes.
- reputation values are integer standings.
- spawned mobs receive concrete faction assignments copied from the definition.

When a mob definition is edited, WR2 resyncs definition-authored faction
assignments on existing spawned mobs while preserving runtime assignments from
other sources.

## Adjust Reputation From Quests

Use `adjust_reputation` in quest rewards or step effects:

```yaml
effects:
  - type: adjust_reputation
    faction: ashwick
    amount: 5
```

`faction` must be a reputation faction. `amount` may be positive or negative.
Core faction changes should use a dedicated future effect, not
`adjust_reputation`.
