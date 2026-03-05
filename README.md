<img width="4484" height="764" alt="image" src="https://github.com/user-attachments/assets/af059b21-7199-4f0f-8610-63adabbaebc0" />

# Overmind

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/overmind.svg)](https://pypi.org/project/overmind/)
[![PyPI downloads](https://img.shields.io/pypi/dm/overmind.svg)](https://pypi.org/project/overmind/)
[![npm version](https://img.shields.io/npm/v/@overmind-lab/trace-sdk.svg)](https://www.npmjs.com/package/@overmind-lab/trace-sdk)
[![npm downloads](https://img.shields.io/npm/dm/@overmind-lab/trace-sdk.svg)](https://www.npmjs.com/package/@overmind-lab/trace-sdk)
[![Discord](https://img.shields.io/discord/WZJhx85CVK?label=Discord&logo=discord&logoColor=white)](https://discord.gg/WZJhx85CVK)

**Performance infrastructure for AI agents.** Overmind is an open-source platform for AI agent execution tracing, LLM observability, and continuous model improvement from production data. It sits between your application and LLM providers — collecting execution traces, evaluating them automatically, and recommending better prompts and models to reduce cost, improve quality, and lower latency.

Built for engineering teams running AI agents in production. Deployable as a single Docker Compose stack. Compatible with OpenAI, Anthropic, Google, and any OpenTelemetry-instrumented system.

**Key results from production deployments:**
- Up to 66% reduction in LLM inference costs
- Up to 25% improvement in agent task performance
- First traces flowing in under 5 minutes from SDK install

<img width="3022" height="1724" alt="image" src="https://github.com/user-attachments/assets/000aa5f2-df9f-4cb6-88e8-3a0fdf6777a5" />

## What is Overmind?

Overmind is performance infrastructure for AI agents. It provides three core capabilities:

1. **Execution tracing** — every LLM call recorded with full I/O, timing, tokens, and cost, compatible with OpenTelemetry distributed tracing standards
2. **Production observability** — LLM judge scoring evaluates each trace on quality, cost, and latency; surfaces actionable recommendations with before/after impact scores
3. **Continuous model improvement** — replays traces through alternative models and prompt variations; enables RL fine-tuning on production data so agents improve over time

Overmind is model-agnostic, framework-agnostic, and designed for zero latency impact on production systems. It is not a security tool or a compliance layer — it is infrastructure for making AI agents faster, cheaper, and more accurate.

Key Features:

- **Trace collection** — every LLM call recorded with full I/O, timing, tokens, and cost
- **Automatic agent detection** — extracts prompt templates from traces after 10+ calls
- **LLM judge scoring** — evaluates each trace on quality, cost, and latency with configurable criteria
- **Prompt experimentation** — generates and tests prompt variations against historical inputs
- **Model experimentation** — replays traces through alternative models for cost/quality comparison
- **Actionable suggestions** — surfaces recommendations with before/after impact scores
- **Feedback loop** — accept, reject, or tweak suggestions; the system refines over time
- **Full observability** — dashboard with trace browser, flame charts, and agent stats

Overmind sits between your application and LLM providers. It collects execution traces, evaluates them with LLM judges, and recommends better prompts and models to reduce cost, improve quality, and lower latency.

You install the SDK, swap one import, and keep building. Overmind handles the rest:

```
Your app (with Overmind SDK)
        │
        ▼
   Send traces ──────────▶ Overmind collects & stores
                                    │
                                    ▼
                           LLM Judge evaluates
                           on cost, latency, quality
                                    │
                           ┌────────┴────────┐
                           ▼                  ▼
                    Try new prompts     Try new models
                           │                  │
                           └────────┬─────────┘
                                    ▼
                           Recommendations
                           appear in dashboard
                                    │
                                    ▼
                           You provide feedback
                           (accept / reject / tweak)
                                    │
                                    ▼
                           System learns, repeats
```

For a detailed walkthrough of each step, see the [How Optimization Works](https://docs.overmindlab.ai/guides/how-it-works) guide and [docs.overmindlab.ai](https://docs.overmindlab.ai).

## Quick Start

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/) and Docker Compose.

```bash
# 1. Configure your LLM key(s)
cp .env.example .env
#    Edit .env and add at least one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY

# 2. Start everything
make run
```

On first startup the system will:

- Build images, install dependencies, and start all services
- Run database migrations automatically
- Create a default admin user (`admin` / `admin`)
- Create a default project and API token (printed in the logs)
- **Auto-open your browser** once all services are healthy

### First Login

1. Open **http://localhost:5173** (auto-opened on `make run`)
1. Log in with `admin` / `admin`
1. **Change the default password immediately**
1. Copy the API token from the startup logs (or create a new one via the UI)

## Connecting Your Agents

Install the Overmind SDK and swap one import. All your LLM calls are traced automatically.

### Python

```bash
pip install overmind
```

```python
import os
from overmind.clients import OpenAI

os.environ["OVERMIND_API_KEY"] = "<your-api-token>"
os.environ["OPENAI_API_KEY"] = "sk-..."

client = OpenAI()
response = client.chat.completions.create(
    model="gpt-5-mini",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

Anthropic and Google are also supported:

```python
from overmind.clients import Anthropic
from overmind.clients.google import Client as GoogleClient
```

### JavaScript / TypeScript

```bash
npm install @overmind-lab/trace-sdk openai
```

```ts
import { OpenAI } from "openai";
import { OvermindClient } from "@overmind-lab/trace-sdk";

const overmindClient = new OvermindClient({
  apiKey: "<your-api-token>",
  appName: "my-app",
  baseUrl: "http://localhost:8000",
});

overmindClient.initTracing({
  enableBatching: false,
  enabledProviders: { openai: OpenAI },
});

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
const response = await openai.chat.completions.create({
  model: "gpt-5-mini",
  messages: [{ role: "user", content: "Hello!" }],
});
```

### OpenTelemetry (any language)

Any OpenTelemetry-compatible SDK can send traces via the OTLP endpoint:

```python
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

exporter = OTLPSpanExporter(
    endpoint="http://localhost:8000/api/v1/traces/otlp",
    headers={"Authorization": "Bearer <your-api-token>"},
)
```

See the [SDK Reference](https://docs.overmindlab.ai/guides/sdk-reference) for full details.

## Services

| Service           | Port | Description                                             |
| ----------------- | ---- | ------------------------------------------------------- |
| **frontend**      | 5173 | Vite dev server with hot-module-replacement             |
| **api**           | 8000 | FastAPI application with hot-reload                     |
| **postgres**      | 5432 | PostgreSQL 17 database                                  |
| **valkey**        | 6379 | Valkey (Redis-compatible) for caching and Celery broker |
| **celery-worker** | —    | Background task processing                              |
| **celery-beat**   | —    | Periodic task scheduler                                 |

## Environment Variables

All settings have sensible defaults for local development. Only LLM keys need to be set.

| Variable            | Default                | Description                            |
| ------------------- | ---------------------- | -------------------------------------- |
| `OPENAI_API_KEY`    | —                      | OpenAI API key                         |
| `ANTHROPIC_API_KEY` | —                      | Anthropic API key                      |
| `GEMINI_API_KEY`    | —                      | Google Gemini API key                  |
| `SECRET_KEY`        | `local-dev-secret-...` | JWT signing key (change in production) |
| `DEBUG`             | `true`                 | Enable debug mode and SQL echo         |

Database, Valkey, and Celery connection strings are pre-configured in `docker-compose.yml` and generally don't need to be changed for local development.

## API Endpoints

All endpoints are under `/api/v1/`. Authentication is via `Authorization: Bearer <token>` header.

| Group           | Prefix         | Description                   |
| --------------- | -------------- | ----------------------------- |
| **Traces**      | `/traces`      | Create, list, filter traces   |
| **Spans**       | `/spans`       | Query individual spans        |
| **Prompts**     | `/prompts`     | Prompt template management    |
| **Agents**      | `/agents`      | Agent discovery and metadata  |
| **Jobs**        | `/jobs`        | Background job management     |
| **Suggestions** | `/suggestions` | Improvement suggestions       |
| **Backtesting** | `/backtesting` | Model backtesting runs        |
| **OTLP**        | `/traces/otlp` | OpenTelemetry trace ingestion |
| **IAM**         | `/iam`         | Login, projects, tokens       |

Interactive API docs are at **http://localhost:8000/docs**.

## Documentation

- **Full documentation**: [docs.overmindlab.ai](https://docs.overmindlab.ai)
- **Interactive API reference**: [http://localhost:8000/docs](http://localhost:8000/docs) (when running locally)
- **Python SDK**: [SDK Reference](https://docs.overmindlab.ai/guides/sdk-reference)
- **JavaScript SDK**: [JS/TS SDK Reference](https://docs.overmindlab.ai/guides/sdk-js)
- **Integrations**: [Providers & Frameworks](https://docs.overmindlab.ai/guides/integrations)

## Development

### Architecture

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

### Project Structure

```
overmind/
├── overmind/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Settings (from env vars)
│   ├── bootstrap.py         # Auto-provision default user/project/token
│   ├── celery_app.py        # Celery configuration and beat schedule
│   ├── celery_worker.py     # Celery entry point (used by CLI at runtime)
│   ├── api/v1/
│   │   ├── router.py        # Route assembly
│   │   ├── endpoints/       # API endpoint handlers
│   │   └── helpers/         # Auth, caching, response utilities
│   ├── models/              # SQLAlchemy ORM models
│   ├── core/                # Business logic (policies, LLMs, tracing)
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

### Backend

Python 3.13 · FastAPI · SQLAlchemy 2 (async) · Celery · Poetry

| Directory                    | What lives there                                                   |
| ---------------------------- | ------------------------------------------------------------------ |
| `overmind/api/v1/endpoints/` | REST endpoint handlers (traces, agents, suggestions, etc.)         |
| `overmind/tasks/`            | Celery background tasks (agent discovery, evaluation, backtesting) |
| `overmind/core/`             | Business logic — LLM calls, template extraction, model resolution  |
| `overmind/models/`           | SQLAlchemy ORM models and Pydantic serialization schemas           |
| `overmind/db/`               | Async database engine, session management, Valkey client           |
| `alembic/`                   | Database migrations                                                |

### Frontend

React 19 · TypeScript · Vite · TanStack Router & Query · Tailwind CSS · shadcn/ui

The frontend is a unified codebase that serves both the open-source and managed editions (controlled by `VITE_SELF_HOSTED` env var).

| Directory                  | What lives there                                          |
| -------------------------- | --------------------------------------------------------- |
| `frontend/src/routes/`     | File-based routing (TanStack Router, auto code-splitting) |
| `frontend/src/components/` | App components and shadcn/ui primitives                   |
| `frontend/src/hooks/`      | Data-fetching hooks wrapping TanStack Query               |
| `frontend/src/api/`        | Auto-generated TypeScript API client from OpenAPI spec    |
| `frontend/src/lib/`        | Utility functions, formatters, schemas                    |

API calls from the frontend are proxied through Vite to the FastAPI backend (configured in `frontend/vite.config.ts`). Any changes to files in `frontend/` are picked up instantly via hot-module-replacement.

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

# In another terminal — start the frontend
cd frontend && bun install && bun run dev
```

### Make Targets

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

## Frequently Asked Questions

**What is Overmind used for?**
Overmind is used for AI agent execution tracing, LLM production monitoring, prompt optimisation, model backtesting, and continuous fine-tuning of language models on real production data. It is designed for engineering teams running AI agents in production environments.

**How is Overmind different from LangSmith or Helicone?**
LangSmith and Helicone are observability tools — they show you what your agents did. Overmind closes the loop: it collects traces, evaluates them, generates and backtests improvements against your real production history, and enables RL fine-tuning so your models get better over time. It is also fully open-source and self-hostable.

**Does Overmind work with LangChain, CrewAI, or AutoGPT?**
Yes. Overmind is framework-agnostic. Any system that makes LLM calls via OpenAI, Anthropic, or Google APIs can be instrumented with the Python or JavaScript SDK in minutes. For frameworks not covered by the native SDKs, any OpenTelemetry-compatible instrumentation works via the OTLP endpoint.

**Can I self-host Overmind?**
Yes. The full platform runs on a single Docker Compose stack with no external dependencies beyond your LLM API keys. There is also a managed cloud edition at [overmindlab.ai](https://overmindlab.ai).

**What LLM providers does Overmind support?**
OpenAI, Anthropic, and Google Gemini are supported natively. Any provider reachable via OpenTelemetry distributed tracing can be integrated via the OTLP endpoint.

**How long does it take to get first traces flowing?**
Under 5 minutes from SDK install to first trace appearing in the dashboard.

## Contributing

Contributions are welcome! All contributions to Overmind are subject to a **Contributor License Agreement (CLA)**. The CLA is currently being drafted and will be shared here once finalized. By submitting a pull request, you agree to comply with the CLA once it is published.

### Getting Started

1. **Fork and clone** the repository

1. **Install dependencies**

   ```bash
   poetry install
   ```

1. **Set up pre-commit hooks** — linting, formatting, and safety checks run automatically before every commit (`pre-commit` is included in dev dependencies)

   ```bash
   poetry run pre-commit install
   ```

1. **Create a branch** for your change

   ```bash
   git checkout -b my-feature
   ```

1. **Build and test**

   ```bash
   make test
   ```

1. **Open a pull request** against `main` with a clear description of what changed and why.

### Guidelines

- Keep pull requests focused — one feature or fix per PR
- Add or update tests for any behaviour you change
- Follow existing code style (enforced by `make lint` / pre-commit)
- Migrations go in `alembic/versions/` — generate with `make revision m="describe change"`
