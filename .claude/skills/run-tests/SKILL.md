---
name: run-tests
description: How to run the platform test suites without wasting minutes — log-to-file pattern, when to skip tests entirely. Use before running pytest or the frontend test suite.
---

# Running tests on this repo

Test runs here are expensive (backend suite ~90s+, frontend ~40s). Two rules:

## 1. Decide whether to run at all

For simple, self-evident fixes (renames, copy changes, small mechanical edits):
**do not run the suite**. Typecheck + lint is the bar:

```bash
cd frontend && bun run typecheck && bun run lint   # frontend
uv run ruff check .                                # backend
```

Say plainly that tests weren't run. Reserve full runs for changes whose
behavior you can't reason through.

## 2. Run once, tee to a log, grep the log

Never re-run a suite just to filter its output differently:

```bash
uv run pytest tests/ 2>&1 | tee "$SCRATCHPAD/pytest.log"
grep -E "FAILED|ERROR|passed|failed" "$SCRATCHPAD/pytest.log"
```

(`$SCRATCHPAD` = the session scratchpad directory.) Re-run only after actually
changing code, scoped to the failing files/tests:

```bash
uv run pytest tests/test_foo.py::test_bar 2>&1 | tee "$SCRATCHPAD/pytest-rerun.log"
```

## Journeys

`make test-journeys` runs `tests/journeys/` against the live stack. It needs
the compose `postgres` and `redis` services, and uses Redis DB 15. Each journey
writes a run record to `tests/journeys/.runs/<timestamp>/`; read it for the
LLM requests, background task failures and the error. `make test` does not
collect journeys unless `TEST_REDIS_URL` is set. The journeys take about six minutes, and CI runs them as their own job beside `test`;
`make test-journeys test_args="-k <name>"` runs one. `test_kit_*.py` files run one
journey per vendor (connectors, workshop LLM engines, GPU training outcomes): a new
vendor joins its kit's parametrize list.

## Where a test belongs

- **Journey** (`tests/journeys/`): a customer promise across CLI, MCP, REST, SDK, worker and Console on the live stack. `tests/journeys/console/` builds the self-hosted Console (`bun run build`, ~4 s) and drives it with Playwright on its own thread; `uv run playwright install chromium-headless-shell` once per machine.
- **Contract kit** (`tests/test_*_contract.py`, `test_otlp_dialects.py`): one table over every vendor or dialect, the real client talking to a network fake.
- **Unit test**: pure logic with many edge cases, through a public entry point.

The unit suite runs behind the same outbound guard as the journeys: sockets reach loopback only, and every HTTP call must be answered by `tests/fakes` (`fake_llm` is autouse; `fake_modal`, `sft`, `serving`, `clerk`, `stripe_api`, `scripted(host)`, `slept` on request). FakeLLM serves chat, streaming (`stream_rounds`), the OpenRouter catalog (`catalog_payload`, `limits`, `prices`), and Jev decisions (`decide`; the Jev path needs Redis, so point `CACHES` at `TEST_REDIS_URL`). The Cursor SDK dials out from a bridge process the guard cannot see, so `CURSOR_API_KEY` is removed unless a test asks for the `cursor` fake.

A known product defect gets a `pytest.mark.xfail(strict=True, reason=...)` control that asserts the correct behaviour; it turns red when the fix lands, and the marker goes.

## Gotchas

- Backend tests need the compose `postgres` service on `localhost:5432`
  (`TEST_POSTGRES_HOST`/`TEST_POSTGRES_PORT` override it).
- `uv add`/`uv remove` resync the venv WITHOUT dev/test groups — pytest
  vanishes. Restore with `uv sync --group dev --group test`.
- Celery-dependent behavior needs `docker compose restart` of the worker to
  pick up backend changes; API containers hot-reload.
