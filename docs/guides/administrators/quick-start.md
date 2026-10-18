# Quick Start

This installs Core on your own computer using the repository's Docker Compose
stack. You need Git, Docker with Compose v2, and OpenSSL for generating secrets.
Python, Node, PostgreSQL, Redis, and RabbitMQ run inside containers.

Use this stack on a trusted development machine. It publishes database and
broker ports and runs Django and Vite in development mode. For an
internet-facing server, continue with [Production hosting](production-hosting.md).

## Clone and configure

Run these commands in a terminal. If you already cloned the repository, start
from its root directory and skip `git clone`.

```sh
git clone https://github.com/writtenrealms/writtenrealms.git
cd writtenrealms
cp .env.example .env
```

Generate three different values:

```sh
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
```

Edit `.env` and use one value for each of `POSTGRES_PASSWORD`,
`DJANGO_SECRET_KEY`, and `JWT_SECRET`. Keep these settings for the local stack:

```dotenv
POSTGRES_DB=wrealms
POSTGRES_USER=django
DJANGO_SETTINGS_MODULE=backend.config.settings.container
```

Leave optional AWS, Sentry, and Google values blank unless you are configuring
those integrations. Local email is printed to the backend logs, so you do not
need an email provider for this walkthrough. Keep `.env` private and out of Git.

## Start and check the services

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 backend
```

The backend applies database migrations and creates its database cache table
before starting the API. On the first run, image builds, dependency installation,
and migrations take longer than subsequent starts. Once the backend is ready:

```sh
curl --fail http://localhost:8000/health/
curl --fail http://localhost:8001/health
```

Both should return `{"status":"ok"}`. The API probe checks PostgreSQL and the
Django cache. The gateway probe checks only that the gateway HTTP process
responds; neither proves that the worker is processing game commands.

| Service | Purpose / local address |
| --- | --- |
| `frontend` | Site and builder: `http://localhost:5200` |
| `backend` | API: `http://localhost:8000/api/v1/`; Django admin: `http://localhost:8000/admin/` |
| `fastapi` | Forge and game WebSockets on port `8001` |
| `celery-worker` | Background jobs and game processing |
| `celery-beat` | Periodic jobs; run one scheduler |
| `db` | PostgreSQL, stored in the `postgres_data` named volume |
| `redis` | Channel messaging |
| `redis-celery` | Celery task results |
| `rabbitmq` | Task broker |

## First administrator

```sh
docker compose exec backend python manage.py createsuperuser
```

Supply your email, a username, and a password. This creates an active staff
superuser. The password is used by Django admin; the main site's sign-in form
uses email links or a configured Google identity.

On a **fresh installation**, initialize a private site with no main world:

```sh
docker compose exec backend python manage.py shell -c "from system.models import SiteControl; SiteControl.objects.update_or_create(name='prod', defaults={'access_mode': 'invite_only', 'building_enabled': False, 'main_world_id': None})"
```

The record name `prod` is the platform settings key, including in local
development. This command deliberately sets those three fields; do not rerun
it as an upgrade step over an existing site's choices. Without explicit
initialization, admission and building default to open, and the homepage points
at world 1 even if that world does not exist yet.

Open `http://localhost:5200/login`, enter your administrator email, and request
a login link. Read the local email output:

```sh
docker compose logs --tail=100 backend
```

Open the newest `http://localhost:5200/login-link/...` URL in your browser.
Links are single use, expire after 15 minutes, and a new request invalidates
older links. The backend service uses unbuffered output so the message appears
immediately. Do not share logs containing login links.

Open `http://localhost:5200/staff` to check the
[Control Panel](control-panel.md). Staff can sign in while the site is invite
only, even with an empty whitelist.

## First world

1. Select **Build** in the site header, then **Create World**. Choose the world
   type you want; staff can create worlds while open building is disabled.
2. Author a starting area, or create a destination with the default Gold
   currency and [import a complete world export](../builders/yaml-manifests.md#importing-into-a-new-world)
   through **World > Edit**. Family bundles need a multiplayer destination.
3. Check the starting room and player creation settings in
   [World Config](../builders/world-config-builder-guide.md). Publish the world
   when it is ready for other players; a private world remains private even
   when selected as the site's main world.
4. In **Control Panel > Access**, set **Promoted world ID** to the root world's database ID,
   or leave it blank to keep the multi-world homepage. Reload the browser.
5. Enter through the world's lobby and try a game command. For multiplayer
   worlds, check the world's administration controls and worker logs if it
   does not start. Add another player's email to the whitelist before asking
   them to sign in.

Keep this database for your installation; an authored-world export does not
include its accounts or player progress.

## Stop, resume, and troubleshoot

```sh
docker compose stop
docker compose start
```

`docker compose down` removes containers and networks while preserving the
PostgreSQL volume. Start again with `docker compose up -d`.
**`docker compose down --volumes` deletes the database volume.** It is not an
ordinary restart or update command.

| Symptom | Check |
| --- | --- |
| Frontend is not available yet | `docker compose logs --tail=100 frontend`; the first start installs its dependencies. |
| Database authentication fails | Check `.env`; changing `POSTGRES_PASSWORD` does not change the password inside an already initialized volume. |
| Homepage says the world is unavailable | Clear **Promoted world ID** until your root world exists, then set its actual ID. |
| Login email never arrives | Local mail is in `backend` logs; production requires [email configuration](sign-in-and-access.md#production-email). |
| Site opens but game commands stall | Check `fastapi`, `celery-worker`, `rabbitmq`, and `redis`; see [Operations](operations.md). |

To edit backend code without rebuilding each time, use the repository's
`docker-compose.mount.yml` overlay. The
[development setup guide](https://github.com/writtenrealms/writtenrealms/blob/main/docs/dev/environment-setup.md)
documents that workflow. To change baked-in application code with the default
stack, rebuild with `docker compose up -d --build`.
