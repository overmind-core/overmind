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
collect journeys unless `TEST_REDIS_URL` is set.

## Gotchas

- Backend tests need the compose `postgres` service on `localhost:5432`
  (`TEST_POSTGRES_HOST`/`TEST_POSTGRES_PORT` override it).
- `uv add`/`uv remove` resync the venv WITHOUT dev/test groups — pytest
  vanishes. Restore with `uv sync --group dev --group test`.
- Celery-dependent behavior needs `docker compose restart` of the worker to
  pick up backend changes; API containers hot-reload.
