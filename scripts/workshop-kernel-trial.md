# Persistent Data Workshop kernel trial

## Runtime

The native agent uses one headless IPython kernel per task inside the project's persistent Daytona workspace. Local files, dataframes, imports and functions survive successive executions. The kernel has no model credentials, runs as uid 10001 and cannot modify staged inputs or the supervisor's execution journal. Platform actions still call native domain services; no browser, webpage, MCP client or runtime package installation is involved.

Execution IDs are persisted before submission. Retrying the same ID and payload returns its retained receipt; a changed payload conflicts. Losing the supervisor fences the old namespace and marks unfinished execution outcomes unknown. Reset creates a new kernel generation while retaining files and receipts. Clean replay runs saved code from immutable inputs without relying on notebook memory.

- Qualified image: `overmind-kernel-0d44fd5b6bfa`.
- Runtime identity: `sha256:0d44fd5b6bfa6408b60da0b428901edb20edf12ebde1ad8e2544eb3fd9728bbd`.
- Daytona workspace: `c2482603-2b3c-4f14-bba3-aa0bfcc2eb03`.
- Local project: `1e3f3e92-b50d-4590-85ed-97921d132d3c`.
- Previous workspace retained stopped: `a6d65896-7e7b-458e-bcb5-2a2ee40edeb8`.
- Restored checkpoint SHA-256: `9a4660a32ea4d0d10fc6487ef5676b84232ed1d8fd2778e964ee3d1d909a9371`.

## Verification before the LLM

`scripts/qualify_workshop_session.py` passed against the immutable image, with its full evidence retained in `output/native-workshop/kernel-qualification.json`:

1. Kernel readiness.
1. Direct local data access and unprivileged execution.
1. Shared dataframe state across calls.
1. Structured Python exception output.
1. Successful repair after the exception.
1. Protected source bytes and execution journal.
1. Client reconnection and retry without duplicate execution.
1. Forced supervisor crash, unknown outcome and namespace fencing.
1. Explicit reset into a new generation.
1. Byte-identical clean replay of a saved transformation.
1. Interrupt followed by successful kernel reuse.

The synthetic three-row replay produced SHA-256 `9ce3dac1945574337ce2d29c21f04eba55c8de69d31566a847a7385dbfc00411`. This qualifies these runtime behaviors, not transformation semantics for arbitrary data.

Local verification: 50 focused tests passed, the Data Workshop journey passed, and applicable pre-commit hooks passed. Live testing caught and corrected Jupyter rewriting the connection file with permissions the unprivileged kernel could not read.

## Native Titanic retry

- Task: `7650137e-b15a-4c39-95ee-9a054810505a`.
- Dataset: `5a8df227-9982-4ef6-96c4-d9b27e97185b`.
- Original source cell: `637dcfac-dcd7-4642-8de0-679cbf9c4ab1`, 891 rows.
- Source fingerprint: `f1a985c11befb35195b826c46acd222448ab9e9b1769bddf69eb0344244c680b`.
- Objective: “I want to train a decision model on this data”.
- Model: `openai/gpt-6.1-sol` through the configured OpenRouter API.
- No action-count limit or total token budget.

The agent published revision `f6e8731b-831c-4be6-a3d8-7710412db145`, retained package `f2004c2e-daff-41fc-8133-f3a7e5c89c5d`, into cell `9cbcf0b8-ca27-4c00-a2c0-1f8f6d6a7837`. Publication fingerprint: `0d2954d0e529280731d8a8844ca3f40adf65539dfc07f61ca9959672e600b831`.

Independent full-row audit (`output/native-workshop/audit_titanic.py`) passed:

- All 891 observations and source-parent lineage retained; labels remain 549 negative and 342 positive.
- Model state contains pclass, sex, age, sibsp, parch, fare and embarked. Every value matches the source; 177 missing ages and two missing embarkation values remain explicit nulls.
- The exact outcome alias `alive` is excluded. The source label only supplies categorical-gold targets in fixed `[no, yes]` option order.
- All rows pass the native decision reader. The downloaded retained script runs in a clean process and reproduces every published value, excluding publisher-owned lineage metadata.
- There are 764 unique model states, 127 repeated-state rows beyond the first, and 16 states with conflicting observed labels. These observations remain preserved; equal attributes do not prove duplicate people.
- Clean replay canonical SHA-256: `352852d145b861b7afca73445df9c8ab0c0c18229a3591c4e69ed050a3927860`.

The live trial revealed a separate native-tool defect: `create_data_partition` advertised an untyped recipe, while its error called the `train` role “training”. The agent tried unsupported role names and an array for a single stratification field. The task was paused, the nested contract moved into the domain layer and shared by native/MCP tools, and the error corrected to exact wire keys. The same task resumed without resetting its kernel or changing the objective/data. Native agent tests passed (46), as did four existing shared partition/training preparation tests. This intervention must not be described as a fully unattended successful trial.

Independent verification of partition `92549024-c554-494b-b0a4-1f7a378b9cd5` passed. The partition uses seed 42, `travel_group` grouping and `observed_label` stratification. It retains all 891 observations exactly once, with zero cross-role overlap in source identities, exact model states or declared proxy groups.

| Role        | Rows | Negative / positive | Groups | Cell                                   |
| ----------- | ---: | ------------------: | -----: | -------------------------------------- |
| train       |  624 |           384 / 240 |    133 | `1cac140f-6713-48d1-a043-609e986b6d37` |
| development |  132 |             82 / 50 |     65 | `451e483e-2013-4c76-b9ba-abc7c653a169` |
| final       |  135 |             83 / 52 |     68 | `0c3f0572-ccbb-4a0e-bd7e-1e7cec2ca63e` |

The final role also passes the native evaluation request/reference reader, with targets outside inference input. Groups are a class/fare/embarkation proxy: exact-state overlap is ruled out, but true family independence and all forms of near-duplicate leakage are not established. All rows remain suitable as observed-label examples under the documented pre-outcome feature assumption; this is not proof of deployment suitability or model quality.

The agent independently reached the same full-source and partition checks in action 150. Its first completion proposal then exposed a second adapter mismatch: it included the valid partition cells, but dataset preparation accepts only cells belonging to its original dataset. Native completion now validates the primary outputs and their completed partition members in the same project, retains all outputs in the task result, and passes only primary cells to the dataset-scoped preparation. Unrelated and cross-project cells remain invalid. Invalid selections receive model correction feedback before finish rather than a generic interrupted-execution failure.

The same native task completed on 2026-11-07 at 02:46:30 UTC, with all four output cells retained. Recorded aggregate model usage was 1,596,569 input/output tokens across the run; this is measured usage, not an imposed budget. The task began at 02:35:32 UTC; that interval includes both operator pauses for adapter repairs. Repeated documentation reads, plan-citation corrections and verifier assumptions added substantial overhead. The result qualifies the data transformation and tested runtime recovery behavior; it does not yet qualify the agent's efficiency or fully unattended reliability.

Final validation after the completion repair: 47 native-agent tests passed. The four shared partition/training tests and Data Workshop journey also passed after the partition repair; real local-kernel tests and all 11 live immutable-image checks passed earlier. Applicable pre-commit checks passed. Evidence is retained in `output/native-workshop/titanic-task-result.json`, `titanic-output-audit.json` and `kernel-qualification.json`.

## Limits

This is a single-project qualification, not a concurrency or capacity benchmark. Kernel memory is transient; scripts and files are the recovery boundary. An unknown execution is never silently replayed. Automatic terminal-task kernel cleanup is not yet integrated. Training performance remains unmeasured; this trial does not launch training.

Architectural review after this trial confirmed that Pydantic AI reasoning still
runs in the Django/Temporal worker, while Daytona hosts the persistent kernel.
Publication execution and partition construction also remain outside that
workspace. This trial therefore qualifies an execution component, not a complete
colocated agent. The [revised architecture](../NATIVE_AGENT_ARCHITECTURE_PROPOSAL.md)
moves the full agent loop and local data work into Daytona and reduces Temporal
to whole-run supervision. That refactor is not deployed by this review.
