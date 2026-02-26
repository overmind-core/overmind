<img width="4484" height="764" alt="image" src="https://github.com/user-attachments/assets/af059b21-7199-4f0f-8610-63adabbaebc0" />

# Overmind Core

Overmind is a tool that enables AI to learn from production data, turning real-world agent behaviour into continuous improvement. All from a single Docker Compose stack!

<img width="3022" height="1724" alt="image" src="https://github.com/user-attachments/assets/000aa5f2-df9f-4cb6-88e8-3a0fdf6777a5" />

## Quick Start

**Prerequisites:** Docker and Docker Compose.

```bash
# 1. Configure your LLM key(s)
cp .env.example .env
#    Edit .env and add at least one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY

# 2. Start everything
make run
```

That's it. On first startup the system will:

- Install dependencies and start the API, frontend, and background workers
- Run database migrations automatically
- Create a default admin user (`admin` / `admin`)
- Create a default project and API token (printed in the logs)
- **Auto-open your browser** once all services are healthy

The frontend is served by Vite at **http://localhost:5173** with hot-reloading. The API runs at **http://localhost:8000**. Check health at http://localhost:8000/health.

## Services

| Service           | Port | Description                                             |
| ----------------- | ---- | ------------------------------------------------------- |
| **frontend**      | 5173 | Vite dev server with hot-module-replacement             |
| **api**           | 8000 | FastAPI application with hot-reload                     |
| **postgres**      | 5432 | PostgreSQL 17 database                                  |
| **valkey**        | 6379 | Valkey (Redis-compatible) for caching and Celery broker |
| **celery-worker** | —    | Background task processing                              |
| **celery-beat**   | —    | Periodic task scheduler                                 |

## First Login

1. Open **http://localhost:5173** (auto-opened on `make run`)
1. Log in with `admin` / `admin`
1. **Change the default password immediately**
1. Copy the API token from the startup logs (or create a new one via the UI)

## SDK Integration

Once running, point any OpenTelemetry-compatible SDK at the OTLP endpoint:

```python
# Example: send traces to your local Overmind instance
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

exporter = OTLPSpanExporter(
    endpoint="http://localhost:8000/api/v1/traces/otlp",
    headers={"Authorization": "Bearer <your-api-token>"},
)
```

## Make Targets

```
make run              # Start all services (foreground)
make run-detached     # Start all services (background)
make stop             # Stop all services
make logs             # Tail logs for all services
make logs-api         # Tail API logs only
make migrate          # Run database migrations
make revision m="..." # Create a new migration
make test             # Run test suite
make lint             # Lint and format code
make psql             # Open a psql shell to the database
make clean            # Stop services and delete all data volumes
```

## Environment Variables

All settings have sensible defaults for local development. Only LLM keys need to be set.

| Variable            | Default                                                              | Description                                                   |
| ------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------- |
| `OPENAI_API_KEY`    | —                                                                    | OpenAI API key                                                |
| `ANTHROPIC_API_KEY` | —                                                                    | Anthropic API key                                             |
| `GEMINI_API_KEY`    | —                                                                    | Google Gemini API key                                         |
| `SECRET_KEY`        | `local-dev-secret-...`                                               | JWT signing key (change in production)                        |
| `DEBUG`             | `true`                                                               | Enable debug mode and SQL echo                                |
| `DATABASE_URL`      | `postgresql+asyncpg://overmind:overmind@postgres:5432/overmind_core` | PostgreSQL connection string                                  |
| `VALKEY_HOST`       | `valkey`                                                             | Valkey hostname                                               |
| `VALKEY_PORT`       | `6379`                                                               | Valkey port                                                   |
| `FRONTEND_URL`      | `http://localhost:5173`                                              | Frontend origin for CORS                                      |
| `API_TOKEN_PREFIX`  | `ovr_core_`                                                          | Prefix for generated API tokens (managed edition uses `ovr_`) |

## API Endpoints

All endpoints are under `/api/v1/`. Authentication is via `Authorization: Bearer <token>` header.

| Group           | Prefix         | Description                       |
| --------------- | -------------- | --------------------------------- |
| **Traces**      | `/traces`      | Create, list, filter traces       |
| **Spans**       | `/spans`       | Query individual spans            |
| **Prompts**     | `/prompts`     | Prompt template management        |
| **Agents**      | `/agents`      | Agent discovery and metadata      |
| **Jobs**        | `/jobs`        | Background job management         |
| **Suggestions** | `/suggestions` | Improvement suggestions           |
| **Backtesting** | `/backtesting` | Model backtesting runs            |
| **Layers**      | `/layers`      | Guardrail policy execution        |
| **Proxy**       | `/proxy`       | LLM proxy with policy enforcement |
| **OTLP**        | `/traces/otlp` | OpenTelemetry trace ingestion     |
| **IAM**         | `/iam`         | Login, projects, tokens           |

Interactive API docs are at **http://localhost:8000/docs**.

## Architecture

```
                ┌──────────────┐
 Browser ──────▶│  Vite (HMR)  │
                │  :5173       │
                └──────┬───────┘
                       │ proxy /api
                       ▼
                ┌──────────────┐    ┌──────────┐
┌──────────┐    │   FastAPI    │───▶│ Postgres │
│   SDKs   │───▶│   :8000     │    └──────────┘
└──────────┘    │   (OTLP)    │───▶│  Valkey   │
                └──────────────┘    └──────────┘
                       │
                       ▼
                ┌──────────────────────────┐
                │  Celery Worker + Beat    │
                │  (background processing) │
                └──────────────────────────┘
```

- **Vite** serves the React frontend with hot-module-replacement; proxies API calls to FastAPI
- **FastAPI** serves the REST API and OTLP ingestion
- **PostgreSQL** stores all data (traces, spans, prompts, users, projects)
- **Valkey** provides caching and acts as the Celery message broker
- **Celery** runs background tasks: agent discovery, auto-evaluation, prompt improvement, backtesting, job reconciliation

## Project Structure

```
overmind/
├── overmind/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Settings (from env vars)
│   ├── bootstrap.py         # Auto-provision default user/project/token
│   ├── celery_app.py        # Celery configuration and beat schedule
│   ├── api/v1/
│   │   ├── router.py        # Route assembly
│   │   ├── endpoints/       # API endpoint handlers
│   │   └── helpers/         # Auth, caching, response utilities
│   ├── models/              # SQLAlchemy ORM models
│   ├── overmind/            # Business logic (policies, LLMs, tracing)
│   ├── tasks/               # Celery background tasks
│   └── db/                  # Database engine and session management
├── alembic/                 # Database migrations
├── frontend/                # React/TypeScript UI
├── tests/                   # Test suite
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── pyproject.toml
```

## Development

### Running without Docker

If you prefer running directly on your machine:

```bash
# Install dependencies
poetry install

# Start Postgres and Valkey (you need these running separately)
# Then export the required env vars:
export DATABASE_URL="postgresql+asyncpg://overmind:overmind@localhost:5432/overmind_core"
export VALKEY_HOST=localhost
export OPENAI_API_KEY=sk-...

# Run migrations
alembic upgrade head

# Start the API
uvicorn overmind.main:app --host 0.0.0.0 --port 8000 --reload

# In another terminal — start the Celery worker
celery -A overmind.celery_worker worker --loglevel=info

# In another terminal — start the Celery beat scheduler
celery -A overmind.celery_worker beat --loglevel=info
```

### Frontend Development

The frontend runs as a Vite dev server inside Docker Compose with volume mounts — any changes to files in `frontend/` are picked up instantly via hot-module-replacement. No manual rebuilds needed.

API calls from the frontend are proxied through Vite to the FastAPI backend (configured in `frontend/vite.config.ts`).

## Contributing

Contributions are welcome! Here's how to get set up for local development:

1. **Fork and clone** the repository

1. **Install dependencies**

   ```bash
   poetry install
   ```

1. **Set up pre-commit hooks** — these run linting, formatting, and a few safety checks automatically before every commit (`pre-commit` is included in dev dependencies, so no separate install is needed)

   ```bash
   poetry run pre-commit install
   ```

1. **Create a branch** for your change

   ```bash
   git checkout -b my-feature
   ```

1. Build, add test cases and test using

   ```bash
   make test
   ```

1. **Open a pull request** against `main` with a clear description of what changed and why.

### Guidelines

- Keep pull requests focused — one feature or fix per PR
- Add or update tests for any behaviour you change
- Follow existing code style (enforced by `make lint` / pre-commit)
- Migrations go in `alembic/versions/` — generate with `make revision m="describe change"`
