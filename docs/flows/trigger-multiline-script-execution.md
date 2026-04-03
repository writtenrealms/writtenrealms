# WR2 Trigger Multi-line Script Execution

This note explains what happens at runtime when a command trigger with a multi-line `spec.script` is invoked.

## Triggering

When a player/mob types a command that matches `trigger.match` and passes conditions:

1. Trigger gate is checked (`gate_delay`).
2. Gate is consumed.
3. Script runs line-by-line.

If the trigger is gated, no script lines run.

## Line Splitting Rules

- `script` is split by newline first.
- Empty lines are ignored.
- Each non-empty line is split by `&&` into segments.
- Segments in the same line run in order.

Example:

```yaml
script: |
  /cmd room -- /echo -- First && /cmd room -- /echo -- First B
  /cmd room -- /echo -- Second
```

Becomes:

- line 1 segments: `First`, then `First B`
- line 2 segment: `Second`

## Timing Behavior

- Line 1 executes immediately in the same request.
- Line 2+ are queued via Celery with countdown delay.
- Delay per line is:
  - `line_index * GAME_HEARTBEAT_INTERVAL_SECONDS`
  - default setting is `2` seconds.

With default delay:

- line 1 at `t+0s`
- line 2 at `t+2s`
- line 3 at `t+4s`

Setting location:

- `backend/config/game_settings.py`
- `GAME_HEARTBEAT_INTERVAL_SECONDS = 2`

## Execution Context

Each segment is dispatched as a text command with:

- `skip_triggers=True` (prevents trigger recursion)
- `__trigger_source=True`
- `issuer_scope=<trigger.scope>` when present

So scripted commands behave like normal text commands but do not re-enter trigger matching.

## Failure Behavior

- If delayed scheduling fails, that delayed line is executed immediately as a fallback.
- Immediate-path script errors are returned to the invoker as trigger feedback.
- For successfully queued delayed lines, errors are handled when those lines run later (not aggregated into the original synchronous trigger response).

## Code References

- Runtime trigger execution:
  - `backend/spawns/triggers.py`
- Delayed line task:
  - `backend/spawns/tasks.py`
- Delay setting:
  - `backend/config/game_settings.py`
