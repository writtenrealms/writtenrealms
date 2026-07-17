---
name: wr-transition
description: This skill describes the current transition process from the old Written Realms 1.0 codebase to the future Written Realms 2.0 codebase. It should be used when the user is referencing this process to load more information about it.
---

# Written Realms Transition

This repository is transitioning from Written Realms 1.0 (currently live on writtenrealms.com) to Written Realms 2.0 (aka Written Realms Core), which is largely a rewrite and is intended to become open source.

WR2 will launch with a clean, empty database. There is no in-place WR1-to-WR2 data migration: do not propose or implement production backfills, dual writes, compatibility storage, or migration of accounts, players, balances, inventories, quest progress, or other runtime state. The only WR1 content bridge is an optional utility that converts authored WR1 worlds into canonical WR2 manifests for builders who choose to import them into fresh WR2 worlds.

Architecture references in this skill are local files, and paths are relative to this `SKILL.md`:
- `./wr1-architecture.md` (WR1 / legacy architecture)
- `./wr2-architecture.md` (WR2 / target architecture)

Implementation reference docs in the repository:
- `../../../docs/architecture/yaml-manifest-system.md` (current manifest authoring/editing flow, including WR1 manifest-conversion notes)
- `../../../docs/architecture/ambient-command-issuers-plan.md` (ambient issuer command model and phased plan)

The desired new architecture is that, instead of using a home-grown real-time synchronous game engine (the Nexus), logic runs at the Django layer with a message queue (Celery/RabbitMQ style). Instead of strict real-time behavior, the goal is "near real-time": async processing that feels responsive while being far more scalable.

Over time, this transition involves functionality from the legacy `advent/` code being reimplemented in `backend/`, replaced, or removed. That is code/architecture work, not migration of live WR1 data.
When removing or replacing legacy WR1 data structures, update the WR1 manifest-conversion notes in `docs/architecture/yaml-manifest-system.md` in the same change so the optional WR1 export utility can be kept aligned with the current WR2 manifest contracts.
