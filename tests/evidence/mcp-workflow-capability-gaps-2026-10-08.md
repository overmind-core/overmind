# Native-agent workflow capability gap analysis

Review: 2026-10-08 PDT. This is a read-only product/source assessment, not a new
end-to-end test run or an implementation of the recommendations below. No new
paid work, browser operation, or product-code change was performed for this review.

## Conclusion

Overmind can perform substantial parts of the requested workflow, but does not
yet provide an operationally complete native-agent experience. The most serious
missing capabilities are reliable observation, request reconciliation, recovery,
spending control, and proof that a trained artifact is the artifact being served.
Adding a platform planning agent would not solve these problems. The platform
needs to supply trustworthy facts and controls; the native agent supplies judgment,
code and explanation.

## Evidence and interpretation

- Connected MCP interface: contract 3.0.0, 61 tools, catalogue fingerprint
  `72e15fb7ccbc855f217e3d417599a6bcf1165cb994a22b7e7f63a1e76a49c1ae`.
- The [real preparation/training/serving audit](mcp-prep-training-ux-audit-2026-10-08.md)
  records 243 calls across 25 named operations plus resource reads. It includes
  two small real training jobs, held-out inference, activation and rollback.
- The earlier [Workshop performance review](workshop-full-performance-review.md)
  records a 49/49 live scenario replay, all 16 Workshop tools in that matrix,
  21 PDF cases and workflows up to one million rows. It also records an unresolved
  full-backend import timing failure; it is not a whole-product readiness claim.
- The [local handoff repair](workshop-local-handoff.md) records the original
  installed-CLI credential failure and successful repository-independent
  upload/transform/export verification in two projects after repair.
- Current contracts and services were inspected to distinguish a missing MCP
  operation from an existing capability, a previous defect or an untested path.

The financial-services project used for the model audit is
`e18b29b5-915d-45a7-80cd-77ffe6559205`. No new model execution was used to diagnose
connectivity. Historical results below were read from the retained evidence,
not rerun for this analysis.

## Release-critical gaps demonstrated by the tasks

### 1. A timed-out inference cannot be reconciled by exact request identity

**Evidence:** the cold original-model inference timed out at the client after
120 seconds. It returned no durable request receipt. Aggregate metrics did not
establish whether that particular request completed. The connected
`run_inference` has neither a request key nor an asynchronous handle;
`get_job` has no inference-request kind.

**Impact:** I could neither recover the answer nor safely establish whether
retrying would repeat paid work. I deliberately did not replay the unknown request.

**Required capability:** persist a request before dispatch, bind an idempotency
key to its exact payload, return a handle within the client timeout, and expose
status, result, provider acknowledgement and usage by that handle. Unknown
submission must remain unknown until reconciled, not become an automatic retry.

**Acceptance:** disconnect before and after provider acknowledgement; reconnect
from a fresh session and recover the same operation/result without a duplicate
provider submission. Changed input under the same key must conflict.

### 2. Provider progress is too coarse to explain long waits

**Evidence:** cold rollback took 13 minutes 16 seconds. Preparation was observed
with zero committed rows until its final update. Deployment facts and activation
deadlines are now exposed, but pending provider calls still hide much of the work.
Worker restore measurements are largely published after startup completes.

**Impact:** I could report that work was pending, but could not reliably distinguish
queueing, allocation, restore, model loading, health verification and a true stall.

**Required capability:** a durable, project-scoped provider event timeline with
operation/attempt identity, measured units, stage start, last observation,
heartbeat and last forward progress as separate facts. Include known deadlines,
recovery options and which model still receives traffic. Expose missing telemetry
explicitly. Passive reads must not wake a worker or leak other tenants' adapters.

**Acceptance:** cold start, warm start, rollback, provider failure and worker
restart each produce an attributable timeline. A heartbeat-only stalled job is
not presented as progressing. Do not fabricate percentages or remaining times.

### 3. Lifecycle controls do not cover the work that MCP can start

**Evidence:** the catalogue has no training/preparation cancellation, deployment
stop, or dataset archive/delete operation. Dataset cancellation raced a completed
3.56-second run and still said only “Cancellation requested.” An interrupted
partition remained apparently running while its recovery lease expired; that
interruption was caused by development-worker reload, not a clean load test.

**Impact:** recovery, disposal of audit artifacts and control of paid resources
could not be completed solely through the requested MCP workflow.

**Required capability:** exact-operation cancellation/reconciliation, dependency-
aware recoverable cleanup and deployment stop controls. Return distinct outcomes
for already completed, publication prevented, remote stop pending, remotely
stopped and cancellation unsupported. Report lease/recovery timing. Native
evaluation pause/resume already exists; extend coverage instead of duplicating it.

**Acceptance:** exercise cancellation before dispatch, during execution and after
completion; interrupt a worker; attempt cleanup of referenced cells. Preserve
sources and consumers, never claim an unconfirmed remote stop, and do not silently
stop a deployment serving active application traffic.

### 4. The workflow cannot demonstrate an all-in spending ceiling

**Evidence:** recorded new-job preparation/training and 36 completed inference
response estimates total $0.25756035. That excludes startup/idle, rollback warm-up,
other preparation and unreported components. The user's $100 authorization was
not an enforceable all-in provider cap.

**Impact:** I had to use small bounded jobs and leave headroom rather than prove
the complete workflow would remain below the authorized total.

**Required capability:** a shared workflow budget covering reservations, accrued
usage, estimates and unreported components, with admission checks and stop controls
across stages. Separate platform ledger coverage from eventual provider invoices.
Where provider reporting lags or cancellation is unavailable, disclose that a hard
cap cannot be guaranteed and bound exposure before dispatch.

**Acceptance:** concurrent child jobs cannot each spend the same remaining budget;
startup and idle costs are accounted for or explicitly uncovered; retries do not
double-reserve; an exhausted budget prevents further submissions.

### 5. Chat training lacks sufficient artifact-to-serving verification

**Evidence:** the second model reported development loss 0.003575, but 0/16
held-out responses and three exact development probes met the requested output
schema. Its MCP training record had null artifact identity, reload verification
and artifact inference contract. Provider metadata confirmed an adapter exists,
but did not prove its application to those requests. The root cause is unresolved.

**Impact:** I could establish a user-visible failure, but not prove checkpoint,
tokenizer/template and served-adapter parity from MCP facts.

**Required capability:** immutable checkpoint/base/tokenizer/template identities,
fresh-process reload checks and serving attestation linked to inference receipts.
Use existing evaluation machinery for a separate output-contract and quality
assessment; a healthy engine or low loss is not task success. Native decision
training already has reload-verification concepts; this gap is scoped to the chat
path, not every training workflow.

**Acceptance:** a deliberately wrong adapter/template is detected; the intended
artifact reproduces a pinned verification suite within declared numerical
tolerances. Keep development diagnostics separate from final held-out evaluation.

### 6. Data-first training can reach activation but fail application handoff

**Evidence:** `get_model_swap_prompt` failed because the training job had no
capability and its project had several. The tool accepts no explicit capability
selection. `run_inference` targets a deployment, not an application alias. No
application-authenticated alias request was verified in this audit.

**Impact:** “trained and responding” did not become a fully verified usable
application integration through the available handoff.

**Required capability:** structured endpoint/model/authentication-requirement
facts independent of capability association, with explicit capability selection
when an alias is wanted. Provide alias-routing diagnostics and an exact receipt
for actual application traffic. A platform probe must remain labelled a probe,
not evidence that a user's application is connected. The native agent can author
the application code from those facts; a platform-generated narrative is optional.

**Acceptance:** complete a data-first journey in projects with zero, one and
multiple capabilities, then verify an authorized application request resolves to
the intended model. Never expose credentials in receipts.

## Additional gaps affecting completion, scale and reproducibility

| Gap                                    | Evidence and impact                                                                                                                                                                                                          | Required surface and proof                                                                                                                                                                                                                                        |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Recover work without remembered IDs    | `list_model_workflows` omits standalone preparation, ordinary training, deployment and activation inventory. I relied on retained receipt IDs.                                                                               | Extend discovery with dataset/cell/request-key/state filters and exact dependency links. A fresh session must recover the in-flight workflow without resubmission.                                                                                                |
| Immutable activation history           | The same capability-level activation ID was reused across model switches. An old reference does not identify an immutable attempt.                                                                                           | Append activation-attempt receipts with previous/target selection and terminal outcome; verify two switches remain independently inspectable.                                                                                                                     |
| Structured external execution evidence | Import accepts a provenance string, not a structured code/environment/seed manifest or cell-bound assessment contract. I kept additional verification evidence outside the product.                                          | Store author attribution, code/artifact hashes, environment, parameters, checks and their coverage. Keep agent assertions separate from platform verification and semantic quality claims.                                                                        |
| Multi-source import lineage            | The current import contract binds one `source_cell` and fingerprint. It cannot express a join across independently versioned parent cells. This was identified by contract inspection, not a failed join in the model audit. | Explicitly pin every input cell/fingerprint and namespace parent-row references. Test many-to-many joins, aggregates and one changed parent; retain all observations and declared groups.                                                                         |
| Bounded, complete inspection           | Audit responses repeatedly included large examples, clipped useful fields and contradictory next actions. Earlier load evidence identifies missing query byte/deadline controls.                                             | Compact snapshots, explicit detail reads, paginated event changes and truthful truncation metadata; byte/time bounds with an actionable recovery path. A clipped response must still offer access to complete authorized facts.                                   |
| State/action consistency               | Terminal experiments suggested preparing a forecast; deterministic lineage failures suggested retrying; ready deployment and warming worker states were not clearly distinguished.                                           | State-dependent actions and stable error classes. Separate artifact readiness, worker state, activation and application connection. This is a contract repair, not a need for more tools.                                                                         |
| Predictable large-import performance   | The earlier million-row workflow took about 720 seconds; a 20,000-transcript import repeatedly exceeded the existing full-suite timing budget. Shared-host contention limits causal conclusions.                             | Controlled stage profiling and an explicit resource/performance envelope; optimize only with full lineage/measurement checks intact. Repeat under declared concurrency without relaxing thresholds.                                                               |
| Workflow-level utility and feedback    | Existing MCP instrumentation measures requests; it does not establish user-confirmed task completion, clarity, effort or satisfaction.                                                                                       | Correlate operations into a privacy-conscious user workflow; measure time to usable result, unobserved wait, recovery effort and abandoned work. Collect optional direct feedback separately from success/latency proxies; never infer delight from HTTP success. |

## Deliberate boundaries and repaired failures

These should not be presented as missing platform intelligence:

- Native agents author arbitrary transformations, samples and semantic checks.
  A limited deterministic pipeline catalogue is intentional; do not restore a
  remote arbitrary-Python runner or a Workshop planning agent to fill it out.
- Local bytes use the supported upload/export CLI. Strict MCP-only testing cannot
  ingest fresh local PDFs, but that is a transport boundary, not missing PDF
  support. The installed CLI credential handoff and exact-cell export receipt were
  repaired and verified. A remaining improvement is a read-only connection check
  that confirms MCP/CLI endpoint, account and project alignment without paid work.
- The initial agent uploaded a KYC projection and no saved brief. That is a source
  handoff failure, not evidence that the platform deleted an original raw file.
  The acceptance journey should retain the original input and brief before
  deriving training cells, and verify that receipt.
- Source paging, OCR facts, page/file progress and discoverable PDF limits were
  repaired in the earlier Workshop work. Do not repeat its pre-fix findings as
  current missing capabilities.
- The later audit repaired lineage collapse, explicit-context normalization,
  numeric token redaction, deployment progress omission, activation scheduling
  facts and context-error classification. Its targeted regression passed 277
  checks. Those repairs do not solve the release-critical gaps above or establish
  whole-product correctness; historical bad lineage cells were not rewritten.

The Overmind skills shaped this assessment by preserving the native-agent/CLI/MCP
boundaries and distinguishing technical readiness from semantic qualification.
No browser fallback or new platform agent is recommended.

## PDF and end-to-end coverage still required

The earlier final matrix already covered 21 PDF cases: native documents up to
2,000 pages, scanned/mixed documents, 100-file batches, an approximately 99-MiB
scanned fixture, invalid inputs and recovery. It preserved original bytes and
page evidence. Those fixtures are mostly simple text and synthetic raster pages;
passing them is not proof of complex-document understanding.

Remaining tests should include dense tables, multi-column reading order, rotated
pages, multilingual material, long scanned books, exact byte boundaries,
concurrent heavy batches and interruption/cancellation during OCR. Current
extraction uses English OCR and does not reconstruct visual table structure or
reading order: these are actual capability limits if the user's documents need
them. Lossy extraction must be visible, not presented as training-ready truth.

The final journey must run from a fresh projectless native-agent session using
the installed CLI for bytes and MCP for platform control: retained original and
brief → source inspection → authored transformation with evidence → pinned
partitions → preparation → bounded training → quality checks → activation →
application-authenticated request → rollback and safe cleanup. Include cold and
warm execution, timeouts and session reconnects. This is broader than the recent
MCP-only model audit; neither that audit nor the earlier Workshop matrix alone
proves the combined journey.

## Recommended order

First complete durable request receipts, provider-backed observation, recoverable
job discovery, lifecycle controls and budget accounting. Next close chat artifact
verification and the data-first application handoff. Then add structured external
execution/multi-source lineage and finish inspection, performance and experience
measurement work, with the acceptance journey above as the release bar.

Prefer extending shared services and existing MCP readers. Add narrowly scoped
missing lifecycle operations where necessary, not one tool per Modal internal
stage. Success means a native agent can complete or safely recover the user's
task from truthful platform facts without private backend access, browser rescue
or guessing whether a paid request is still running.
