# Workshop MCP acceptance contract

Scope: finish the native-agent connection and workflow verification. Do not change
the inherited migration numbering. Use the original checkout and existing local
API, PostgreSQL, Redis and Celery workers. No paid provider execution.

## Continued adversarial coverage

Results of this continuation, including catalogue gaps and replay limits, are in
`workshop-catalogue-regression.md` and `workshop-regression-results.json`.

Failure cases recorded before the next regression changes:

- A valid 255-character partition name overflows generated member names.
- Misspelled explicit grouping fields silently disappear, falsely claiming a
  group-safe partition. Projected-away groups must still work from saved lineage.
- Account-key mutations or returned resource links select the wrong project when
  the same account belongs to two projects.
- Concurrent partition requests share a project request key but lock different
  source datasets, producing an internal error instead of a stable conflict.
- More than ten source files become undiscoverable through MCP inspection.
- Published guidance promises sample construction that no public input supports.
- Explicit trace-group aliases remain valid when a merge retains only provenance;
  an internal content fingerprint must not stand in for a missing `content` field.
- A real grouping column named `content` keeps its identity after projection;
  it must not collide with the internal content-fingerprint namespace.
- Large partition exports duplicate, omit or separate related observations. Verify
  every one of 100,000 rows and 20,000 groups across four output roles.

The last two are catalogue capability audits: record supported native-client
workarounds and missing primitives, rather than counting a missing capability as
implemented merely because its limitation is reproducible.

## Expanded Workshop-only acceptance

The user clarified that “whole MCP surface” means the Data Workshop surface,
not paid model workflows. Test the 16 Workshop tools and three shared discovery /
job tools below. A schema-validation response is not a successful workflow.

Additional failure cases, recorded before implementing any fixes:

- Concurrent first submissions duplicate a recipe, run, import, or published cell.
- Simultaneous different requests overwrite work; cancellation races publish anyway.
- Projection, simultaneous rename, typed equality, nulls, nested JSON, Unicode,
  long fields, and heterogeneous records corrupt content or lineage.
- Invalid conversation values, conflicting names, and invalid or partly valid
  parent arrays publish partial data or return unexplained internal errors.
- Merged/split rows lose any parent; caller-authored evidence impersonates review.
- Read-only, account and foreign-project credentials reach a Workshop mutation,
  artifact, frozen source, run receipt, resource, or exported file incorrectly.
- Full cell/run/recipe/dataset pagination omits or duplicates real records.
- Exploration reports count only a sample; cached profiles or paginated strata
  disagree with the source. Derivation mutates its frozen parent.
- Partition output loses duplicate observations, leaks groups/content between roles,
  ignores holdouts, or changes on retry; failed construction cannot recover.
- Trace selection/filtering/split and LLM-call split include other-project records.
- The published upload/export resources or prompts cannot complete their workflow.

Each live case must use HTTP MCP, actual database/file persistence and actual
Celery workers. Fixture setup and explicitly labelled fault injection may use ORM;
neither mocked workers nor fabricated job success count as live verification.

## Failure cases recorded before changes

- Installed guidance or a fresh MCP connection advertises removed agent tools.
- Project/account/read-only credentials cross their authorization boundary.
- Invalid arguments, SQL, recipes or row values become an internal server error.
- Retried or simultaneous requests publish duplicate versions.
- A stale source/artifact or changed request key is accepted.
- Empty output, missing parents, cancellation or expiration changes readable data.
- A cancelled landing's delayed delivery steals a newer attachment operation.
- Native target distributions, nested values, Unicode or parent lineage change.
- More than 100 recipes/runs become undiscoverable without an explicit boundary.
- Large uploads, transformations, imports, paging or exports lose rows, truncate
  silently, exceed bounded response sizes or stall the job-inspection surface.
- Source profiles, derived chains, train/eval partitions, trace and LLM-call
  sources fail with actual background workers.
- An external semantic transformation cannot publish a source-bound cell without
  delegating reasoning to a platform model.

## Coverage and execution

The live replay will use disposable local projects and temporary credentials;
credentials are never printed. It will discover and validate the complete MCP
manifest, test invalid input on every tool without invoking paid operations, and
exercise all Workshop tools plus discovery, job/resource reads and file transport.
Negative cases pass only when rejected safely and prior versions remain readable.

Data sizes: singleton, inline import limit (2,000), over-limit (2,001), 100,000 and
1,000,000 rows. Files use the existing CLI upload/export implementation, while
transformations and publication use real HTTP MCP requests and Celery dispatch.
Injected failures and races that need deterministic scheduling belong in the
isolated E2E suite, not mutations of the user's existing projects.

Record exact commands, fixture sizes, coverage, observed failures and final results
here after running. A passing finite matrix is not proof of every possible input.

## Verified repairs

Regressions were written and observed failing before these corrections:

- Read-only SQL validation now returns `query_invalid`, not `internal_error`.
- LLM-call datasets satisfy the MCP source-kind contract during inspect/list.
- Cancelled initial source deliveries cannot claim a replacement attachment.
- Recipes and run receipts page beyond 100 records through both REST and MCP.
- Cancelled split members release their unclaimed partners; paired claims are atomic.
- SQL returns native decision objects and JSON expressions as typed values, while
  JSON-looking text remains text. Query-to-import preserves the original values.

Cancellation and expiration during output construction were also exercised.
Neither can publish after cancellation, overwrite a newer queued operation, or
duplicate a version on redelivery. These cases already passed.

Two test-runner assumptions were corrected without changing product behavior:
oversized HTTP bodies correctly return 413 before tool dispatch, and completed
partition plans correctly reject retry. Recovery is checked by explicitly injecting
a failed state into the disposable partition fixture, then invoking the real retry.

## Repeatable commands

Run from the original checkout. Requirements: the existing Compose API, batch
workers, PostgreSQL and Redis; the migrated local database; repository Python
test dependencies; Bun for frontend checks. No model-provider credentials or paid
provider execution are needed for the acceptance replay.

The API image contains an older installed SDK, so use an isolated copy of the
current checkout's SDK for the replay. Do not overwrite its installed package.

```sh
acceptance_sdk_dir=$(docker compose exec -T api mktemp -d /tmp/workshop-acceptance-sdk.XXXXXX)
docker compose cp overmind/overmind "api:${acceptance_sdk_dir}/overmind"
docker compose cp tests/fixtures/documents "api:${acceptance_sdk_dir}/documents"
docker compose exec -T -e PYTHONPATH="$acceptance_sdk_dir" -e DO_NOT_TRACK=1 \
  -e WORKSHOP_ACCEPTANCE_DOCUMENTS="$acceptance_sdk_dir/documents" \
  api python manage.py shell < tests/evidence/workshop_mcp_acceptance.py
```

Default fixture sizes are 100,000 and 1,000,000 rows. Set
`WORKSHOP_ACCEPTANCE_ROWS=1000` on the container command for a smaller replay.
The full replay requires the document-fixture environment variable above. A small
replay explicitly reports live large-worker cancellation as not exercised; it is
not a substitute for the default full run. To rerun a changed case, set
`WORKSHOP_ACCEPTANCE_CASES` to its exact label (comma-separated for several).
Cases using `self.small`, `self.large_dataset`, or partition fixtures require their
setup cases too. Targeted summaries show missing tools; they do not claim full coverage.
The same runner also checks singleton sources, 2,000-row imports, 2,001-row
rejection, oversized bodies, typed decisions and five consecutive readable cells.
It removes only its disposable projects, temporary credentials and owned dataset,
exploration and partition artifacts after jobs become terminal. If work remains
active, it reports the exact fixture project IDs instead of deleting active data.

Fresh official MCP transport verification uses the existing project configuration
and never prints its credential:

```sh
.venv/bin/python tests/evidence/workshop_connection_replay.py
UV_NO_SYNC=1 uv run pytest tests/test_workshop_mcp_acceptance.py -q
UV_NO_SYNC=1 uv run pytest tests/ -n 4 --dist worksteal -q -ra
UV_NO_SYNC=1 make -C overmind test WORKERS=4
UV_NO_SYNC=1 make -C overmind lint-check
cd frontend
bun run typecheck
bun run lint
bun run test
```

## Previous acceptance run — superseded by the expanded replay

- Official MCP SDK Streamable HTTP initialization, resource read and authenticated
  tool call passed: contract 3.0.0, 61 tools, five new Workshop tools, no retired
  Workshop tools.
- Project and installed personal-plugin guidance were refreshed from the current
  bundled skills. No connection credentials or endpoint were changed.
- All 19 Workshop/support tools were successfully called through HTTP MCP.
  All 61 catalogue input schemas and invalid-argument paths passed.
- The small final replay passed all seven scenarios, including typed soft targets,
  weights, duplicate observations, blank states, Unicode and a five-version cell
  chain followed by a source append.
- Large replay: 100,000 sources → 50,000 transformed → 50,000 imported; 32,944,450
  export bytes. The million-row replay: 1,000,000 sources → 500,000 transformed →
  500,000 imported; 333,944,450 export bytes. Aggregate totals and original row
  counts matched; eight concurrent retries returned the same receipt at each size.
  The final post-fix replay passed **8/8 scenarios**: 28.875 seconds for the
  100,000-row workflow and 284.183 seconds for the million-row workflow. The
  largest MCP response was 63,031 bytes; bulk rows travelled through file transport.
- Backend: **4,043 passed, 15 skipped**. Skips are optional Torch/Transformers,
  Baseten/Truss and live Modal NER dependencies, plus the isolated Redis-lock test
  whose broker is deliberately in-memory. The equivalent real Redis timeout/release
  probe passed against Compose: seven-second lease, released afterward.
- Frontend: **854 passed** in 113 files; typecheck, lint, design, contrast and
  controls checks passed.
- SDK: **595 passed**; lint and formatting passed.
- API client regeneration completed. Repository hooks, Ruff and diff checks passed.
- Existing unrelated development warnings remain: short test-only JWT signing key,
  synchronous consumption of one streaming response, and local Modal test notices.
- This replay’s projects, accounts and temporary SDK copy were removed. All 126
  pre-existing datasets were preserved, including older fixture accounts belonging
  to that existing data. The application and all Compose workers remain running.
- Migration numbering 0013–0015 was deliberately not changed. No commits or pushes.

Logs: `/private/tmp/workshop-backend-verified.log`,
`/private/tmp/workshop-frontend-final.log`, `/private/tmp/workshop-sdk-final.log`,
`/private/tmp/workshop-connection-verified.log`,
`/private/tmp/workshop-live-acceptance-final-json.log`,
`/private/tmp/workshop-live-acceptance-verified.log`, and
`/private/tmp/workshop-precommit-final.log`. The final evidence-script hooks also
passed in `/private/tmp/workshop-evidence-hooks-green.log`.

These results are 100% of the executed acceptance cases, not a proof of every
possible input or paid training/inference provider workflow. The current chat's
already-loaded tool definitions remain cached; the fresh MCP connection is verified.
Restart the Overmind MCP connection or open a fresh chat to load the new catalogue.

## Expanded Workshop coverage

Final combined live replay: **20/20 passed**, no Workshop scenario skipped and
no missing tools. All 16 Workshop tools and three shared support tools completed
real workflows. [Machine-readable results](workshop-mcp-results.json) preserve
each case, measured duration, successful/rejected call counts, resource reads,
cleanup result and the exact replay-script checksum.

- 100,000 sources → 50,000 transformed → 50,000 imported: 41.425 seconds,
  32,944,450 export bytes.
- 1,000,000 sources → 500,000 transformed → 500,000 imported: 289.753 seconds,
  333,944,450 export bytes. Exact aggregate totals and original counts matched.
- 24 simultaneous first requests produced one recipe and two output versions.
- 105 executed recipes/runs and all 106 cells were recovered through pagination.
- A running 500,000-row operation was cancelled without an output cell; a new
  request completed. This live cancellation/recovery case took 86.919 seconds.
- All six additional tabular formats and eight document/image fixtures passed.
- Zero billable-usage records. This replay's two projects and user were removed;
  all 126 pre-existing datasets remained.

Full live output: `/private/tmp/workshop-complete-live-final.log`.
This is 100% of the explicit acceptance matrix, not a claim that finite testing
proves correctness for every possible input. Injected partition interruption and
isolated cancellation/lease tests are labelled separately from live worker checks.

Every row below requires successful live behavior, in addition to rejecting
invalid arguments. All tools validate identical structured and JSON-text output.
All 12 Workshop mutations are denied with read-only keys; all 18 project-scoped
Workshop/support operations reject an unauthorized project. The account-level
`list_projects` tool returns only authorized projects.

| Tool                            | Live outcome checked                                                                                                                                                                       |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `start_dataset`                 | Written brief survives later file attachment; purpose is explicit.                                                                                                                         |
| `list_datasets`                 | Paged results reconstruct every fixture dataset without duplicates.                                                                                                                        |
| `inspect_dataset`               | Active IDs/fingerprints, readability, frozen versions and cursor pagination over 106 real cells.                                                                                           |
| `query_dataset`                 | Exact nested values, Unicode and long text; 205-row paged reconstruction; CTEs, historical UUID/version selectors, aggregate counts/sums; unsafe SQL rejected.                             |
| `inspect_dataset_workbench`     | All 105 recipes and 105 real terminal run receipts remain accessible across pages.                                                                                                         |
| `save_dataset_pipeline`         | All four operations, immutable recipe conflicts, eight concurrent first submissions produce one recipe.                                                                                    |
| `run_dataset_pipeline`          | Four consecutive cell transformations, typed comparisons, simultaneous rename, failure atomicity, eight concurrent first submissions, large streaming execution.                           |
| `import_dataset_version`        | Inline and large artifacts, 2,000-row boundary, invalid/mixed parent arrays, all-parent merges and splits, eight concurrent first submissions, retained artifacts, spoofed review removed. |
| `update_dataset`                | Name/intent/default changes; select/reset active; foreign cells rejected; frozen metadata cannot change; combined invalid changes roll back atomically.                                    |
| `cancel_dataset`                | Observe a real worker running, reject conflicting edits, cancel publication, retain cancelled receipt on retry, complete a new request successfully.                                       |
| `create_dataset_from_traces`    | Explicit IDs, filtered/limited selection, disjoint train/eval outputs, unavailable traces, bad filters and oversized split names.                                                          |
| `create_dataset_from_llm_calls` | Actual captured span fixtures land; stable train/eval split with exact union; aware and naive time windows; invalid timestamps safely rejected.                                            |
| `explore_dataset`               | Whole-source 1,000-row census, 125 strata across resource pages, exact allocations, infeasible requests, cache reuse, key conflicts and source freeze.                                     |
| `derive_dataset`                | Separate 1,000-row chain; filtering it leaves eight rows while the original source stays unchanged.                                                                                        |
| `create_data_partition`         | All four roles; every duplicate observation retained; groups disjoint; explicit holdout respected; intents correct; invalid fractions/conflicts rejected.                                  |
| `retry_data_partition`          | Live retry retains exact member IDs and assignment checksum after a labelled terminal-state fault injection. Completed plans reject retry.                                                 |
| `get_job`                       | Actual landing, pipeline, exploration and partition state transitions; completed, failed and cancelled outcomes.                                                                           |
| `list_model_workflows`          | Workshop partition discovery and project isolation. No model job is launched.                                                                                                              |
| `list_projects`                 | Project-key and account discovery without access to the foreign project.                                                                                                                   |

File workflows execute the current SDK upload/export implementation over HTTP,
then use MCP and real workers. CSV, TSV, JSON, NDJSON, gzipped JSONL and Parquet
round-trip through transformation and export. JSONL is also used at large scale.
Duplicate CSV headers, malformed JSONL and empty sources fail safely. TXT,
Markdown, DOCX, scanned/mixed PDFs, PNG, JPEG and WebP retain original bytes and
row evidence through projection. OCR runs locally, not through a model provider.

Resources checked: interface, upload/export handoffs, dataset, pipeline run,
exploration, paginated strata and partition; foreign resource and export access
are denied. Both upload and export prompts render. Removed Workshop-agent tools
reject calls and are absent from a fresh official MCP SDK connection.

### Repairs found by the expanded live replay

- Trace split names exceeding the suffix-adjusted limit returned an internal
  database error. The shared creation service now rejects them before creation.
- Bounded OCR metadata shortened source IDs/checksums, making the advertised
  original-file download return 404. Both bounding passes now preserve identity
  fields intact. The actual stored bytes were not damaged.
- Invalid calendar dates reached an uncaught parser exception. LLM-call source
  selection now returns its normal validation error; naive ISO timestamps use
  the standard UTC timezone instead of a removed Django constant.
- Two installed-guide passages still described server-side agent preparation.
  The bundled guide and project-installed copy now describe native-agent authoring.

The source-download regression was independently reproduced through the existing
API/MCP integration test before the fix. The live script contains the split-name
and timestamp regressions. No UI redesign or migration renumbering was performed.

### Regression and connection results for this pass

- Backend: 4,044 passed, 15 optional-environment skips. The first sandboxed run
  passed 4,042 but could not bind the localhost HTTP server for two analytics
  tests. Those two passed when rerun with local-server permission. No product
  test failure remains. The skipped GPU/provider dependencies are not counted
  as Workshop E2E coverage.
- Fresh official MCP SDK connection: version 3.0.0, 61 catalogue tools, all five
  replacement Workshop tools present, zero retired tools; authenticated call
  and resource read passed. No non-Workshop provider workflow was executed.
- Ruff formatting/lint, repository hooks and diff checks passed. Frontend and
  standalone SDK suites were not rerun in this pass: no frontend or SDK runtime
  code changed. File transfer was verified live using the current SDK source.
  OpenAPI regeneration was not needed: these repairs change validation and
  metadata values, not the REST schema.

Logs: `/private/tmp/workshop-complete-backend.log`,
`/private/tmp/workshop-analytics-recheck.log`,
`/private/tmp/workshop-regressions-green.log`,
`/private/tmp/workshop-complete-connection.log`,
`/private/tmp/workshop-complete-hooks.log`.

Failure evidence and focused repairs are recorded in
`/private/tmp/workshop-expanded-documents.log`,
`/private/tmp/workshop-source-evidence-red.log`,
`/private/tmp/workshop-source-regressions-red.log`,
`/private/tmp/workshop-repaired-sources.log`,
`/private/tmp/workshop-final-targeted.log`,
`/private/tmp/workshop-time-window-red.log`, and
`/private/tmp/workshop-time-window-green.log`.
