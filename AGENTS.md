# Repository Guidelines

## Project Structure & Module Organization

- `backend/` holds the Django REST API ("Forge") and its app-specific tests.
- `frontend/` contains the Vue + TypeScript client ("Herald").
- `docs/` has Sphinx documentation sources.
- `docker-compose.yml`, `backend/Dockerfile`, and `entrypoint.sh` define the containerized dev stack.
- `data/` and `logs/` are used for local persistence and runtime output when running via Docker.

## Written Realms Transition Context

- This repo is transitioning from Written Realms 1.0 (live) to Written Realms 2.0 (aka Written Realms Core).
- The WR2 target architecture keeps real-time game logic in `backend/` with async jobs (Celery/RabbitMQ style), aiming for near-real-time behavior.
- Legacy WR1 engine code is now maintained out of tree as migration reference material.
- See `.codex/skills/wr-transition/wr1-architecture.md` and `.codex/skills/wr-transition/wr2-architecture.md` for architecture references.
- For current Trigger/YAML editing behavior, see `docs/yaml-manifest-system.md`.
- For ambient issuer command context direction, see `docs/ambient-command-issuers-plan.md`.

## Build, Test, and Development Commands

- `docker-compose up -d` starts the full stack in the background.
- `docker-compose up -d --build` rebuilds images after code changes.
- `docker-compose logs -f` tails service logs.
- `make dev` starts backend services with bind mounts for fast local iteration.
- `make run` starts the backend service via Docker Compose.
- `npm install` and `npm run dev` start the frontend (see `frontend/README.md`).
- `npm run dev-local` points the frontend at a local backend via `.env.dev`.

## Coding Style & Naming Conventions

- Python uses 4-space indentation and `snake_case` for functions and modules.
- Django apps live under `backend/` and typically use `tests.py` per app.
- WR2 tests live under `backend/wr2_tests/`.
- Vue components are `PascalCase.vue` in `frontend/src/components/`; TypeScript uses `camelCase`.
- No repo-wide formatter is enforced; match the style in the surrounding files.

## Testing Guidelines

- `make test` runs Django tests in the backend container.
- `make test-wr2` runs WR2-focused tests in `backend/wr2_tests`.
- `tox` runs WR2 Django tests (`tox.ini`).
- When running tests for this project, always use Docker and the testing settings, e.g. `docker compose exec backend python manage.py test <test> --settings=config.settings.testing`.
- When touching frontend UI, add or update unit tests if they exist and include a screenshot in the PR.
- WR2 transition override: place all new automated tests under `backend/wr2_tests/` (including builder-facing WR2 Trigger coverage).
- Test placement convention:
  - Builder/editor endpoint and permission tests live with app tests (for example `backend/builders/tests.py`).
  - WR2 runtime/engine behavior tests live in `backend/wr2_tests/`.

## Commit & Pull Request Guidelines

- Commit messages are short, sentence case, and describe the change directly (e.g., "Update Edeus node affinity...").
- PRs should include a summary, testing notes, and any required config changes.
- Link relevant issues and call out migrations or data changes explicitly.

## Configuration & Environment Tips

- Copy `.env.example` to `.env` and set secure secrets before running locally.
- Use `docs/environment-setup.md` for full setup and troubleshooting details.

## Cursor Cloud specific instructions

### Docker-based development

This project runs entirely via Docker Compose. The VM environment snapshot already has Docker installed with `fuse-overlayfs` storage driver and `iptables-legacy` configured for the nested container environment. The update script handles `docker compose build`.

### Starting services

Start all services: `sudo docker compose up -d`. This brings up 9 containers: `backend` (Django on :8000), `fastapi` (:8001), `frontend` (Vue/Vite on :5173), `db` (PostgreSQL :5432), `redis` (:6379), `redis-celery` (:6380), `rabbitmq` (:5672), `celery-worker`, and `celery-beat`. The backend entrypoint automatically runs `migrate` and `createcachetable` on startup.

For fast iteration with bind-mounted source code (no rebuild needed after edits): `COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml sudo docker compose up -d`.

### Running tests

- WR2 tests (primary): `sudo docker compose exec backend python manage.py test wr2_tests --settings=config.settings.testing`
- Full Django tests: `sudo docker compose exec backend python manage.py test --settings=config.settings.testing` (some legacy tests have pre-existing failures related to the WR1→WR2 transition)
- Frontend type check: `sudo docker compose exec frontend npx vue-tsc --noEmit`
- Frontend build: `sudo docker compose exec frontend npm run build`

### Authentication gotcha

The app uses passwordless email-based authentication. In dev, emails are not actually sent. To authenticate via the API, generate a JWT token through the Django shell:
```
sudo docker compose exec backend python manage.py shell -c "
from users.models import User; from rest_framework_simplejwt.tokens import RefreshToken
user = User.objects.get(email='YOUR_EMAIL')
print(str(RefreshToken.for_user(user).access_token))
"
```

### Environment file

The `.env` file is not committed. If missing, copy `.env.example` to `.env` and set `POSTGRES_PASSWORD`, `DJANGO_SECRET_KEY`, and `JWT_SECRET` to any dev-safe values (min 50 chars for keys). Other variables are optional.
