# Workshop regression and catalogue audit

For subsequent source-inventory and progress fixes, see
[the full performance review](workshop-full-performance-review.md). This document
preserves the earlier observations rather than rewriting them as later successes.

Scope: the native-agent Data Workshop, on the existing branch and deployment.
No platform-agent restoration, UI redesign, training launch, evaluation launch,
hosted inference or paid provider calls. Existing user data is outside the fixtures.

## Reproduction

Use `tests/evidence/workshop_mcp_acceptance.py` with the existing API, PostgreSQL,
Redis and Celery batch worker, plus the current SDK and synthetic document fixtures.
The exact setup command is in `workshop-mcp-acceptance.md`. The default run includes
100,000- and 1,000,000-row files, all sixteen Workshop tools, three shared discovery
and job tools, and document/OCR round trips.

To isolate this continuation, set `WORKSHOP_ACCEPTANCE_CASES` to:

```text
partition_maximum_name_lengths,partition_declared_and_projected_groups,partition_projected_group_aliases,partition_content_column_groups,account_key_mutations_and_resource_links,concurrent_partition_keys_across_sources,catalogue_sampling_and_source_inventory,large_partition_exact_membership
```

Additional failure modes were recorded before their fixes in
`workshop-mcp-acceptance.md`. The projected-alias extension checks that an explicit
`source_trace_id` group still resolves to preserved `trace_id` lineage after its
column is removed, while the built-in content fingerprint does not falsely
satisfy a nonexistent user column named `content`.

The scale extension exports all four members of a 100,000-row partition and
checks exact content, unique membership, source-cell attribution and disjoint
ownership of every one of 20,000 groups.

## Reproduced regressions

| Failure                                               | Evidence                                                                                                      | Correction                                                                       |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Valid 255-character names fail partition construction | Live job resource reported `value too long for type character varying(255)`                                   | Reserve role-suffix space in generated member names; preserve the full plan name |
| Long names make `get_job` unreadable                  | Live tool returned `invalid_output`; its summary exceeded the 240-character contract                          | Bound only the summary label and preserve status and full structured identity    |
| A misspelled grouping field silently succeeds         | `group_by=["cohrot"]` completed and claimed no group overlap                                                  | Reject explicit fields absent from rows and preserved lineage                    |
| Same project request key races across two sources     | Two real concurrent HTTP requests produced a PostgreSQL unique-constraint error, surfaced as `internal_error` | Lock the project before resolving its request key, consistently with exploration |

The initial grouping fix was additionally tested and refined before final
verification: merged rows retain canonical trace-group aliases in provenance,
and internal content fingerprints do not satisfy a nonexistent `content` field.
A real declared `content` column now uses a separate group identity, so projection
does not erase its grouping declaration. Its regression was observed failing
before the namespace correction and rerun with the related lineage workflows.

One replay assertion was corrected without changing the product: freezing the
imported cell changes its derived display version from `1.1` to `2.0`. Its UUID,
fingerprint and row count remain identical. The final full replay had already
loaded the older assertion; its corrected scenario was rerun separately and
passed. The machine-readable record retains both results rather than rewriting
the failed assertion as a pass.

The account-key test completes upload, saved recipe, execution, polling, rename,
query and returned-resource reads in two authorized projects. It also rejects
cross-project dataset references despite membership in both projects.

## Catalogue gaps

These are missing or limited capabilities, not passing implementations. All
recommendations keep reasoning and arbitrary code execution in the native agent.

| Priority | Gap                                                              | Evidence and current workaround                                                                                                                                                        | Recommended change                                                                                                                                                                            |
| -------- | ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| High     | Source inventory cannot be fully discovered through MCP          | Twelve uploaded files yield `sources_total=12` but only ten entries; no source cursor exists. REST returns all twelve.                                                                 | Add source paging to `inspect_dataset`, including stable original-file references. No separate inspection tool is needed.                                                                     |
| High     | External execution has no structured reproducibility record      | `ImportInput` stores a prose `provenance`, source identity and output/artifact identity, not a command, code revision/hash, environment or seed contract. Save those externally today. | Extend import receipts with a versioned native-execution manifest; do not execute the code on the platform.                                                                                   |
| High     | Native-agent quality findings have no structured write/read path | New runs publish `semantic_quality="unmeasured"`; there is no assessment input in Workshop mutation contracts. Prose attribution is not a row-level assessment.                        | Record cell-bound, attributable checks, sampled/whole-source coverage, findings and limitations. Distinguish submitted findings from independently verified truth.                            |
| Medium   | Imports cannot bind parents from several existing versions       | One source cell must belong to the destination dataset; parent references are integer identities in that single source. Artifact identity pins output, not another parent source.      | Extend imports with an explicit source set and cell-qualified parents. Today, combine inputs into one source chain first and retain external evidence records.                                |
| Medium   | Sampling feasibility is not a publishable sampling recipe        | `explore_dataset` measures allocations; `derive_dataset` copies every row and rejects a `sampling` argument. Live native-client selection/import preserved 25 rows across five strata. | Either expose deterministic sampling as a saved pipeline operation, or explicitly retain the native selection/import workflow with a replay manifest. The misleading skill text is corrected. |
| Medium   | No cancellation for exploration or partition construction        | `cancel_dataset` controls landing and pipeline publication only; workflow next actions offer read/retry but no cancellation for these job kinds.                                       | Add durable cancellation at safe publication boundaries if these long jobs need user control. Do not claim remote provider cancellation.                                                      |

Additional hardening gap, identified by code inspection: `query_dataset` caps
rows at 100 and uses a 512 MB, two-thread query connection, but has no explicit
statement deadline or response-byte ceiling. Oversized individual values are
outside this replay's capacity proof. Add explicit budgets, a structured limit
error and CLI export guidance rather than silently truncating values.

Four built-in deterministic transformation operations are not themselves a gap:
arbitrary transformations and generation deliberately belong to the native agent.
The important missing platform responsibilities are complete discovery, durable
evidence, reproducibility and lifecycle control—not an embedded reasoning agent.

## Product experience: utility, delight and workflow clarity

Technical success is necessary but does not establish a good native-agent
experience. Include useful workflow verbosity in the experience assessment:
whether the user can understand what is happening, what changed, what remains
uncertain and what they can do next. Longer responses or more progress messages
are not success measures.

### Experience contract

The platform supplies factual, bounded state and receipts. The native coding
agent decides how to explain them to the user; no platform reasoning agent or
cell-layout redesign is required. A tool response is evidence available to the
agent, not proof that the user saw or understood it.

- On submission: distinguish accepted, queued and running from completed; return
  the durable job and source/cell references.
- During work: expose the current stage, measured counts and meaningful state
  changes. Mark totals or remaining time unknown when unmeasured. The agent
  should acknowledge long waits without repeating unchanged poll payloads.
- After each logical transformation: summarize actual row/schema changes,
  identify the resulting cell and preserve access to the source. Keep detailed
  impact and lineage available through resource links rather than repeating rows.
- At completion: identify the usable result and remaining limitations. Separate
  successful execution from compatibility, coverage and semantic quality;
  `semantic_quality="unmeasured"` must never become "quality verified".
- On failure or interruption: state what was saved, whether work is still
  running and which recovery actions are safe. Do not imply cancellation or
  retry support where the catalogue does not provide it.

Example agent narration, conditional on matching receipts: "500,000 rows saved
in a new cell. Original source retained. Semantic quality has not been assessed."
This is a proposed communication pattern, not a new emitted product message.

### Assessment and measurement

| Dimension        | What to assess                                                                               | Evidence or proposed measure                                                                                                                                                                                                   |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Utility          | Does the requested task produce a usable, traceable result?                                  | User-confirmed task outcome; time from an explicit workflow start to the first usable cell; immutable result and source references. A successful tool call or published cell alone does not prove the user's task is complete. |
| Workflow clarity | Can the user distinguish waiting, progress, completion and limitations?                      | Review the actual native-client transcript against job receipts; check stage changes, truthful counts, stale updates and clear completion. Server response delivery alone does not prove narration was displayed.              |
| Recovery effort  | Can a failed or interrupted task continue without losing work or repeating it unnecessarily? | Time to a verified recovered result; duplicate submissions; corrective tool calls; user intervention. Separate normal polling from failed attempts.                                                                            |
| Product delight  | Does the workflow feel clear, controlled and worth using again?                              | Direct user feedback on clarity, confidence and effort after a completed task. Repeat successful use and fewer interruptions are supporting proxies, not proof of delight.                                                     |
| Verbosity cost   | Does extra explanation help without overwhelming the user or agent?                          | Response bytes, repeated information and context consumed alongside task success and feedback. Compare concise stage summaries with expanded detail; do not optimize word count alone.                                         |

For an instrumented workflow, define a stable correlation identity across
submissions, background work and reconnects. Deduplicate terminal outcomes using
durable receipts. Record explicit failures and cancellations separately from
incomplete observation; lack of another MCP call does not prove abandonment.
Segment results by operation, native client, dataset size and outcome. Exclude
regression fixtures from product-usage reporting. Capture bounded metadata and
counts, not additional source rows, prompts, credentials or raw local commands.

### Evidence and remaining gaps

Code inspection confirms that `get_job` exposes progress and dataset-run next
actions, and the upload prompt instructs the agent to poll, inspect impact and
verify the chosen cell. The existing PostHog integration captures MCP request
events; its integration tests cover attribution, session correlation and an
analytics-ingest failure that must not break tool use. Those tests were inspected,
not rerun for this documentation addition.

Request telemetry is not a durable end-to-end workflow outcome or a measurement
of user-visible narration. The current acceptance replay proves the technical
scenarios below, not clarity or delight. An experience pass still needs actual
native-client sessions covering a fast transformation, the million-row path,
validation failure and recovery, interruption/reconnect, and unmeasured quality.
Review the visible messages against receipts and collect feedback before scoring
delight or selecting a default verbosity level.

This section adds an assessment requirement and proposed measurement definitions.
It does not add runtime narration, new analytics events, a dashboard, or a passing
experience-test claim.

The subsequent [PDF and experience audit](workshop-pdf-experience.md) records
implementation status and twenty additional functional scenarios across focused
runs. It confirms that file progress is stored but omitted from MCP job output,
scanned-document metadata loses key facts under truncation, and source inventory
still stops at ten entries. These experience requirements are not fully implemented;
functional extraction success does not close them.

## Verification record

Final result: **28 distinct scenarios verified** across the full replay and final
focused reruns, covering all 16 Workshop tools and three supporting tools.
The complete replay recorded 26/27 passes with the display-label assertion
described above; the final focused run recorded 13/13 passes, including its
correction and the added real-`content`-column regression. This is an aggregate
of latest results, not a claim of a single 28/28 invocation.

- 1,000,000 source rows → 500,000 transformed/imported rows; 333,944,450 export
  bytes; 286.142 seconds in the final full replay.
- 100,000 partition rows individually verified from exports; all 20,000 groups
  stayed within one role; final focused check took 19.003 seconds.
- 2,080 successful calls in the full replay and 299 in the final focused run.
- Fresh official MCP SDK connection: contract 3.0.0, 61 total tools, five new
  Workshop tools present and all three retired platform-agent tools absent.
- No billable usage records. Disposable projects/users were removed, preserving
  the 126 pre-existing datasets. Intermediate cleanup counts include other
  concurrently running disposable probes, not newly retained user datasets.

The continuation's complete live replay and final targeted results are recorded in
`workshop-regression-results.json`. Logs are local under
`/private/tmp/workshop-regression-*.log`; failing runs remain separately under
`/private/tmp/workshop-adversarial-red.log`,
`/private/tmp/workshop-partition-names-red.log` and
`/private/tmp/workshop-group-alias-red.log`.

Backend command:

```sh
.venv/bin/pytest tests/ -n 4 --dist worksteal -q -ra
```

Final backend run: 4,044 passed, 15 optional-environment skips, 39.84 seconds.
Repository format/validation hooks passed on the production changes and replay.
A finite
passing matrix is not proof of every input, nor does it close the catalogue gaps
above. No UI or generated REST schema changed in this continuation.
