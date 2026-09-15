# Overmind Platform

Open-source platform for continuously improving production agents with real usage data.

**Console:** [console.overmindlab.ai](https://console.overmindlab.ai/) ·
**Site:** [overmindlab.ai](https://www.overmindlab.ai/) ·
**Docs:** [docs.overmindlab.ai](https://docs.overmindlab.ai)

## What it does

Overmind scans your repo and builds a model of your agents — capabilities, behaviours, prompts, and tools, each pinned to the file and line that defines it. Production telemetry binds to that model, and every downstream stage reads it:

| Pillar             | What it does                                                                         |
| ------------------ | ------------------------------------------------------------------------------------ |
| **Observability**  | Capability scanning, auto-instrumentation, coverage scoring, live scoring on arrival |
| **Data Workshop**  | Production traces become audited, redacted training and eval data                    |
| **Agent Testing**  | Generated evals, scored candidates, winning diffs in your repo                       |
| **Model Training** | Fine-tune on your own data, benchmark against the incumbent, serve it                |

This repo contains the full stack:

```
overmind-platform/
├── overbae/          # Django API
│   ├── api/          # DRF views, serializers, OTLP + OpenAI-compatible endpoints
│   ├── models/       # agents, traces, datasets, evaluation, finetuning, billing
│   ├── services/     # domain logic (scan, eval, workshop, finetuning, mcp)
│   ├── tasks/        # Celery tasks
│   └── modal/        # Modal training/serving entrypoints
├── frontend/         # React Console (Vite + TanStack Router)
├── overmind/         # Tracing SDK + CLI (published to PyPI as `overmind`)
└── .claude/skills/   # Agent skills (see below)
```

Traces arrive via the SDK (`pip install "overmind[tracing]"`, `npm install @overmind-lab/trace-sdk`), raw OTLP (`POST /api/v1/traces`), or Langfuse sync. Inference is OpenAI-compatible (`/api/v1/chat/completions`, `/api/v1/models`).

## Setup

Requirements: Docker, [uv](https://docs.astral.sh/uv/), [Bun](https://bun.sh/).

```bash
cp .env.example .env   # fill in at minimum OPENROUTER_API_KEY
docker compose up      # full stack: API, Console, Postgres, Redis, Celery workers
```

The Console runs at `http://localhost:3000`, the API at `http://localhost:8000`. Without `STRIPE_SECRET_KEY`, billing runs in OSS mode (spend metering only, no quotas). Training/serving backends (Modal, Baseten) are optional and selected with `FINETUNING_BACKEND`.

For development outside Docker:

```bash
make run               # Django + Vite
make worker            # local Celery (all queues, solo pool)
```

Common commands:

```bash
make test                  # backend tests (test-serial for the non-parallel run)
make lint-format           # or lint-backend / lint-frontend
make generate_api_client   # after backend API changes — regenerates frontend/src/openapi/
```

## Working with coding agents

### MCP

MCP is the first-class agent surface. Create a project API key in Console (Settings → API keys), then point your agent at `POST {API_BASE}/api/mcp/` with an `X-Api-Key: ovr_…` header. For example, in Cursor's `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "overmind": {
      "url": "http://localhost:8000/api/mcp/",
      "headers": { "X-Api-Key": "ovr_…" }
    }
  }
}
```

Claude Code: `claude mcp add --transport http overmind <url> --header "X-Api-Key: ovr_…"`. Codex: `overmind init --ide codex && overmind sync` from your agent repo.

### Skills

`.claude/skills/` holds skills for working on this codebase — subsystem maps (`backend-architecture`, `data-workshop`, `mcp`), procedures (`api-endpoints`, `run-tests`, `pr-etiquette`), and conventions (`engineering-taste`, `code-comments`, `frontend-design`). Claude Code and Cursor load them automatically on demand; `AGENTS.md` is the always-on playbook that indexes them. If you contribute with a coding agent, no setup is needed — the agent reads the relevant skill before touching a subsystem.

## Docs

Full product and API documentation lives at [docs.overmindlab.ai](https://docs.overmindlab.ai), maintained in the sibling [`overmind-core/docs`](https://github.com/overmind-core/docs) repo — quickstart, tracing setup, agent testing, data workshop, and model training guides.
