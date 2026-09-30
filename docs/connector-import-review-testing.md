# Connector import and review verification

Implemented flow: credentials → source project → fixed time range → count and duration estimate → confirmed import → group review. Imported spans preserve provider trace structure. Setup closes after confirmation; each integration row shows a progress chip, imported count and ETA, then Review. The review dialog stages capability choices (including Leave unassigned) in row cards, then confirms them atomically. Approved pattern rules apply to later matching traces; new patterns need review.

## Failure cases

- Valid Langfuse keys cannot complete setup, or a preview ignores the selected dates.
- Counting imports or assigns data; duplicate provider pages inflate the count.
- Confirmation imports a different range, a failed/expired preview, or another project's preview.
- Generic observation names silently assign capabilities; unrelated tool paths collapse together.
- Reviewing unassigned has no effect, or provider updates undo a human decision.
- A new pattern inherits an unrelated rule, or a matching trace loses its approved assignment.
- A stale group revision assigns traces the reviewer has never seen.
- Changing an assignment preserves scores or execution bindings from the previous capability.
- Re-importing a range deletes history, duplicates spans, or loses parent relationships.
- REST and MCP apply different review rules or cross project boundaries.

## Automated verification

Run from the repository root after `uv sync --group dev --group test`. Backend tests use `tests.settings`, in-memory SQLite and mocked provider HTTP; no customer keys are needed. The workflow test drives credential setup, asynchronous preview, confirmed import, stored grouping, review and re-import through REST and the real domain services.

```sh
uv run pytest tests/test_connector*.py tests/test_langfuse*.py tests/test_langsmith*.py tests/test_braintrust*.py tests/test_galileo*.py tests/test_mcp_connectors.py tests/test_mcp_connector_job_poll.py tests/test_mcp_manifest.py tests/test_mcp_prompts.py tests/test_capability_identity.py tests/test_celery_topology.py -q
uv run pytest tests/test_connector_import_review.py tests/test_connector_mutable_rows.py tests/test_mcp_connectors.py -q
uv run pytest tests/ -n 4 --dist worksteal -q
PYTHONPATH=. uv run python tests/fixtures/verify_connector_upgrade.py
DJANGO_SETTINGS_MODULE=tests.settings uv run python manage.py migrate --noinput
make check-migrations
```

Observed September 29–30, 2026 (the local `.venv/bin/pytest` executable was used):

| Check                                                          | Result                                                                                          |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Provider/connector/MCP checks                                  | 314 passed before four additional edge-case scenarios                                           |
| Import/review, mutable provider rows and MCP regression checks | 17 passed, including the new scenarios                                                          |
| Full backend suite                                             | 3,853 passed, 9 skipped, 1 failed                                                               |
| Failing catalog test run in isolation                          | 1 passed                                                                                        |
| Existing-data upgrade                                          | 100 assigned imported traces grouped; all capability assignments and the native trace preserved |
| Empty database migration                                       | All migrations applied, including 0009 and 0010                                                 |
| Model drift check                                              | No changes detected                                                                             |

The full-suite failure was `tests/test_sdk_permutations.py::TestGatewayModelNamePermutations::test_bare_catalog_only_name_resolves_via_catalog`: an unrelated model-catalog request returned 404 instead of 200. It passed in isolation. That path was not changed. The migration-numbering script examines committed changes and reported none; the new migration files are uncommitted. They follow the checked-out `origin/main` migration 0008. Rebase and repeat the numbering check before publishing.

From `frontend/`:

```sh
bun run test
bun run typecheck
bun run check
bun run check:all
```

Results: **844 tests passed across 112 files**; typecheck, Biome, design, contrast and controls checks passed. The setup test verifies that counting does not trigger import and that the user must press the separate confirmation button. All applicable pre-commit hooks passed on the changed files.

From `overmind/`:

```sh
uv run --extra tracing --group dev python -m pytest tests/test_connector_cmd.py -q
```

Result: **6 passed**. The bundled MCP workflow documentation is versioned as SDK 0.1.79; no package was published.

## Earlier browser verification (before the review dialog change)

Use the existing Docker Compose deployment with its API, frontend, database, Redis and batch worker. Apply migrations to that local deployment, then start the synthetic provider:

```sh
python3 tests/fixtures/trace_provider.py
```

This serves a Langfuse-compatible fixture on `127.0.0.1:8734`: **120 traces / 240 spans**, divided into 80 question-answering traces, 30 refund traces and 10 ticket-routing traces. It accepts synthetic credentials and contains no customer data. Docker Desktop reaches it through `http://host.docker.internal:8734`; a host-based API uses `http://127.0.0.1:8734`.

1. In a local test project, create capabilities named Answer questions, Review refunds and Route tickets.
1. Open Observability → Integrations → Langfuse → New connection.
1. Enter name `Import review verification`, public key `pk-review-verification`, secret `sk-synthetic`, and the fixture host URL.
1. Confirm the source project and press Continue, then choose All available history and Count traces. Observed: **120 traces**, estimated **6–14 seconds**, and a separate **Import 120 traces** button. No spans are stored by counting.
1. Confirm. Observed: import complete, **120 traces / 240 spans**, and three rows sized **80 / 30 / 10**, all initially requiring review.
1. Set Answer questions to the corresponding capability. Set Review refunds to Leave unassigned. Leave Route tickets untouched. Observed: 80 assigned and reviewed, 30 unassigned and reviewed, 10 still awaiting review.
1. At a 390-pixel viewport the document remains 390 pixels wide; the table scrolls horizontally within its container. Restore the normal viewport after testing.

The local project `connector-import-review` retains this example. Auto-sync is off. A prior synthetic connection was disconnected and was left untouched. Screenshots:

- [Count and confirmation](connector-review/count-confirmation.png)
- [Assigned, unassigned and pending groups](connector-review/group-review.png)

## Boundaries of this version

Grouping uses exact structural signatures, tool names, input/output shapes and available system/prompt identities. It does not infer semantic capability equivalence. Generic traces with insufficient evidence and incomplete trees remain separate; human review determines the capability.

The count preview scans provider pages without storing spans. Very large ranges can take time to count; the count worker has a 120-second soft deadline and a 150-second hard task limit; abandoned counts fail after 180 seconds and reports failure rather than confirming a partial count. Import duration is an estimate, and provider updates or retention can change the final count. The live browser run used the synthetic Langfuse provider; production-scale volumes were not tested. The later diagnostic below checked the user’s configured source without importing data.

## Source project follow-up

The source selector remains explicit, including when only one project is returned. It now shares a single setup form with credentials and timeframe. Galileo labels it Source log stream. Empty source lists prevent advancing. The selected source remains visible beside the range and on the saved connection, with a source ID fallback if provider names cannot be fetched.

```sh
cd frontend
bun run test src/components/connectors/setup-wizard.test.tsx
bun run typecheck
bun run check:all
```

The three wizard scenarios failed before the change and passed afterward: single-source confirmation before counting; multiple-source selection and preview invalidation after changing sources; no available source blocking progress. Browser verification used the synthetic Langfuse provider and confirmed the source selector and ID before the range form. The selected source remained visible after Continue. The document stayed within a 390-pixel viewport. See [source selection](connector-review/source-selection.png). Typecheck, Biome and the design, contrast and controls checks passed.

## Review dialog and saved rules

Browser use was skipped for this follow-up at the user’s request. The earlier screenshots and browser results document the previous inline review UI.

Failure cases covered by workflow checks: dropdown changes saving before confirmation;
partial batch writes when a revision is stale; unassigned choices being treated as missing;
matching arrivals losing approved assignments; new structures inheriting an unrelated rule;
retired capabilities continuing to receive traces; and import progress reporting completion
before provider pagination finishes. The review dialog snapshots its rows until confirmation;
a conflict requires a refreshed review, with no partial assignments committed.

Follow-up verification commands (same local environments and mocked provider fixtures as above):

```sh
.venv/bin/pytest tests/test_connector*.py tests/test_langfuse*.py tests/test_langsmith*.py tests/test_braintrust*.py tests/test_galileo*.py tests/test_mcp_connectors.py tests/test_mcp_connector_job_poll.py tests/test_mcp_manifest.py tests/test_mcp_prompts.py tests/test_capability_identity.py tests/test_celery_topology.py -q
.venv/bin/pytest tests/test_connector_import_review.py tests/test_mcp_connectors.py -q
cd frontend
bun run test src/components/connectors/trace-groups.test.tsx src/components/connectors/setup-wizard.test.tsx
bun run typecheck
bun run check
bun run check:all
```

Observed: **320 backend checks passed**; after exposing each pattern's review-needed state, **13 focused workflow/MCP checks passed**. **5 frontend workflow checks passed**, covering source selection, separate import confirmation, draft assignments, a single batch confirmation including null, dialog closure on success, and rejection requiring reload. Backend workflows verify rollback of all assignments and audit records on a stale revision, reuse of approved assigned/unassigned rules, novel pattern review, retired capability review, structural changes losing the old assignment, and progress/ETA before and after import. Browser verification of this follow-up was intentionally skipped.

The final API schema and generated client expose pending review totals, per-pattern review state and estimated import time remaining. REST and MCP share the atomic review service; MCP uses `review_trace_groups` with an `assignments` array. Existing audit records store the approved rules, so this follow-up adds no database migration.

## Calendar, single form and stalled-count follow-up

Setup now keeps credentials, source and time range together. Custom dates use an inline calendar with month/year selectors; start/end dates are interpreted in the browser's local timezone, and the end includes the selected day, capped at the current time. Changing credentials, source or dates clears the count receipt.

Failure scenarios: empty multi-month ranges issuing one request per day; count scans downloading trace payloads; provider throttling hidden behind sleeps; abandoned tasks remaining running; stale count completions overwriting a newer request; manual date entry and unnecessary source/range transitions.

Repeat the provider regression command above and the frontend setup workflow command. New backend cases exercise an empty six-month range, identity-only cursor pages with visible partial counts, a stale running job, and an immediate 429 response. The three form scenarios cover calendar selection, same-form source/range selection, invalidating a counted source, and blocking an empty source list. The calendar and adjacent controls are checked through `bun run typecheck`, `bun run check` and `bun run check:all`; browser checks remain skipped at the user's request.

Live diagnostic: the configured Langfuse six-month count was still running, with zero published progress. Two active count tasks were confirmed via Celery inspection. The prior implementation walked daily windows, and a bounded v2 request returned HTTP 429. Stale requests were marked failed through the shared expiry service. Retrying the exact source and March 30–September 30, 2026 window through `request_preview` completed with **0 traces / 0 spans**; no import was launched. The new preview is `32e2f4c7-445e-4e22-beb8-b8831e02779c` in the local database. To repeat without exposing keys, read the source/window from the earlier preview, call `request_preview` with those values, then poll the resulting preview status; the existing API, Redis and batch worker and the saved Langfuse connection are required. This result applies to that connected source and range, not to other customer data.

Observed final follow-up checks: **324 provider/backend/MCP checks passed**, **3 setup workflow checks passed** (including calendar selection), and frontend typecheck, Biome, design, contrast and controls checks passed. The calendar dependency was installed in the existing frontend container by restarting that container; no additional development server was started.
