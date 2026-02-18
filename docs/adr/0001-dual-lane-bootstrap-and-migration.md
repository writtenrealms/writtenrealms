# ADR 0001: Dual-Lane Bootstrap and WR1 Migration

- Status: Accepted
- Date: 2026-02-18
- Owners: Written Realms Core maintainers

## Context

Written Realms Core is being open sourced while WR1 is still live.

We need both:

- A clean public onboarding path where a developer can clone, boot, and log in
  without private data.
- A migration path that can ingest WR1 data into WR2 for transition work.

The old approach of committing a database dump in `initdb/` is not acceptable
for OSS because dumps can contain sensitive data and make reproducibility opaque.

## Decision

Adopt a dual-lane strategy:

- Lane A (public OSS bootstrap):
  - Bootstrap from migrations and safe fixtures only.
  - No production-like SQL dumps in Git.
  - `initdb/` is kept only as an optional local mount point.
- Lane B (private WR1 migration):
  - Keep WR1 dump import tooling as a private/operator workflow.
  - Continue supporting dump-to-WR2 migration during transition.
  - Evolve toward a structured world export/import contract (for example YAML)
    that can be produced by WR1 and consumed by WR2.

## Options Considered

1. Commit WR1/WR2 dump files in the public repo.
- Rejected: data sensitivity, noisy history, non-deterministic bootstrap.

2. Immediately flatten/split migrations for OSS and drop dump-based migration.
- Rejected: too disruptive during active transition and increases cutover risk.

3. Dual-lane approach (chosen).
- Accepted: preserves migration velocity while making OSS onboarding reliable.

## Consequences

Positive:

- Public setup can be verified in clean-room tests.
- Private migration work can continue without blocking OSS launch.
- Future WR1 export functionality can be introduced without breaking OSS users.

Negative:

- Two workflows must be documented and maintained.
- Migration compatibility must be tested separately from OSS bootstrap.

## Acceptance Criteria

Lane A (OSS bootstrap) is considered healthy when all are true:

- Fresh clone with empty DB volume completes migrations.
- Backend starts without startup race failures.
- Frontend loads and auth flow can issue a valid token for a new user.

Lane B (private migration) is considered healthy when:

- WR1 data import into WR2 is documented and repeatable for maintainers.
- Post-import migrations to current WR2 schema complete successfully.

## Follow-up

- Maintain a clean-room bootstrap check in CI.
- Define a WR2 world import contract (`WorldBundle v1`) and build import tooling.
- Add WR1 export support when practical, then progressively reduce reliance on
  raw SQL dump migration.
