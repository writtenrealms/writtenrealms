---
name: wr-mob-describer
description: Fill or revise Written Realms mob definition names, room descriptions, and detailed descriptions from database mob definition IDs and existing authoring fields. Use when the user wants Codex to populate, draft, complete, revise, or review text-MUD creatures or NPCs using builders.MobDefinition rows, mob IDs, names, notes, descriptions, or room descriptions in the Written Realms style.
---

# WR Mob Describer

Use this skill as a local authoring assistant for Written Realms mob definitions. Treat bare IDs as `builders.MobDefinition.id` database IDs unless the user says otherwise.

## Resources

- Read `references/mob-description-style-prompt.md` before drafting or revising any mob text. Treat it as the primary style contract.
- Use `scripts/db_mob_context.py` to inspect, update, and validate mob definitions through the Dockerized Django backend.

## Workflow

1. Run `scripts/db_mob_context.py context --mob-definition-id <id>`. Always read the returned world context before drafting. It includes the current world's description; for an instance world, it also includes the base world's description.
   - If Docker requires sudo, set `WR_MOB_DESCRIBER_DOCKER_CMD="sudo docker compose"`.
   - Pass `--world-id <id>` when the user provides a world ID, as a guard against editing the wrong world.
2. Use only the currently defined values of `name`, `notes`, `description`, and `room_description` as mob-specific source material. Use the world description as broader setting context. For an instance world, use its description as the local lens and its base world's description as the broader setting anchor. Do not use mechanics, stats, keywords, factions, or other model fields unless the user explicitly supplies them as authoring context.
3. Treat a blank name, `a new mob`, or `Unnamed Mob` case-insensitively as a missing name. Treat a blank `description` as missing. Treat `room_description` as missing when it is blank or contains only the creation default `<name> is here.`, ignoring capitalization and whitespace differences.
4. Stop without generating or applying anything when none of the four mob-specific source fields contains usable material. A world description supplies setting context but does not by itself identify a mob. Report that the mob definition needs at least one authoring clue.
5. Stop and report that the definition is already complete when no target fields are missing, unless the user explicitly asked for a revision.
6. Generate every missing target field together so the three components agree:
   - `name`
   - `room_description`
   - `description`
7. Preserve populated target fields exactly. Use them to constrain missing fields even when their prose does not perfectly match the style prompt. The creation-default `<name> is here.` room description is not populated content and may be replaced without `--allow-overwrite`. Overwrite any other populated field only when the user explicitly asks for a revision.
8. Infer conservatively. Treat `notes` as author intent and constraints, not prose that must be copied. Do not invent named lore, affiliations, props, surroundings, or history unsupported by the available fields.
9. Preview the proposed fields, then apply them with `scripts/db_mob_context.py apply`. Use `--allow-overwrite` only for an explicitly requested revision.
10. Run `scripts/db_mob_context.py validate --mob-definition-id <id>` after applying. Fix warnings in newly generated fields. Report but do not silently rewrite warnings caused by preserved fields.

For bulk work, inspect and generate each mob independently. Write paired mobs or state variants together only when their existing fields explicitly establish that relationship.

## Writing Rules

- Follow the bundled style prompt over general MUD-writing instincts.
- Write the name as a minimal canonical noun fragment. Use a lowercase article for common mobs and normal capitalization for proper names.
- Write `room_description` as one complete present-tense sentence of roughly 6-15 words, ending in a period. Give the mob one characteristic, repeatable action or posture.
- Write `description` as one paragraph of 1-4 sentences, scaling length to importance. Keep all behavior habitual and repeatable.
- Make missing fields consistent with every populated source field. Do not duplicate the room description as the detailed description's opening sentence.
- Anchor details in the supplied world descriptions without inventing a mob's role in world lore. When instance and base-world descriptions are both present, respect both and prefer the instance description for local atmosphere or scope.
- Express identity, temperament, threat, humor, and pathos through visible evidence. Avoid exposition and unsupported interiority.
- Never introduce game mechanics, stats, levels, meta-language, or one-time events.

## Database Commands

Inspect a mob definition:

```bash
python .codex/skills/wr-mob-describer/scripts/db_mob_context.py context --mob-definition-id 100
```

Apply one or more generated fields using JSON on stdin:

```bash
python .codex/skills/wr-mob-describer/scripts/db_mob_context.py apply --mob-definition-id 100 --input-json -
```

The JSON object may contain only the fields being filled:

```json
{
  "name": "a meticulous locksmith",
  "room_description": "A meticulous locksmith sorts brass keys behind a narrow counter.",
  "description": "Wire spectacles sit low on his nose as he tests each key against a row of tiny locks. His ink-stained fingers move with the patient certainty of someone who trusts metal more readily than conversation."
}
```

Validate the completed definition:

```bash
python .codex/skills/wr-mob-describer/scripts/db_mob_context.py validate --mob-definition-id 100
```

Saving a `MobDefinition` uses the model's normal save path, which synchronizes its display fields to existing spawned mobs.

## Response Shape

For a single mob definition, report the ID and final name, list which fields were filled or preserved, and give the validation result. For a dry run, show only the proposed missing fields and do not edit the database.
