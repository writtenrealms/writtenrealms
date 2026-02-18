---
name: wr-transition
description: This skill describes the current transition process from the old Written Realms 1.0 codebase to the future Written Realms 2.0 codebase. It should be used when the user is referencing this process to load more information about it.
---

# Written Realms Transition

This repository is transitioning from Written Realms 1.0 (currently live on writtenrealms.com) to Written Realms 2.0 (aka Written Realms Core), which is largely a rewrite and is intended to become open source.

Architecture references in this skill are local files, and paths are relative to this `SKILL.md`:
- `./wr1-architecture.md` (WR1 / legacy architecture)
- `./wr2-architecture.md` (WR2 / target architecture)

The desired new architecture is that, instead of using a home-grown real-time synchronous game engine (the Nexus), logic runs at the Django layer with a message queue (Celery/RabbitMQ style). Instead of strict real-time behavior, the goal is "near real-time": async processing that feels responsive while being far more scalable.

Over time, this transition involves code in the legacy `advent/` folder being phased out, migrated into `backend/`, or removed.
