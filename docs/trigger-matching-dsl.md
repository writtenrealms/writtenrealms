# Trigger Matching DSL

## Goal

Use one compact expression language for authored "what to look for" matching
across trigger fields.

## Operators

- `or` or `|`: logical OR
- `and` or `+`: logical AND
- `not` or `!`: logical NOT
- `(...)`: grouping / precedence override

Default precedence:

1. `not`
2. `and`
3. `or`

Yes, parentheses are supported and encouraged for clarity in non-trivial rules.

## Literals

Literals are plain text fragments.

- Unquoted: `touch altar`
- Quoted (recommended for explicit phrases): `"touch altar"`

## Examples

- `touch altar or touch stone`
- `touch altar and (pray or kneel)`
- `hello and (traveler or friend) and not enemy`

## Matching Semantics By Trigger Context

The DSL syntax is shared. Literal matching behavior depends on field context:

- `Trigger.match` on command triggers: phrase match against command text.
- `Trigger.match` on mob `say` event triggers: phrase match against spoken text.
- `Trigger.match` on mob `receive` / `periodic` event triggers: exact match.
- Events without a match payload (for example `enter`) ignore `Trigger.match`.

This gives a single authored language while preserving event-specific value
matching where exact comparisons are needed.

## Validation

Matcher expressions are validated on ingestion:

- Trigger manifest `spec.match`
- Mob trigger compatibility endpoints (`match`)

Invalid expressions are rejected with parse errors (for example, unmatched
parentheses).

## Authoring Guidance

- Keep expressions short and readable.
- Use parentheses to show intent when combining `and` + `or`.
- Prefer explicit phrases for multi-word terms.
- Split complex behavior across multiple triggers instead of a single very long rule.
