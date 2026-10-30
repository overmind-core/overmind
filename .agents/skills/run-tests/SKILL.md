---
name: run-tests
description: How to run the platform test suites without wasting minutes — log-to-file pattern, when to skip tests entirely. Use before running pytest or the frontend test suite.
---

# Running tests on this repo

Test runs here are expensive (`make test` about a minute, frontend about 40 s, journeys a few minutes). Two rules:

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

## Coverage map

`tests/coverage_map.py` lists every entry a test may cover, ordered by severity.
Each test file sets `pytestmark = pytest.mark.covers("<entry>")`; a test inside it
that owns another entry takes its own `@pytest.mark.covers(...)`. Collection fails
on a test with no entry, an unknown entry, a promise outside `tests/journeys/` or
a second journey for a promise; the `coverage-map` hook fails when an entry has no
owner. Vitest files own `console-logic`.

| Severity | A failure means | Gate |
|---|---|---|
| S1 | A security breach, a wrong charge, or lost or changed data | One owning test at the lowest tier that reproduces; never quarantined; a fix ships with a test that fails before it |
| S2 | A vertical or the platform stops for every customer | One journey per promise; one owning test per platform invariant |
| S3 | One path, integration, model family or displayed fact is wrong | Contract tables (one row per dialect, vendor, provider) and logic tests |
| S4 | A heuristic or a presentation detail is wrong | A logic test only for a real edge case or a fixed bug |

A new behaviour joins an existing entry. A new entry or a new journey variant is a
change to the map, and so is a new promise.

## Where a test belongs

- **Journey** (`tests/journeys/test_<promise>.py`): the one end-to-end run of a product vertical across CLI, MCP, REST, SDK, worker and Console on the live stack. An edge case never gets its own journey; fold the assertion into the journey or test it at a lower tier.
- **Stack owner** (`tests/journeys/test_<entry>.py`): an invariant that needs the live worker, beat or API to reproduce, such as tenancy over every route or a cancelled GPU call.
- **Contract table** (`tests/test_*_contract.py`, `test_otlp_dialects.py`): one table over every vendor, dialect or provider, the real client talking to a network fake.
- **Unit test**: pure logic with real edge cases, through a public entry point.
- **Load check** (`pytest.mark.load`): capacity with wall-clock bounds. `make test` and `make test-journeys` skip them; `make test-load` runs them.
- **Migration replay**: a data migration that rewrites rows ships a replay test on `tests/migration_replay.scratch_database`. Delete the test once a published release has applied the migration.

The unit suite runs behind the same outbound guard as the journeys: sockets reach loopback only, and every HTTP call must be answered by `tests/fakes` (`fake_llm` is autouse; `fake_modal`, `sft`, `serving`, `decisions`, `clerk`, `stripe_api`, `scripted(host)`, `slept` on request). FakeLLM serves chat, streaming (`stream_rounds`), the OpenRouter catalog (`catalog_payload`, `limits`, `prices`), and Jev decisions (`decide`; the Jev path needs Redis, so point `CACHES` at `TEST_REDIS_URL`).

A known product defect gets a `pytest.mark.xfail(strict=True, reason=...)` control that asserts the correct behaviour; it turns red when the fix lands, and the marker goes.

## Journeys

`make test-journeys` runs `tests/journeys/` against the live stack with xdist (`--dist loadfile`, one Redis DB pair per worker; DB 12 stays with the load subprocess). It needs the compose `postgres` and `redis` services. The Console builds once per run into a shared folder and each worker serves it against its own API; `uv run playwright install chromium-headless-shell` once per machine. Each journey writes a run record to `tests/journeys/.runs/<timestamp>/`; read it for the LLM requests, background task failures and the error. `make test` never collects journeys. `make test-journeys test_args="-k <name>"` runs one.

A journey waits on observable state (`drain`, `settle`, `until`, `console.sees`), never on wall time, animations or fonts; the `test-seams` hook refuses new ones.

## Database transactions

Tests run inside a rolled-back transaction by default. Call MCP tools with `mcp_fixtures.call_tool`: it runs thread-sensitive handlers on the test thread and runs on-commit work. Use `django_db(transaction=True)` only when another thread or process must see the rows (the ASGI MCP app takes `mcp_fixtures.asgi_db`, sandboxes, admission races). After such a test, only the tables it wrote are emptied.

## Gotchas

- Backend tests need the compose `postgres` service on `localhost:5432`
  (`TEST_POSTGRES_HOST`/`TEST_POSTGRES_PORT` override it).
- `uv add`/`uv remove` resync the venv WITHOUT dev/test groups — pytest
  vanishes. Restore with `uv sync --group dev --group test`.
- Compose workers restart through watchmedo on `.py` changes under `overbae/`; API containers hot-reload. A non-Python edit under `overbae/` (for example `overbae/modal/models.json`) needs `docker compose restart <worker>`. Recreate a worker after a dependency change (AGENTS.md Gotchas).
