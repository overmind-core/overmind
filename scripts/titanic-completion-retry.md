# Titanic completion retry

Started 6 November 2026 (America/Los_Angeles).

- Project: `1e3f3e92-b50d-4590-85ed-97921d132d3c` on `http://localhost:8000`.
- Dataset: `5a8df227-9982-4ef6-96c4-d9b27e97185b`.
- Pinned source: `637dcfac-dcd7-4642-8de0-679cbf9c4ab1`, fingerprint `f1a985c11befb35195b826c46acd222448ab9e9b1769bddf69eb0344244c680b`.
- Native task: `6ca52abc-5a33-4cfa-a057-2ba1027aa2a1`.
- Objective: “I want to train a decision model on this data”. No transformation recipe or target mapping was added to the native task.

## Infrastructure recovery

Docker Desktop was stopped. The existing local containers and Temporal service were restarted. The CLI connection check confirmed the local MCP and transfer endpoints, account and project; no staging or production endpoint was used.

Daytona DNS now resolves from the native worker. The existing sandbox `a6d65896-7e7b-458e-bcb5-2a2ee40edeb8` reports started, private, outbound network blocked and no auto-stop. No new workspace was created.

The previous task's bootstrap action was reconciled using its retained traceback: DNS failed during the sandbox lookup, before a runtime probe, directory creation or upload. Its unknown receipt was converted into an explicit pre-execution connection failure; no uncertain command was replayed.

The local Temporal development database then reported `AppendHistoryNodes: database disk image is malformed (11)`. A separate SQLite recovery passed integrity checking but recovered 18,929 of the original 18,932 history-node records, so it was not used as a complete historical recovery. The original database is retained in the Temporal volume as `/var/lib/temporal/development-corrupt-20261106.db` and copied to `/tmp/overmind-temporal-before-recovery.db` for diagnosis. Platform task/action receipts, datasets and Daytona files remain intact.

The old task `bc46971e-3248-42ef-bd29-7a3e5b8df91c` was cancelled through the platform lifecycle. Temporal termination itself failed against the corrupt store; this is not reported as a successful termination. After confirming the only active platform task was the new, undispatched retry with no actions, a fresh local Temporal store was initialised. Historical Temporal executions are archived rather than present in the new store. No paid training, evaluation or inference deployment was launched.

## Independent source checks

The pinned source export contains 891 passengers: 549 non-survivors and 342 survivors. Age is missing in 177 rows and deck in 688. `alive` exactly duplicates the survival outcome in all 891 records. `adult_male` and `alone` are strings: each contains 537 `True` and 354 `False` values. A truthiness conversion of these nonempty strings would corrupt the false values.

Output acceptance requires actual consumer validation, all source identities and labels preserved, leakage excluded from inputs, field-value comparisons with explicit treatment of missingness, retained executable code, successful preview/publication and checked partition membership if partitions are produced. These checks are independent of the native agent's own claims.

## Provider recovery

The new task reached source and workbench inspection, then OpenRouter returned HTTP 200 with a JSON error (`504`, upstream idle timeout) and no completion. The harness incorrectly classified the missing usage as an unresolved provider outcome. The saved response establishes a rejected request with no tool call. Recovery now recognises transient response-body errors without choices, preserves the complete error and unknown usage, and applies backoff before a new reasoning request. Transport interruptions without a definitive response remain blocked. The same task was resumed; no operator-authored transformation was inserted.

The focused native-agent suite passed: 40 tests in 10.89 seconds, including the observed HTTP-200/error-504 case and preservation of the block on uncertain responses. Ruff passed.

The actual Data Workshop journey passed (1 test, 59.92 seconds). An initial name filter selected no tests; an overly broad Make invocation was interrupted before completion, then the exact journey file was run.

Workspace bootstrap completed successfully. The native agent subsequently called command-status with sequence `31` instead of a command UUID. Django's invalid-UUID exception escaped the correction path, incorrectly producing an unknown action. Command ID validation now returns ordinary tool feedback; interrupted status reads can be settled on resume because they submit no commands. This is a harness recovery fix, not transformation assistance.

After the command-status fix, 41 focused tests passed in 12.37 seconds; lint and all applicable pre-commit checks passed. The previous exact Data Workshop journey passed before this narrow validation change.

OpenRouter's live endpoint catalogue listed only Parasail for Qwen3-Coder-Next. Repeated HTTP 429 receipts identified its upstream shared pool as the limit. No alternate model or provider was silently selected. Native notes now explicitly exclude both `survived` and `alive` from inputs and recognise false-like strings, but notes alone are not full-population checks.

Orientation feedback previously conflated an invalid citation with absence of source observations. It now names unsupported receipt IDs. The existing orientation regression covers a mix of successful source evidence and a failed citation; 41 tests passed in 137.64 seconds during local resource contention. File-read tools now specify character offsets/limits and return the next offset; model observations expose action completion state. These are general tool-contract corrections.

Concurrent hot reloads caused prolonged local API/worker startup. Celery processes were briefly paused to relieve contention and then unpaused after the API became responsive. The native task retained its receipts and was resumed with no semantic recipe injected.

## Native execution boundary

The fragmented trial was cancelled at action 74 after the user clarified that the native agent must work directly with colocated data and must never depend on MCP. It produced no transformation script or published training output; its recorded reasoning is not a successful preparation.

The native worker now has typed Python tools that invoke project-scoped domain services directly. MCP catalogue invocation, resource reads and synthetic MCP credentials were removed from the native runtime and Console task-start path. External MCP tools retain their existing role; shared pipeline request contracts and authoring documentation now live in the dataset service layer.

Before the first model turn, Temporal pins the selected source cell and stages its Parquet file, retained original bytes, hashes, capability context, runtime guide and consumer/publication contracts in the existing Daytona workspace. Every copied file is checksum-verified. A failed bootstrap prevents reasoning; subsequent bootstrap attempts reuse the pinned source even when the active dataset changes. The task begins with local `data/input.parquet`, `input.json`, `context.json` and documentation paths. No task-specific target mapping or transformation recipe is supplied.

Verification so far: the 41 existing native-agent tests passed; three additional tests passed for real Daytona SDK HTTP transfers, corruption rejection and native recipe preview/publication through the real pipeline runner. The actual test transformation preserved source identities and changed values from 3/7 to 6/14. Ruff and the applicable pre-commit hooks passed. A Data Workshop journey initially failed at Docker network creation; 17 unused parser-test networks were removed, preserving networks with attached containers, and the exact journey was restarted. Live Titanic output remains unqualified until the new runtime actually publishes and the source-to-output audit passes.

The changed-active-source recovery case also passed: after a corrupted copy, a retry staged the original pinned cell despite a newer active cell. Native retained-package file inspection was verified through the shared checksum-aware reader. The fresh native task is `33ad1f22-66ef-4a2b-a93e-fb1df5ff1708`, with the same minimal objective and source dataset. Its first start attempt failed during local MCP connection metadata discovery before task creation; retrying the same request key created one queued task once the local API was responsive.

The restarted journey reached the Console, then timed out waiting for the onboarding dialog. The captured browser state contained the expected dialog and reported no page/server errors. The journey was retried after competing focused tests completed; no frontend or timeout changes were made.

## Fresh native trial result

Automatic bootstrap succeeded before the first model request. It verified the 45,803-byte Parquet source (`f1a985c11befb35195b826c46acd222448ab9e9b1769bddf69eb0344244c680b`) and 57,018-byte original CSV (`81787d320d7f7b03df935e91de8bd19e11d45c5bbcab86ef4d4a76dc91b7d4f2`). Data, manifests, capability context and authoring contracts were available in the existing persistent Daytona workspace before reasoning. No native operation invoked MCP.

The model used local shell inspection (`head`, `tail`, `wc`). It first attempted to read Parquet as UTF-8, then redundantly copied the already staged original through a native source-transfer tool. That is model/tool-selection inefficiency, not a requirement to fetch data through MCP. It did not execute a full-data Python exploration or author a transformation.

At action 24 its draft assessment still hypothesised that `alive` is available before the event and that all 891 rows represent unique passengers, without checks establishing those claims. The independent source audit establishes that `alive` exactly reproduces `survived`; it must not be an inference feature for survival prediction. Recorded hypotheses are not certification.

Action 25 timed out at the 150-second model-request deadline. The retained receipt records HTTP 200 headers, `TimeoutError`, unknown usage, no complete response, no model message and no provider response ID. No proposed tool was executed. The request cannot be reconciled from the retained metadata, so it was not replayed. The failed trial was cancelled to release the workspace, retaining its files and receipts. No transformed dataset was published and Qwen remains unqualified as the default.

Final verification: 44 distinct focused native-agent cases passed across the targeted runs, including changed-active-source recovery and retained package source inspection. The exact Data Workshop journey passed on the final run (1 test, 205.18 seconds). Applicable pre-commit hooks and Ruff passed. All local services temporarily paused during verification were restored; a final Docker check returned no paused containers. No model training, evaluation or deployment was launched.
