# Branch cleanup — 2026-10-12

The Ponytail sweep compared `codex/general-decision-training` with merge base
`5a0bd483e1800477ad0905e0bc43a359893d6a35`. The pre-cleanup snapshot is
`63caac868ac9fc1cf6b66525f029e84e1b90a0de`.

| Measure             |  Before |   After |
| ------------------- | ------: | ------: |
| Added lines         | 242,872 |  82,514 |
| Deleted lines       |  21,790 |  22,500 |
| Total changed lines | 264,662 | 105,014 |
| Net growth          | 221,082 |  60,014 |

Total changed lines fell by approximately 61%. Counts include the compact
replacement documentation; line count was not a stopping condition.

## Removed

- Generated JSON receipts, screenshots, local probes and one-off replay scripts
  from `tests/evidence/`; superseded experiment notes and six unreferenced manual
  frontend harness files. All 352 files were copied to ignored
  `scratch/branch-cleanup-2026-10-12/` and verified against their original SHA-256
  content. They also remain recoverable from the pre-cleanup commit.
- Unused frontend exports, the superseded synchronous inference helper, unused
  serializers and scoring/preparation helpers, and obsolete in-memory sampling
  and provenance implementations. Their reference checks included tests and
  configuration; dynamic framework entry points were retained.
- Unused `datasketch`, `langdetect` and `rapidfuzz` dependencies and lock entries.

Historical Banking77, SST-5 and BoolQ results and their frozen protocol remain in
[the benchmark record](decision-benchmarks.md). Provenance and mixed-target tests
now exercise production file-lineage and sampling-profile services.

## Runtime simplification

- Experiment preparation verifies and scans each shared source once per attempt,
  reusing its frozen metadata across model variants. Two variants sharing train
  and development data now issue two cell reads instead of four. Changed files
  still fail preparation; no cache survives an attempt.
- Evaluation monitoring calibrates predictions in batches of at most 256 rows,
  using the indexed participant/key cursor. A 513-row regression covers multiple
  batches, invalid predictions, retained raw probabilities and repeat refreshes.
  Summary aggregation reads each participant's predictions once instead of three
  times; a differential check matched the previous results across 10,000 samples.
  A non-unique input-hash index speeds external prediction matching while keeping
  duplicate observations.
- Monitoring overviews no longer deep-copy freshly loaded metric receipts before
  removing paginated fields. REST/MCP evidence pagination tests retain the full
  stored values and checksums.
- Seven MCP modules use the installed async adapter directly. Dataset resolution
  delegates directly to the shared resolver; UUID parsing has one implementation.
  Project filters, error conversion and thread-sensitive database access remain.
- Nine live charts share one renderer with their existing labels, colours,
  numeric formatting, checkpoint markers and empty states. The duplicated Discord
  SVG now lives in the shared icon registry.

## Preserved and verified

The cleanup leaves generated API clients, database migrations, the SDK, training
assets and pinned GPU worker sources unchanged. It does not remove product
features, contract checks, billing, cancellation or error handling. Similar code
with different selection, authorization or probability semantics was not merged
merely to shorten it. Static scans do not prove that no future simplification is
possible.

- Backend: 4,386 passed, 10 skipped; 319 MCP tests passed after the final
  reference-helper consolidation. All 15 native evaluation tests passed after
  the final aggregation/index changes.
- End-to-end journeys: 76 passed against local ASGI, Celery, Postgres and Redis;
  the three native benchmark journeys also passed after the final query changes,
  covering REST, MCP and Console progress, samples and historical backfill.
- Frontend: 894 passed; final icon registry: 113 passed. Type checking, lint and
  design checks passed.
- SDK: 591 passed. Backend lint/format and lock consistency checks passed.
- Required pre-commit checks and `git diff --check` passed.

External providers are faked in automated tests. No live GPU training, hosted
deployment or production write was part of this cleanup.

Backend skips cover absent Torch/Truss packages and opt-in Docker/Redis fixtures.
The journey suite supplies its own local Redis/worker integration checks.
