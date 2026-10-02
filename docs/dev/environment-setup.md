# Environment Setup Guide

This repository uses environment variables to manage sensitive configuration data. This guide explains how to set up your local development environment.

## Quick Start

1. **Copy the example environment file:**
   ```bash
   cp .env.example .env
   ```

2. **Use Python 3.10 locally** to match the Docker runtime.

3. **Edit `.env`** with your actual credentials (this file is gitignored and won't be committed)

4. **For frontend development:**
   ```bash
   cd frontend
   # For npm run dev-local (and the docker-compose frontend service):
   # .env.localBackend is committed defaults for local backend URLs.
   # Use .env.localBackend.local for your machine-specific overrides.
   cat > .env.localBackend.local <<'EOF'
   VITE_GOOGLE_CLIENT_ID=your_google_oauth_client_id_here
   EOF
   ```
   `VITE_GOOGLE_CLIENT_ID` is optional. If not set, Google login is hidden and email login/signup still works.

5. **Run with docker-compose:**
   ```bash
   docker compose up --build
   ```
   Docker Compose will automatically load variables from `.env`

## Optional: bind-mount backend code for fast iteration

The default `docker-compose.yml` builds the `backend` image with the repo contents baked in (good default for an open source workflow).

If you want to edit Python code on your host and only do `docker compose restart` (no rebuild), use the mount overlay:

```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml
docker compose up -d --build
```

After code changes:

```bash
# Django API changes
docker compose restart backend

# If your changes affect Celery / FastAPI too
docker compose restart celery-worker celery-beat fastapi
```

Convenience targets:

```bash
make docker-up-mount
make docker-restart-mount
make reset-dev-db-mount
```

If you prefer not to export `COMPOSE_FILE`, pass both files each time:

```bash
docker compose -f docker-compose.yml -f docker-compose.mount.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.mount.yml restart backend
```

To revert to the default workflow:

```bash
unset COMPOSE_FILE
docker compose up -d --build
```

## Reset the local database

For a clean local onboarding pass, reset the Docker-backed Postgres data and let the app rebuild from migrations:

```bash
make reset-dev-db
```

If you are using the bind-mount workflow instead of the default compose setup:

```bash
make reset-dev-db-mount
```

The underlying script is `scripts/reset-dev-db`. It stops the stack, deletes `./data/db`, recreates it, and starts the stack again. Add `--build` if you also want to rebuild images as part of the reset.

## Required Environment Variables

### Database Configuration
- `POSTGRES_DB` - Database name (default: wrealms)
- `POSTGRES_USER` - Database user (default: django)
- `POSTGRES_PASSWORD` - Database password (**required**)

### Django Configuration
- `DJANGO_SECRET_KEY` - Django secret key for sessions/CSRF (**required**)
- `DJANGO_SETTINGS_MODULE` - Settings module to use (default: backend.config.settings.container)

### JWT Configuration
- `JWT_SECRET` - Secret key for JWT token signing (**required**)

### AWS Configuration (for backups)
- `AWS_ACCESS_KEY` - AWS access key
- `AWS_SECRET_KEY` - AWS secret key
- `AWS_BUCKET_NAME` - S3 bucket name for backups

### Optional Services
- `SENTRY_DSN` - Sentry error tracking DSN (optional)
- `GOOGLE_CLIENT_ID` - Google OAuth client ID (optional)

### Email Configuration
- `MONITORING_EMAIL` - Email for system monitoring alerts
- `SYSTEM_EMAIL_FROM` - From address for system emails

### Frontend Configuration (Vite)
- `VITE_API_BASE` - Backend API base URL
- `VITE_FORGE_WS_URI` - WebSocket URI
- `VITE_GOOGLE_CLIENT_ID` - Google OAuth client ID for frontend (optional)

## File Structure

```
.
├── .env.example          # Template with placeholder values (committed)
├── .env                  # Your actual credentials (gitignored, for local dev)
├── frontend/
│   ├── .env.localBackend        # Frontend defaults for dev-local mode (committed)
│   ├── .env.localBackend.local  # Frontend local overrides for dev-local mode (gitignored)
│   ├── .env                     # Frontend defaults for default Vite mode (optional, if present)
│   └── .env.local               # Frontend local overrides for default Vite mode (gitignored)
└── docker-compose.yml    # Configured to use .env
```

## Production Deployment

Servers may enable email admission restrictions with `WR_ACCESS_RESTRICTED=1`
and a comma-separated `WR_ALLOWED_EMAILS`. Addresses are matched without case
sensitivity. An enabled, empty list denies all accounts; the default is public
registration. Pass both variables to Django, Celery, and the FastAPI gateway.
Recreate those services after editing the list: this reloads the policy and
disconnects existing WebSocket sessions. HTTP access is checked against the
current account email without an additional database query. Gateways check the
signed email claim when a connection authenticates. Older access tokens without
that claim require a fresh login or token refresh when restrictions are enabled.
Guest play is disabled on restricted servers. Keep the actual list and
provider-specific operations instructions outside the public repository.

For production:

Use `DJANGO_SETTINGS_MODULE=backend.config.settings.production` with an HTTPS
reverse proxy. In addition to the variables below, provide `ALLOWED_HOSTS`,
`WR_SITE_BASE`, `POSTGRES_HOST`, `POSTGRES_PORT`, and `POSTGRES_SSLROOTCERT`.
PostgreSQL connections verify the server certificate against that CA file.
`POSTGRES_CONN_MAX_AGE` defaults to `0`, keeping persistent connections disabled
for the ASGI web service. Set it to `60` in the **prefork Celery worker's service
environment only** to reuse each process's database connection for up to 60
seconds instead of repeating TLS/authentication for every task. Celery's Django
integration checks connection age at task boundaries, and Django health checks
test reused connections before the first query of each task. Idle connections
are retired at the next task boundary, rather than by a separate timer. Set the
value to `0` to disable reuse; only nonnegative integer seconds are accepted.
Allow one connection per worker process when sizing database connections. Do
not set a positive value in a shared environment used by the ASGI web service;
see [Django's persistent connection guidance](https://docs.djangoproject.com/en/5.2/ref/databases/#persistent-connections).
Provide a credentialed `CELERY_BROKER_URL` and shared `DJANGO_CACHE_URL` to all
Django processes. Configure `CHANNEL_REDIS_HOST` for the gateway and workers.
Enable transactional email with `WR_SEND_EMAIL=1`, `WR_AWS_ACCESS_KEY`,
`WR_AWS_SECRET_KEY`, and `SYSTEM_EMAIL_FROM`. Both signing secrets must be at
least 50 characters. Build the Vue application with deployment-specific
`VITE_API_BASE` and `VITE_FORGE_WS_URI`; run the compiled output behind the
proxy. Keep credentials, private infrastructure state, and backups outside Git.

1. **Create a `.env` file** (or use your hosting provider's environment variable system)
2. **Set all required variables** with production values
3. **Never commit** production credentials to version control
4. **Rotate credentials** if they were ever committed in git history

## Generating Secrets

### Django SECRET_KEY
```python
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

### JWT_SECRET
```bash
openssl rand -base64 64
```

### Database Password
```bash
openssl rand -base64 32
```

## Security Notes

- ✅ `.env` and `.env.local` files are in `.gitignore`
- ✅ `.env.example` contains only placeholders
- ✅ All hardcoded secrets have been removed from source code
- ⚠️ **Before open sourcing**: Create a fresh repository without git history
- ⚠️ **Production**: Rotate all credentials before deploying

## Troubleshooting

### "Environment variable not set" errors
Make sure `.env` exists and contains all required variables.

### Docker Compose not loading variables
Ensure `.env` is in the same directory as `docker-compose.yml`. Docker Compose automatically loads `.env` files.

### Frontend can't connect to backend
For `npm run dev-local`, check `VITE_API_BASE` and `VITE_FORGE_WS_URI` in `frontend/.env.localBackend` (or overrides in `frontend/.env.localBackend.local`).

### Google login button missing
Set `VITE_GOOGLE_CLIENT_ID` in `frontend/.env.localBackend.local` (for `dev-local`) or `frontend/.env.local` (for default `npm run dev` mode).
