# WR2 YAML Manifest Editing

## Goals

WR2 world editing is moving toward an authored-manifest workflow inspired by Kubernetes:

- builder UI pages show current state
- canonical edit format is YAML
- import/export is straightforward because authored entities can round-trip through manifests

The first implemented entity is `Trigger`.

## Current Trigger Flow

### 1. Room Triggers Screen

In room navigation, **Triggers** now replaces **Actions**.

- It lists room-scoped triggers for the selected room.
- It includes a room-tailored **New Room Trigger Template** YAML block.
- Each trigger displays its YAML definition.
- Each trigger includes **Copy YAML** and **Copy Delete YAML** actions.
- Recommended workflow: copy template YAML, tweak it, ingest in **Edit World**.

### 2. World Edit Screen

A new world-level **Edit World** view accepts a YAML manifest textarea.

- Submitting a manifest currently supports `kind: trigger`.
  - `kind` is case-insensitive (`trigger`, `Trigger`, `TRIGGER` all work).
- Trigger manifests now support both:
  - **create** (no `metadata.id` / `metadata.key`)
  - **update** (include `metadata.id` or `metadata.key`)
  - **delete** (`operation: delete` with `metadata.id` or `metadata.key`)

## Trigger Manifest Shapes

### Create Trigger

```yaml
kind: trigger
metadata:
  world: world.1
  name: Pull Lever Trigger
spec:
  scope: room
  kind: command
  target:
    type: room
    key: room.10
  actions: pull lever or pull chain
  script: /cmd room -- /echo -- The lever clicks.
  conditions: level 1
  show_details_on_failure: true
  failure_message: Not yet.
  display_action_in_room: true
  gate_delay: 5
  order: 7
  is_active: true
```

### Update Trigger

```yaml
kind: trigger
metadata:
  world: world.1
  id: 42
  key: trigger.42
  name: Pull Lever Trigger
spec:
  scope: room
  kind: command
  target:
    type: room
    key: room.10
  actions: pull lever or pull chain
  script: /cmd room -- /echo -- The lever clicks.
```

### Multi-line `script`

`spec.script` accepts YAML block strings (multi-line).

Runtime behavior details are documented in:

- `docs/trigger-multiline-script-execution.md`

Execution behavior:

- first script line runs immediately
- each following line runs after a fixed delay from the previous line
- default delay is `2` seconds, configured via
  `backend/config/game_settings.py` (`GAME_HEARTBEAT_INTERVAL_SECONDS`)

```yaml
kind: trigger
metadata:
  world: world.1
  id: 42
spec:
  script: |
    /cmd room -- /echo -- The lever clicks.
    /cmd room -- /echo -- Dust falls from the ceiling.
    /cmd room -- /echo -- A hidden door slides open.
```

### Delete Trigger

```yaml
kind: trigger
operation: delete
metadata:
  world: world.1
  id: 42
```

## `apiVersion`

- `apiVersion` is optional for trigger manifests.
- If provided, accepted values are:
  - `v1alpha1`
  - `writtenrealms.com/v1alpha1` (legacy-compatible)
- For approachability, the exported YAML omits `apiVersion` by default.

## `metadata.id` vs `metadata.key`

### What they are

- `metadata.id`: numeric DB identifier (`42`)
- `metadata.key`: typed string key (`trigger.42`)

### How they are used today

- Both are accepted as trigger identity for updates.
- If both are present, they must refer to the same trigger.
- If neither is present, ingestion creates a new trigger.

### Is `key` WR1 cruft?

No, but its role should be narrow and explicit in WR2:

- `key` is still useful as a typed reference format across entities (`room.10`, `zone.3`, `trigger.42`) and is already widely used in builder/game payloads.
- `id` is simpler for update targeting.
- For WR2 manifests, treat `id` as the primary update identifier and `key` as an interoperability/reference-friendly alias.

Long term, if we want portable manifests across worlds/environments, neither raw `id` nor `trigger.<id>` is ideal alone; we should add stable authored identifiers (for example `metadata.slug` or `metadata.uid`) and map those at import time.

## Is `kind: trigger` redundant with `key: trigger.42`?

Partially, yes. They validate different things:

- `kind` selects the manifest parser/contract and is case-insensitive.
- `key` (or `id`) identifies one concrete instance.

Keeping both is still useful because:

- `kind` allows generic ingestion dispatch before touching IDs.
- `key` keeps typed references consistent with other entity refs.

If we eventually move to `metadata.id` only for updates, `kind` remains required.

## Validation Rules (Current)

- `kind` must resolve to `trigger` (case-insensitive).
- For update: `metadata.id` or `metadata.key` must reference an existing trigger in the selected world.
- For create: omit both `metadata.id` and `metadata.key`.
- For delete: set `operation: delete` and include `metadata.id` or `metadata.key`.
- `metadata.world` (if present) must match the selected world.
  - `metadata.world` accepts either integer id (`1`) or key form (`world.1`).
- `spec.scope`, `spec.kind`, booleans, and integers are validated.
- For create:
  - `spec.scope` is required.
  - `spec.target` is required for room/zone scope.
- `spec.target` must match scope type (`room`, `zone`, `world`) and exist in world.
- `conditions` are validated through the WR2 conditions parser in `backend/core/conditions.py`.

Permission checks are applied when editing via manifest:

- rank 3+ builders can edit all trigger scopes
- rank 1-2 builders can edit room/zone targets only when assigned
- rank 1-2 builders cannot edit world-scoped triggers

## Implementation Notes

- Manifest helpers live in `backend/builders/manifests.py`.
- Trigger list + YAML serialization endpoint:
  - `GET /api/v1/builder/worlds/<world_pk>/rooms/<room_pk>/triggers/`
- Manifest apply endpoint:
  - `POST /api/v1/builder/worlds/<world_pk>/manifests/apply/`
  - returns `operation: created`, `operation: updated`, or `operation: deleted`

## How To Add A New Trigger (Builder Workflow)

1. Open room **Triggers** view.
2. Copy YAML from an existing trigger if you want a template.
3. In **Edit World**, paste YAML and remove `metadata.id`/`metadata.key`.
4. Update `metadata.name`, `spec.target`, `spec.actions`, `spec.script`, etc.
5. Submit manifest.
6. Verify response indicates `operation: created`.
7. Refresh room Triggers view and confirm new trigger appears.

## How To Edit An Existing Trigger

1. Open room **Triggers** view.
2. Copy YAML for the trigger.
3. Keep `metadata.id` (and optionally `metadata.key`) intact.
4. Modify only the fields you want to change in `spec` (partial updates are supported).
5. Submit manifest.
6. Verify response indicates `operation: updated`.

### Minimal Patch Example

```yaml
kind: trigger
metadata:
  world: 1
  id: 42
spec:
  actions: new action
```

## How To Delete A Trigger

1. Open room **Triggers** view.
2. Use **Copy Delete YAML** on the trigger.
3. In **Edit World**, paste the delete manifest.
4. Submit manifest.
5. Verify response indicates `operation: deleted`.

## Guidelines For Extending To Other Entities

When adding YAML support for another entity (ItemTemplate, MobTemplate, Quest, etc.):

1. Add serializer/parser/apply helpers in `backend/builders/manifests.py` (or a sibling module per domain if it grows large).
2. Support both create and update semantics up front:
   - create when identity fields are omitted
   - update when identity fields are present
3. Keep one stable manifest contract per `kind` with:
   - `apiVersion`
   - `kind`
   - `metadata`
   - `spec`
4. Make UI pages read-oriented first (state visibility), then use World Edit for writes.
5. Enforce strict world/target validation to avoid cross-world edits.
6. Keep permission checks at apply time, based on entity scope.
7. Add round-trip tests:
   - list/export includes YAML
   - apply can create
   - apply updates expected entity
   - permission gate behavior
8. Prefer additive evolution (`apiVersion` bumps, new optional fields) over breaking format changes.
