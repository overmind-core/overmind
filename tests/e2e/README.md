# Overmind E2E Tests

End-to-end tests that exercise the full Overmind platform — from onboarding
through ML features — against a live docker-compose stack.

## Prerequisites

1. **Docker stack running**: `make run` (or `make run-detached`) from the repo root.
   Wait until the health check passes (`http://localhost:8000/health`).

1. **LLM API keys** — source the project `.env` before running tests so the mock
   agents can call LLM APIs and the backend can run judge/tuning/backtesting:

   ```bash
   export $(grep -v '^#' ../../.env | xargs)
   ```

   LLM responses are cached automatically on first call. Subsequent runs serve
   from cache without needing API keys for the mock agents (only the backend
   workers still need keys for judge/tuning/backtesting).

   - `GOOGLE_API_KEY` — required on first run (current mock agents use Gemini)
   - `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — only if you enable those providers

## Setup

```bash
cd tests/e2e
poetry install
```

> **Tip:** The E2E Poetry env is separate from the backend's. If `poetry run`
> gives `command not found` errors (e.g. from pyenv shims), use the venv
> directly: `$(poetry env info -e)/bin/pytest`.

## Running Tests

### Recommended first-time flow

```bash
# 1. Source env vars (needed for real LLM calls on first run)
export $(grep -v '^#' ../../.env | xargs)

# 2. Run all stages — cache is auto-populated on first run (~2 min for LLM calls)
poetry run pytest -v
```

### Normal run (cached LLM responses, skips already-completed stages)

```bash
poetry run pytest -v
```

LLM vendor responses are served from the `cache/` directory. On cache miss
(first run, or after changing mock agent prompts/queries), the real LLM API
is called and the response is saved for next time. Stages whose results
already existed in the database **at session start** are skipped.

> **Important:** The cache key is SHA-256(url_path, request_body). Changing a
> mock agent's system prompt, model, or queries changes the request body,
> which invalidates the cache for those queries. The new responses will be
> fetched and cached automatically on the next run.

### Re-run specific stages (force re-execution)

```bash
poetry run pytest --e2e-rerun -k "test_06" -v
```

Purges previous results for the stage and re-runs it. The stage skip check is
based on a snapshot taken at session start, so tests within the same session
never skip each other.

### Reset the database (keep cached LLM responses)

```bash
# From the repo root:
make e2e-reset-db

# Then re-run everything using the existing cache:
make e2e
```

This drops all tables, re-runs Alembic migrations, and restarts the API
container so the default admin user is re-provisioned — giving you a
completely fresh database while keeping the 90 cached LLM responses in
`tests/e2e/cache/`. This is the most reliable way to get a clean slate since
the project-delete API cannot cascade to traces.

### Full clean run (via pytest)

```bash
poetry run pytest --e2e-clean -v
```

Attempts to delete the E2E project via the API, then runs everything from
scratch. Prefer `make e2e-reset-db` if you need a guaranteed clean state.

### Run only certain stages

```bash
# Just onboarding and telemetry
poetry run pytest -k "test_01 or test_02" -v

# Just the ML feature tests (assumes earlier stages completed)
poetry run pytest -k "test_04 or test_05 or test_06 or test_07 or test_08" -v
```

## CLI Flags & Make Targets

| Command / Flag      | Effect                                                  |
| ------------------- | ------------------------------------------------------- |
| `make e2e`          | Normal run (cached responses, skip completed stages)    |
| `make e2e-rerun`    | Purge previous results for each stage, then re-run      |
| `make e2e-clean`    | Delete E2E project via API, then run (best-effort)      |
| `make e2e-reset-db` | Drop all tables + re-migrate — clean DB, keep LLM cache |
| `--e2e-rerun`       | pytest flag: purge + re-run stages                      |
| `--e2e-clean`       | pytest flag: delete E2E project at session start        |

## Environment Variables

| Variable            | Required                | Default                 | Purpose               |
| ------------------- | ----------------------- | ----------------------- | --------------------- |
| `E2E_BASE_URL`      | No                      | `http://localhost:8000` | Overmind API base URL |
| `GOOGLE_API_KEY`    | On first run (no cache) | —                       | Mock agent LLM calls  |
| `OPENAI_API_KEY`    | Only if provider used   | —                       | Mock agent LLM calls  |
| `ANTHROPIC_API_KEY` | Only if provider used   | —                       | Mock agent LLM calls  |

## Architecture

Tests run **on the host** in a dedicated Poetry environment. Mock agents use the
real Overmind Python SDK (`pip install overmind`) which auto-instruments LLM calls
via OpenTelemetry. LLM vendor HTTP calls are intercepted at the httpx transport
layer so cached responses can be replayed while still generating real OTel traces.

### Test stages

| Stage | File                         | What it tests                                  | Approx time                    |
| ----- | ---------------------------- | ---------------------------------------------- | ------------------------------ |
| 1     | `test_01_onboarding.py`      | Login, project, API token                      | ~5s                            |
| 2     | `test_02_telemetry.py`       | Trace ingestion (OpenAI, 30+15 spans)          | ~90s (no-cache) / ~5s (cached) |
| 3     | `test_03_agent_discovery.py` | Template extraction (exactly 2 agents)         | ~15s                           |
| 4     | `test_04_evaluations.py`     | LLM judge scoring (initial, lenient)           | ~2 min                         |
| 5     | `test_05_agent_review.py`    | Review + strict criteria (clears old scores)   | ~30s                           |
| 6     | `test_06_rescore.py`         | Re-scoring with strict criteria                | ~5 min                         |
| 7     | `test_07_prompt_tuning.py`   | Prompt improvement (now has room to improve)   | ~5 min                         |
| 8     | `test_08_backtesting.py`     | Model backtesting (3 cheap alternative models) | ~15 min                        |

### Mock agent design

The two mock agents are **deliberately structurally distinct** so the
template extractor identifies them as separate prompt templates:

- **QA agent** (`qa_agent.py`): verbose natural-language system prompt,
  natural-language questions (30 queries). Uses gpt-5-mini —
  overkill for trivial Q&A, so backtesting should recommend a cheaper model.
- **Tool agent** (`tool_agent.py`): structured `<<FUNCTION_EXECUTOR>>`
  system prompt with explicit function signatures, structured
  `calculate:/lookup_fact:` query format (15 queries). Uses gpt-5-mini with
  tool definitions.

The vocabulary and structure differences prevent the template extractor's
anchor-based grouping from merging them. If you modify either agent's system
prompt or queries, verify that agent discovery still finds exactly 2 agents.

### LLM response caching

On the first run, mock agents make real LLM API calls (30 QA + 15 tool = 45
total). Responses are cached as JSON files under
`cache/{provider}/`. On
subsequent runs a custom httpx transport serves cached responses without
hitting the real API. OTel traces are still generated because the SDK
instrumentor wraps at a higher level than the transport.

The cache strips `content-encoding` and `transfer-encoding` headers to avoid
decompression errors when replaying responses.

### Stage skip / rerun semantics

The auto-skip logic snapshots which stages are already complete **at the
start of the pytest session**. Only those pre-existing stages are skipped.
Stages that become complete during the current run (e.g. after the QA agent
sends traces) do **not** cause subsequent tests in the same stage to skip.

`--e2e-rerun` removes the "already complete" flag for each stage and attempts
to purge its results (where the API supports it).

### Known limitations

- **Project deletion** can fail with 500 if traces exist (FK constraint on
  `traces.project_id`). `--e2e-clean` handles this gracefully but the project
  may survive. Use `make e2e-reset-db` for a guaranteed clean slate.
- **Prompt tuning** depends on the backend's LiteLLM being able to generate
  outputs. If the Celery worker's LLM calls fail, the tuning job will report
  `failed` with `All output generations failed`.
- **Backtesting eligibility** has prerequisites (scored spans, eligible prompt).
  If earlier stages didn't complete fully, backtesting will skip with a 400.
- The Overmind SDK accepts `overmind_api_key` and `overmind_base_url` — **not**
  `overmind_traces_url`. The SDK auto-detects `localhost:8000` for `ovr_core_`
  prefixed tokens.
- The `list_traces` API returns PascalCase keys (`TraceId`, `Inputs`, `Outputs`)
  matching the `SpanResponseModel` aliases, not snake_case.
