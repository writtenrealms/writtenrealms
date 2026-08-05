# Social Builder Guide

Socials are world-authored emote commands such as `wave`, `nod`, or `salute`.
They produce one message for the actor, one for a direct target when present,
and one shared witness message for the other players in the room.

Social definitions belong to the base world. Rank 3+ builders can author them
there; instance worlds inherit the same catalog and expose it read-only.

## Minimal Manifest

```yaml
kind: social
metadata:
  world: world.1
  command: wave
spec:
  priority: 10
  targetless:
    self: You wave.
    others: "{{ Actor }} waves."
  targeted:
    self: "You wave at {{ target }}."
    target: "{{ Actor }} waves at you."
    others: "{{ Actor }} waves at {{ target }}."
```

`metadata.command` is the portable identity. It must match
`[a-z][a-z0-9_-]{0,63}` and is unique ignoring case within the base world.
Applying the same command updates that social. To rename a command, create the
new social and delete the old one. A base world can define at most 512 socials;
this hard bound keeps runtime lookup and cache memory predictable.

Builders can also create, inspect, edit, and delete definitions in
**World > Socials**. **World > Edit World** accepts the canonical YAML form.

## Message Sets

A social can provide either or both of these complete modes:

- `targetless.self`: what the actor sees
- `targetless.others`: what other players in the room see
- `targeted.self`: what the actor sees after targeting someone
- `targeted.target`: what the direct target sees
- `targeted.others`: what other players in the room see

A targetless mode requires both targetless messages. A targeted mode requires
all three targeted messages. A definition must retain at least one complete
mode.

If a player supplies a target to a social that has no targeted mode, WR2 uses
the targetless mode instead. If the social has only a targeted mode, using it
without a target returns `A target is required.`

The standard player communication mute blocks social use. A target player's
personal mute list also rejects a direct targeted social from that actor.

On update, omitting a message group preserves it. Within an included mapping,
omitted fields also retain their existing values. Set a group to `null` or `{}`
to clear the whole mode; the resulting definition must still contain another
complete mode.

## Command Resolution And Priority

Exact command names always win. Players may also type a command prefix. When a
prefix matches more than one social, the definition with the higher
`spec.priority` wins; ties use command name and then database id for a stable
result. Priority is an integer from `0` through `1,000,000`.

Socials are fallback text commands. Registered commands, dynamic ability
commands, and eligible contextual command triggers are resolved before a
social with the same text.

## Message Templates

Messages use validated, sandboxed Jinja templates. Targetless messages can use
the actor variables; targeted messages can use both actor and target variables.

Available actor variables:

- `actor` and `Actor`
- `actor_title` and `actor_state`
- `actor_subject_pronoun`, `actor_object_pronoun`
- `actor_possessive_adjective`, `actor_possessive_pronoun`
- `actor_reflexive_pronoun`

The target equivalents use the `target` prefix, including `target`, `Target`,
`target_title`, and `target_state`. Target variables are rejected in targetless
messages because no target exists in that mode.

Templates are parsed and compiled during authoring so malformed syntax or
unknown variables do not first fail in the game-processing path. Each source
template and each rendered result is limited to 2,000 characters. Templates
support bounded interpolation and conditionals; loops, calls, filters, imports,
assignments, arithmetic, explicit concatenation, and collection literals are
rejected so authoring cannot materialize unbounded intermediate values on the
game-processing path. Character names, titles, pronouns, and state trees are
also copied into a bounded render context before a template runs.

## Partial Updates And Deletion

Canonical exports include `metadata.id` and `metadata.key` as safeguards, but
portable imports need only `metadata.command`. When an id or key is present, it
must identify the same social as the command.

```yaml
kind: social
metadata:
  world: world.1
  command: wave
spec:
  priority: 25
```

Delete by command:

```yaml
kind: social
operation: delete
metadata:
  world: world.1
  command: wave
```

World export writes social manifests in command order. Import and the manifest
apply endpoint use the same validation and base-world ownership rules as the
REST builder screen.

## Mob Reactions To Socials

A mob definition can react when a player directly targets one of its spawned
mobs with a social:

```yaml
kind: trigger
metadata:
  world: world.1
  name: Guard Returns A Salute
spec:
  scope: world
  kind: event
  target: mobdefinition.guard
  event: social
  match: salute
  script: say Your courtesy is noted.
  conditions:
    eq:
      - actor.archetype
      - diplomat
  display_action_in_room: false
  gate_delay: 5
  order: 0
  is_active: true
```

For `event: social`, each literal in `spec.match` is compared exactly with the
resolved social command. A player abbreviation still emits the full command,
so `sal` can run `salute` and match `salute`; it does not match a trigger whose
literal is `sal`.

The trigger actor is the player. Only the directly targeted mob is considered;
targetless socials, player targets, mob-originated socials, and bystander mobs
do not run social reaction triggers. Put all conditional logic in
`spec.conditions` using the shared WR2 condition framework. Do not create a
social-specific predicate format.

See [Trigger Builder Guide](trigger-builder-guide.md) and
[Condition Builder Guide](condition-builder-guide.md) for the full contracts.

## Runtime And Scaling

- Each base world's social catalog is cached and versioned. Its compact command
  index and bounded per-definition cache shards keep every shared cache item
  comfortably below common backend item limits. Instances reuse the base-world
  catalog, and committed authoring changes invalidate it; rolled-back changes
  are never published to shared cache state.
- Validated templates are compiled in a bounded cache. At execution, WR2
  renders once for each applicable audience cohort: actor, direct target, and
  witnesses. One witness rendering is reused for every witness recipient.
- Witness recipients are collected with one indexed room-player fanout query,
  rather than one query or event per player.
- A targeted mob reaction filters directly to that mob before trigger
  evaluation. WR2 does not scan every mob in the room for every social.

## WR1 Conversion Boundary

The optional WR1 converter may translate authored social definitions into
these manifests. It does not transfer players, mute lists, command history, or
runtime events/state. The exact positional-to-named mapping is maintained in
[the WR1 conversion architecture notes](https://github.com/writtenrealms/writtenrealms/blob/main/docs/architecture/yaml-manifest-system.md#optional-wr1-authored-world-conversion-notes).

## Related Docs

- [Player Socials Guide](../players/socials.md)
- [Trigger Event Subscriptions](https://github.com/writtenrealms/writtenrealms/blob/main/docs/architecture/trigger-event-subscriptions.md)
- [Trigger Matching DSL](https://github.com/writtenrealms/writtenrealms/blob/main/docs/architecture/trigger-matching-dsl.md)
- [Instance System](https://github.com/writtenrealms/writtenrealms/blob/main/docs/architecture/instance-system.md)
