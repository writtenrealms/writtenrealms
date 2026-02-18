# Written Realms

A text-based multiplayer game engine with real-time websocket communication, built with Django/Python backend and Vue.js frontend.

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Git

### Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd writtenrealms
   ```

2. **Configure environment variables**
   ```bash
   cp .env.example .env
   ```

   Edit `.env` and set your credentials (at minimum, set passwords and secret keys):
   ```bash
   # Required: Generate secure values for these
   POSTGRES_PASSWORD=your_secure_password_here
   DJANGO_SECRET_KEY=your_django_secret_key_here
   JWT_SECRET=your_jwt_secret_here
   ```

3. **Start the services**
   ```bash
   docker compose up -d --build
   ```

4. **Access the application**
   - Frontend: http://localhost:5173
   - API: http://localhost:8000/api/v1/
   - Database: localhost:5432

### Generating Secure Keys

```bash
# Django SECRET_KEY
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'

# JWT_SECRET and passwords
openssl rand -base64 64
```

### Useful Commands

```bash
# View logs
docker compose logs -f

# Stop services
docker compose down

# Rebuild after code changes
docker compose up -d --build
```

### Development: bind-mount code (fast iteration)

By default the `backend` container bakes the code into the image, so Python changes require a rebuild.

To switch to a bind-mount workflow (edit code on your host, then just `docker compose restart`):

```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.mount.yml
docker compose up -d --build

# after code changes
docker compose restart backend
```

To go back to the default workflow: `unset COMPOSE_FILE` (then `docker compose up -d --build`).

## Documentation

For detailed setup instructions, environment variables reference, and troubleshooting, see:

- [Environment Setup](docs/environment-setup.md)
- [WR2 Player Command Flow](docs/player-command-flow.md)
- [WR2 Trigger YAML Manifest System](docs/yaml-manifest-system.md)
- [Ambient Command Issuers Plan](docs/ambient-command-issuers-plan.md)
- [Architecture Decision Records](docs/adr/)

## Project Structure

```
.
├── backend/          # Django REST API
├── frontend/         # Vue.js frontend application
└── docker-compose.yml
```

## Development

Frontend runs with hot reload by default. Backend changes are picked up via `docker compose restart backend` when using `docker-compose.mount.yml`.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
