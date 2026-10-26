# Production Hosting

Start with the [local quick start](quick-start.md), then provision your own
domain, HTTPS endpoint, PostgreSQL database, and application services.
Use `backend.config.settings.production` for an internet-facing installation.

**The public repository does not currently include a turnkey production Compose
file or cloud installer.** Its default Compose file runs a Vite development
server, publishes internal service ports, and does not pass all production
variables to containers. Changing `DJANGO_SETTINGS_MODULE` in `.env` alone is
not a production deployment. This guide specifies what your deployment must
provide; the local quick start is the complete runnable Compose walkthrough.

## Services and persistence

Build the backend image from `backend/Dockerfile`. Use the same source revision
and signing secrets for the API, gateway, worker, and scheduler.

| Process | Command | Working directory |
| --- | --- | --- |
| API | `uvicorn backend.config.asgi:application --host 0.0.0.0 --port 8000` | `/code/backend` |
| Gateway | `uvicorn fastapi_app.main:app --host 0.0.0.0 --port 8001` | `/code` |
| Worker | `celery -A backend.config worker -l info -Q celery` | `/code/backend` |
| Scheduler | `celery -A backend.config beat -l info` | `/code/backend` |

Optional inference uses a separate `inference` queue and worker process; see
[LLM integration](llm-integration.md). Only that worker sets `WR_LLM_WORKER=1`.
Keep provider network requests off game workers even when using the same image.

Set `PYTHONPATH=/code:/code/backend`. Keep **one active scheduler** for an
installation. The gateway needs the backend source, Django settings, and database
access to check current accounts and invitations; a JWT secret alone is insufficient.

Provide PostgreSQL, RabbitMQ, Redis for channel messaging/shared Django caching,
and Redis for task results. Keep database and broker ports private. Persist
PostgreSQL and the broker's data, configure restart policies and dependency
readiness, and keep HTTPS certificate storage persistent if your proxy manages it.
The development Compose file only declares PostgreSQL persistence; it is not a
durable production broker configuration.

Production settings require a PostgreSQL endpoint with TLS and a trusted server
certificate. The stock local `db` container is not configured for this.
Use a managed database or configure PostgreSQL TLS yourself; mount the CA file
read-only into every application container at `POSTGRES_SSLROOTCERT`.

## Runtime configuration

Supply the following to the API, gateway, worker, and scheduler. Values below
are placeholders for your infrastructure, not usable credentials.

```dotenv
DJANGO_SETTINGS_MODULE=backend.config.settings.production
PYTHONPATH=/code:/code/backend
PYTHONUNBUFFERED=1
DJANGO_SECRET_KEY=replace-with-an-independent-secret-at-least-50-characters
JWT_SECRET=replace-with-an-independent-secret-at-least-50-characters
ALLOWED_HOSTS=realms.example.com
WR_SITE_BASE=https://realms.example.com
WR_HOST=realms.example.com

POSTGRES_HOST=database.example.com
POSTGRES_PORT=5432
POSTGRES_DB=wrealms
POSTGRES_USER=core
POSTGRES_PASSWORD=replace-with-your-database-password
POSTGRES_SSLROOTCERT=/run/secrets/postgres-ca.crt
POSTGRES_CONN_MAX_AGE=0

CELERY_BROKER_URL=amqp://core:replace-with-a-url-encoded-password@rabbitmq:5672/core
CELERY_RESULT_BACKEND=redis://redis-celery:6379/0
DJANGO_CACHE_URL=redis://redis:6379/1
CHANNEL_REDIS_HOST=redis
```

Create the broker user and vhost named in your URL and grant the user access.
Use your actual service addresses; the names above assume a private container
network. Protect secrets with your deployment's secret storage and restrict
access to generated environment files. Do not bake secrets into images or
frontend bundles.

`WR_HOST` is a hostname without a scheme or path. The worker uses it when
returning the gameplay WebSocket URL to a player. Leaving it unset returns a
localhost URL even when the website and Forge WebSocket use your public domain.

Set up [production email and optional Google sign-in](sign-in-and-access.md)
as part of the same deployment. The production mail variables use the
`WR_AWS_*` names.

If you use Compose, explicitly pass variables with `environment` or `env_file`
in **each relevant service**. A project `.env` supplies interpolation values;
it does not automatically inject every variable into every container. See
[Docker's environment documentation](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/).

Keep `POSTGRES_CONN_MAX_AGE=0` for the ASGI API and gateway. A **prefork worker**
may set it to `60` in its own service environment to reduce repeated database
TLS/authentication work. Budget connections for each worker process and API
load; do not put that override in a shared environment file.

## Build the browser application

Build the frontend with Node 20 or later and your public URLs. For example,
from the repository root:

```sh
npm --prefix frontend ci
VITE_API_BASE=https://realms.example.com/api/v1/ \
VITE_FORGE_WS_URI=wss://realms.example.com/ws/forge/ \
npm --prefix frontend run build
```

If using Google sign-in, also supply `VITE_GOOGLE_CLIENT_ID` at build time.
Deploy `frontend/dist/` as static files. Updating runtime container variables
does not change an already-built frontend. The provided frontend Dockerfile
does not declare deployment-specific `VITE_*` build arguments; set up the
build environment explicitly rather than assuming `--build-arg` is consumed.

Optionally run `make docs-build` and serve `docs/.vitepress/dist/` at `/docs/`.
That build includes redirects and canonical links to the official Core manual.
If you want your own canonical documentation site, also adapt
`docs/scripts/redirects.mjs` and the docs theme's external links to your domain.

## Public routing

Terminate HTTPS at a reverse proxy and use these routes before the frontend's
single-page-app fallback:

| Public path | Destination |
| --- | --- |
| `/api/v1/*` | Django API on port `8000`, preserving the path |
| `/admin` and `/admin/*` | Django on port `8000`; `/admin/` is canonical |
| `/health/` | Django readiness on port `8000` |
| `/ws/forge/` | Gateway on port `8001`, with WebSocket upgrades |
| `/ws/game/cmd` | Gateway on port `8001`, with WebSocket upgrades |
| `/static/*` | Collected Django static files from `/code/backend/static/` |
| `/docs` and `/docs/*`, if hosted | Redirect `/docs` to `/docs/`; serve the built docs, including clean `.html` URLs |
| Other site paths | Frontend assets, falling back to `index.html` for client routes such as `/staff` |

Use the application image's collected static directory or collect those assets
as a release step. The production API does not serve static files itself.
Serve a real 404 for missing docs instead of the frontend application.

The proxy must set the original host and `X-Forwarded-Proto: https` correctly.
Do not expose the API directly to clients that can forge those trusted proxy
headers. Validate HTTPS redirects, CSRF-protected Django admin forms, and both
WebSocket paths. A successful homepage request only validates static delivery.

## First deployment

These are the application commands to put into your own deployment workflow,
using the production image, environment, and private network described above:

```sh
# Run once as a release job before accepting traffic.
python manage.py migrate --noinput
python manage.py check --deploy
python manage.py createsuperuser
```

Run them in `/code/backend`. Production uses Redis for Django caching, so it
does not need the development database cache table. Apply migrations once per
release before starting processes on the new schema.

Initialize `SiteControl` with your intended admission/building policy and no
main world, using the [first-administrator setup](quick-start.md#first-administrator).
Keep public traffic restricted until an administrator can sign in. Start the
API, gateway, worker, and the single scheduler, then create or import a world.

Before inviting players, verify:

- HTTPS and `/health/`, then an actual administrator login and `/staff`.
- Both Forge and gameplay WebSockets, including a completed game command.
- Invited-account admission and uninvited-account rejection if invite only.
- The intended world can start, a player can enter, and periodic work progresses.
- A database backup and a restore drill into an isolated installation.

Review [Django's deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)
and the [operations guide](operations.md) for upgrades and recovery. The current
production settings intentionally limit HSTS to the configured hostname; they
silence Django's subdomain/preload warnings without disabling HTTPS enforcement.

## Capacity

Size from measured gameplay latency, queue age, worker saturation, memory,
database connections, and lock contention. There is no hardware-to-player-count
guarantee in this guide. More workers can increase database contention; start
with bounded concurrency, measure representative worlds, and increase capacity
only when those measurements justify it. Keep static frontend/docs requests on
the proxy so they do not consume API or worker capacity.
