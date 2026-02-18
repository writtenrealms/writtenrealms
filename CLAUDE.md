# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Written Realms is a text-based multiplayer game engine with real-time websocket communication. The system is built with:
- **Django REST Framework** ("Forge") - long-term data persistence, user accounts, world templates
- **FastAPI** - WebSocket server for real-time game communication
- **Vue 3 + TypeScript** ("Herald") - frontend client
- **PostgreSQL** - primary database
- **Redis** - real-time game state and caching
- **Celery + RabbitMQ** - async task processing

## Architecture Transition (WR1 → WR2)

This codebase is transitioning from Written Realms 1.0 to Written Realms 2.0 (WR Core):

**WR1 Architecture (Legacy):**
- Real-time game engine previously lived in a separate WR1 codebase using Tornado + ZeroMQ + Redis
- "Nexus" containers with Pulsar (PUB socket), Beat/Tic schedulers, Game ROUTER hub
- Custom ORM for Redis-based game state
- Being phased out

**WR2 Architecture (Target):**
- Command → Action → Event pipeline
- PostgreSQL as source of truth for canonical game state
- Queued Actions for game logic execution
- Events published to WebSockets
- Aggregate-level row locking for concurrency
- Runtime/cached data in JSONB fields, rebuildable from canonical state
- See `.claude/skills/wr-transition/wr2-architecture.md` for full specification

**Implication:** Legacy WR1 engine code is now out of tree while capabilities continue migrating into `backend/` with async Celery jobs. When working on new features, prefer WR2 patterns.

## Development Setup

### Quick Start (Docker - Recommended)
```bash
# Copy environment variables
cp .env.example .env
# Edit .env and set required secrets (POSTGRES_PASSWORD, DJANGO_SECRET_KEY, JWT_SECRET)

# Start all services
docker compose up -d --build

# Access:
# - Frontend: http://localhost:5173
# - Django API: http://localhost:8000/api/v1/
# - FastAPI: http://localhost:8001
# - Database: localhost:5432
```

### Fast Iteration Workflow (Bind Mounts)
```bash
# Use docker-compose.mount.yml for code bind-mounting
export COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml
docker compose up -d --build

# After code changes, just restart (no rebuild needed)
docker compose restart backend
# or
docker compose restart fastapi

# Unset to return to default
unset COMPOSE_FILE
```

### Alternative: Local Virtualenv
```bash
# Install dependencies
make install

# Run backend service
make dev  # development mode
make run  # normal mode
```

## Common Development Commands

### Docker Operations
```bash
# View logs
docker compose logs -f

# Restart specific service
docker compose restart backend
docker compose restart fastapi
docker compose restart frontend

# Stop all services
docker compose down

# Rebuild after dependency changes
docker compose up -d --build
```

### Database Migrations
```bash
# Create migrations
docker compose exec backend python manage.py makemigrations

# Apply migrations
docker compose exec backend python manage.py migrate

# Create cache table (first time setup)
docker compose exec backend python manage.py createcachetable
```

### Running Tests

**Django/Backend Tests:**
```bash
# ALWAYS use testing settings when running Django tests
docker compose exec backend python manage.py test <test_path> --settings=config.settings.testing

# WR2 tests specifically
docker compose exec backend python manage.py test wr2_tests --settings=config.settings.testing

# Or via Makefile (requires local virtualenv)
make test-wr2-docker
```

**Legacy Advent Tests:**
```bash
# Requires local virtualenv
make test
```

### Frontend Development
```bash
# Frontend runs with hot reload in docker by default

# Or run locally
cd frontend
npm install
npm run dev          # uses .env
npm run dev-local    # points to local backend via .env.dev
npm run build        # production build
```

### Celery Tasks
```bash
# View Celery worker logs
docker compose logs -f celery-worker

# View Celery beat logs
docker compose logs -f celery-beat
```

## Code Architecture

### Backend Structure (`backend/`)
- `builders/` - world building tools, item/mob/room templates, loaders
- `config/` - Django settings, ASGI/WSGI, URL routing
- `core/` - shared utilities, pagination, DB mixins
- `lobby/` - player lobby, matchmaking
- `spawns/` - mob/item spawning logic and celery tasks
- `system/` - site-wide configuration, policies
- `users/` - user accounts, authentication
- `worlds/` - world instances, rooms, world lifecycle management
- `wr2_tests/` - WR2 architecture tests

### Key Django Apps

**worlds**: Core game world management
- `World` model: world instances, lifecycle states, multiplayer config
- `Room` model: individual rooms in worlds
- World lifecycle: `NEW → BUILDING → STARTING → STARTED → STOPPING → STOPPED`
- Multiplayer vs singleplayer worlds

**builders**: World authoring system
- `ItemTemplate`, `MobTemplate`, `RoomTemplate` - game content templates
- `WorldBuilder` - permissions/assignments for world authors
- `Loader`, `Factions`, `Quests`, `Transformations` - game mechanics
- `Path` model: defining room connections and zone organization

**spawns**: Dynamic content generation
- Celery tasks for spawning mobs, items, rooms
- Template-based spawning with randomization

**users**: Authentication & user management
- Custom User model (email-based, passwordless auth supported)
- JWT authentication via SimpleJWT

### FastAPI Structure (`fastapi_app/`)
- `main.py` - app initialization, JWT auth, health endpoint
- `forge_ws.py` - WebSocket handler for Forge/lobby interactions
- `game_ws.py` - WebSocket handler for live gameplay commands
- Endpoints:
  - `/ws/forge/` - authenticated Forge WebSocket
  - `/ws/game/cmd` - game command WebSocket
  - `/health` - health check

### Frontend Structure (`frontend/`)
- Vue 3 with TypeScript
- Vuex for state management
- Vite for build tooling
- Components in `src/components/` (PascalCase.vue)
- Store modules in `src/store/modules/`
- Communicates with Django REST API and FastAPI WebSockets

### Legacy WR1 Engine (Out Of Tree)
- ZeroMQ-based real-time game engine (WR1)
- Custom Redis ORM
- Pulsar, Beat, Tic schedulers
- Maintained separately - avoid new development there

## Testing Conventions

- Django tests: Use `--settings=config.settings.testing` to disable migrations and use in-memory cache
- Test files: `tests.py` per app in `backend/`, plus WR2-focused coverage in `backend/wr2_tests/`
- Frontend: Include screenshots in PRs when touching UI

## Django Settings

Multiple settings files in `backend/config/settings/`:
- `base.py` - shared settings
- `dev.py` - development (DEBUG=True)
- `testing.py` - test environment (migrations disabled, in-memory cache)
- `production.py` - production settings

Set via `DJANGO_SETTINGS_MODULE` environment variable.

## Key Patterns and Conventions

### Code Style
- Python: 4-space indentation, `snake_case` functions/modules
- TypeScript/Vue: `camelCase` for variables/functions, `PascalCase` for components
- Match existing style in surrounding files (no repo-wide formatter)

### Commit Messages
- Short, sentence case, direct description
- Example: "Update Edeus node affinity to use dedicated instance"
- Not: "Updated the node affinity for the Edeus world"

### Database Patterns
- Use `AdventBaseModel` for models with `created_ts` and `modified_ts`
- `**optional` shorthand for `null=True, blank=True`
- Prefer atomic transactions for multi-model operations
- Use `select_for_update()` for row-level locking when needed (WR2 pattern)

### WR2 Development Patterns
When implementing WR2 features:
1. Store canonical data in regular columns/relations
2. Store runtime/cached data in JSONB with `_cache_version` and `_built_at`
3. Use Command → Action → Event pipeline
4. Lock aggregates in consistent order to avoid deadlocks
5. Make actions idempotent with idempotency keys
6. Emit events for WebSocket broadcasts

### Model Relationships
- `World.author` → User (world creator)
- `World.context` → World (for spawned world instances referencing template)
- `WorldBuilder` - many-to-many between users and worlds with permissions
- Templates (ItemTemplate, MobTemplate, RoomTemplate) belong to worlds
- Spawned instances reference their templates

## Environment Variables

Required secrets (set in `.env`):
- `POSTGRES_PASSWORD` - database password
- `DJANGO_SECRET_KEY` - Django signing key
- `JWT_SECRET` - JWT token signing (shared between Django and FastAPI)

Optional:
- `AWS_ACCESS_KEY`, `AWS_SECRET_KEY`, `AWS_BUCKET_NAME` - S3 integration
- `GOOGLE_CLIENT_ID` - Google OAuth
- `SENTRY_DSN` - error tracking
- `MONITORING_EMAIL` - alerts
- `CHANNEL_REDIS_HOST` - Redis host (defaults to 'redis')
- `CELERY_BROKER_URL` - RabbitMQ URL
- `CELERY_RESULT_BACKEND` - Celery results Redis

## Service Ports

- Frontend: 5173
- Django (backend): 8000
- FastAPI: 8001
- PostgreSQL: 5432
- Redis: 6379
- Redis (Celery): 6380
- RabbitMQ: 5672, 15672 (management UI)

## Useful References

- `AGENTS.md` - repository development guidelines
- `ENVIRONMENT_SETUP.md` - detailed setup and troubleshooting
- `.claude/skills/wr-transition/wr1-architecture.md` - legacy architecture
- `.claude/skills/wr-transition/wr2-architecture.md` - target architecture
- `README.md` - quick start guide
- `docs/` - Sphinx documentation sources
