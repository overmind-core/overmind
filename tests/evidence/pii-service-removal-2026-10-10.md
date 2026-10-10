# Unused PII service removal

The hosted GLiNER service was disconnected from the current product. Repository
searches found no runtime caller outside `overbae/services/pii` itself: only its
own test suite imported it. No API, MCP tool, current Workshop pipeline or
training path invoked it. Its references to automatic Workshop scoring and the
old score cache described removed workflows.

## Change

- Removed the orphaned PII package, Modal worker, Django settings and dedicated
  test suite, including the three tests requiring an unconfigured live endpoint.
- Removed the recursive redaction helper whose only consumer was that package.
- Removed the dependency comment and architecture reference specific to this
  worker. Modal remains a dependency for active training and serving workers.
- Updated the product description to promise explicit, versioned transformations
  rather than automatic redaction that the current product did not perform.
- Replaced the obsolete automatic-redaction expected failure with a trace-to-dataset
  journey verifying that landing preserves the full source text, including an
  email address, for explicit transformation. This is a changed test expectation
  aligned with the current source-preservation contract; no redaction behavior was
  implemented or disabled by this cleanup.

The SDK's active tracing/secret redaction and transfer/telemetry protections are
independent of the deleted service. No cloud deployment or resource deletion was
performed.

## Failure cases checked

- A hidden import could prevent backend startup or test collection.
- Removing the service could unexpectedly change landed trace content.
- Shared utility removal could break SDK redaction or transfer behavior.
- Remaining configuration, MCP or architecture references could advertise a
  feature that no longer exists.

## Reproduction and observed results

Environment: the current branch on top of
`b5c466e00e69340f83133c3b4aee55967b5ffdc7`, installed backend and SDK dependencies,
existing local Postgres and Redis. Journeys run their isolated local API and
Celery worker, use a test database and fake model providers, and make no paid
provider calls. `/private/tmp/branch_e2e_settings.py` contains:

```python
from tests.settings import *  # noqa: F403

DATABASES["default"]["NAME"] = "branch_e2e_20261010"
```

Before deleting the service, the source-preservation journey passed:

```sh
PYTHONPATH=/private/tmp:. TEST_REDIS_URL=redis://localhost:6379/15 .venv/bin/pytest tests/journeys/test_data_workshop.py::test_trace_landing_preserves_source_text_for_explicit_transformation -q --no-cov --ds=branch_e2e_settings
```

Observed: **1 passed**, `/tmp/pii-removal-before.log`.

After removal:

```sh
PYTHONPATH=/private/tmp:. TEST_REDIS_URL=redis://localhost:6379/15 .venv/bin/pytest tests/journeys/test_data_workshop.py tests/journeys/test_boot.py -q -rxX --no-cov --ds=branch_e2e_settings
PYTHONPATH=/private/tmp:. .venv/bin/pytest tests/ --collect-only -q --no-cov --ds=branch_e2e_settings
```

Observed: **15 passed, 1 expected failure** in 20.68 seconds, with no new failures;
**4,311 backend tests collected** without import errors. Logs:
`/tmp/pii-removal-journeys.log`, `/tmp/pii-removal-collection.log`. Journey receipts
are in `tests/journeys/.runs/20261010T222438Z/`. The remaining expected failure is
the previously recorded capability attribution issue for multi-capability traces.

From `overmind/`:

```sh
uv run pytest tests/test_tracing_serialize.py tests/test_analytics.py tests/test_dataset_cmd.py -q
```

Observed: **65 passed**, `/tmp/pii-removal-sdk-redaction.log`.

Scoped Ruff lint/format and `git diff --check` passed. A code/configuration search
after deletion found no remaining references to `PII_NER`, `modal_pii_ner`,
`services.pii`, `gliner` or `recurse_redact`; historical evidence retains its
original names and results. All 206 pre-existing untracked files still match their
starting SHA-256 hashes. This records validation before the cleanup commit.

The three PII skip entries are retired with the unused subsystem, not converted
into passing tests. Five dependency-related skip entries from the original
regression run remain; the two Redis skips were separately rerun and passed.

Later follow-ups ran all five dependency skips and fixed the trace-attribution
expected failure. See `dead-features-and-remaining-tests-2026-10-10.md` and
`platform-regression-2026-10-10.md` for their final disposition.
