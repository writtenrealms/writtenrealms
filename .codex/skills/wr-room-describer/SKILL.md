---
name: wr-room-describer
description: Draft or update Written Realms room descriptions from database room IDs, builder notes, exits, and nearby room context. Use when the user wants Codex to write, revise, populate, or review text-MUD room descriptions in the Edeus/Written Realms style using WR room IDs, Django database rooms, local <room_id>.txt exports, notes, keywords, or exported room data.
---

# WR Room Describer

Use this skill to write flagship Written Realms room descriptions as an authoring assistant. This is a prototype workflow for Codex-driven content work, not a WR2 platform integration.

## Resources

- `references/room-description-style-prompt.md`: the primary style contract. Read this before drafting or revising any description.
- `references/edeus-room-descriptions.txt`: 120 Edeus room descriptions. Use as a style corpus and continuity reference only; do not copy room names, proper nouns, distinctive landmarks, or long phrases into a new world unless the user explicitly asks for Edeus content.
- `scripts/db_room_context.py`: primary helper for reading and updating `worlds.Room` rows through the Dockerized Django backend.
- `scripts/room_context.py`: fallback helper for parsing `<room_id>.txt` export files and collecting depth-based neighbor context.

## Workflow

1. Read `references/room-description-style-prompt.md`.
2. Read or sample `references/edeus-room-descriptions.txt` for style calibration when drafting more than a minor edit.
3. Treat bare room IDs as `worlds.Room.id` database IDs unless the user explicitly says they are file IDs.
4. Use `scripts/db_room_context.py context --room-id <id> --depth 3` to gather the target room, connected rooms, and coordinate-adjacent rooms from the Django database.
   - If Docker requires sudo, run with `WR_ROOM_DESCRIBER_DOCKER_CMD="sudo docker compose"`.
   - If the user provides a world ID, pass `--world-id <id>` as a guard against editing a room in the wrong world.
   - The helper reads `Room.name`, `Room.note`, `Room.description`, `Room.zone`, coordinates, and `north/east/south/west/up/down`.
   - Connected rooms are followed to the requested depth. Direct coordinate neighbors of the target room are also included even if there is no exit to them.
5. Treat `Room.note` as builder notes/keywords. Treat `Room.description` as an existing description to preserve unless the user asks to revise or overwrite it.
6. If the target room title is exactly `Untitled Room`, infer the best suitable title from the room's note, exits, coordinates, description draft, and neighboring rooms. Use the same short map-label style as normal room titles, then apply it with `scripts/db_room_context.py rename --room-id <id> --name '<title>'`. Do not rename non-placeholder rooms unless the user explicitly asks.
7. Draft one room at a time unless the user asks for bulk generation. For bulk work, process rooms in graph order so newly written adjacent descriptions and inferred titles can inform later rooms.
8. When applying a generated description to the database, update only `Room.description`, except for the `Untitled Room` title replacement described above. Do not modify `Room.note`, zone, coordinates, exits, doors, triggers, or neighboring rooms.
9. Re-run `scripts/db_room_context.py validate --room-id <id>` after edits and fix any format/style warnings that matter.

## Writing Rules

- Follow the bundled style prompt over any general MUD-writing instincts.
- Output a single paragraph body whose sentence count matches the room's importance:
  - 2 sentences for boring connector rooms, roads, paths, stairs, and transitional spaces whose main purpose is movement.
  - 3 sentences for normal rooms; this is the default for non-road rooms.
  - 4 sentences only for landmark, hub, dramatic, or mechanically/story-important rooms that warrant extra attention.
- Use present tense and an impersonal camera.
- Open with the dominant physical anchor of the room.
- Weave navigation into prose selectively. Usually describe the most important direction prominently, mention a second direction briefly if useful, and mention more than two directions only for simple intersection rooms whose main purpose is routing.
- Keep navigation origin-neutral. Descriptions must work no matter which exit the player used to arrive and must not depend on the order rooms were authored. Avoid path-assumptive phrasing such as "back toward", "returns to", "continues from", "ahead", "behind", or "came from"; prefer objective destination phrasing such as "the road leads south toward the Agora" or "the eastern archway opens to the hall."
- Maintain continuity with adjacent rooms, especially recurring materials, light sources, landmarks, paths, water flow, stairs, sound, scent, and air.
- Use non-accessible coordinate-adjacent rooms as optional scenery, boundaries, or blocked-direction justification when they are significantly relevant. Do not imply the player can travel that way unless an exit exists.
- Keep adjacent rooms from reading like copies of each other. Before applying a draft, compare it with directly connected room descriptions and vary the opening image, first noun phrase, sentence structure, and navigation phrasing while preserving shared materials and landmarks.
- Write descriptions that work at all times of day. Avoid direct claims that depend on sun position, moonlight, shadows, reflections, or weather unless phrased conditionally and used sparingly.
- Use builder notes as constraints, not prose to preserve verbatim.
- If context is thin, infer conservatively from the room name, exits, nearby names, and region palette.
- Avoid second person, named NPCs, game mechanics, lore exposition, events in progress, and copied language from Edeus examples.

## Editing Database Rooms

Database rooms use the existing `worlds.Room` model:

- `name`: room title
- `note`: builder-facing notes/keywords
- `description`: generated/player-facing room body
- `north`, `east`, `south`, `west`, `up`, `down`: exit room links
- direct coordinate neighbors: rooms at adjacent `x/y/z` positions that may be visible or relevant even without an exit

To inspect context:

```bash
python .codex/skills/wr-room-describer/scripts/db_room_context.py context --room-id 100 --depth 3
```

To apply a description:

```bash
printf '%s' '<description>' | python .codex/skills/wr-room-describer/scripts/db_room_context.py apply --room-id 100 --description-stdin
```

To replace a placeholder title:

```bash
python .codex/skills/wr-room-describer/scripts/db_room_context.py rename --room-id 100 --name '<new title>'
```

Use `--allow-overwrite` only when the user asked to revise or replace an existing description. The apply command calls `room.update_live_instances()` after saving, matching the current builder update path even though it is presently a no-op.

## Editing Room Files

Room files are expected to follow this shape:

```text
Room Name
Middle block containing notes, keywords, an existing description, or a generated description.
N: 123
E: 456
```

Only contiguous trailing direction lines are exits. Everything between the title and exit block is the room body/notes.

When applying a generated description:

```text
<original title>
<new one-paragraph description>
<original exit lines>
```

If the title line is exactly `Untitled Room`, replace it with the best suitable title in the same edit. Use `apply_patch` for file edits. Do not overwrite unrelated rooms, reorder exits, or delete notes from neighboring room files.

## Response Shape

For a single-room request, report:

- room ID and title updated
- one-sentence note about the continuity choices used
- validation result

For a dry-run request, provide the proposed description without editing the file.
