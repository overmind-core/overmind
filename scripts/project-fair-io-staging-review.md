# Staging review corrections

PR #210 follow-up, 2026-10-17. No Modal, training, inference or model-backed scoring jobs were launched for these checks.

## Corrections

- Mixed-worker draining resumes ordinary IO before returning. Dedicated trace workers retain the trace queue. Replacement task registration happens before draining, and unacknowledged consumer restoration fails visibly.
- Live scoring selects the first waiting item by kind using a composite `(project, state, kind, id)` index. Migration 0048 creates the replacement index concurrently before removing the old index.

## Verification

The existing staging database probe used transaction-local synthetic tables, rolled back afterward. Original selection times were 0.424 ms for 1,000 waiting rows, 3.984 ms for 10,000, and 38.761 ms for 100,000. At 100,000 rows the proposed empty-kind and matching-kind lookups took 1.363 ms combined. These are individual PostgreSQL query measurements, not application throughput or concurrent fairness certification.

The corrected focused suite passed **34 tests**, covering drain/recovery, deployment rendering, per-project fairness, duplicate delivery, expiry and the actual PostgreSQL selection plan. The three new regression cases failed against the original implementations and passed with the corrections. The plan check requires no sort and at most one examined row per selection-plan node.

Docker was stopped. Tests used an isolated temporary PostgreSQL instance and copies of the three test modules under the repository's existing conftest. Only the unused sandbox parser fixture was overridden; external-network guards and provider fakes remained active. Temporary test copies were removed after verification. This does not replace CI or rollout verification.

## Staging rollout

Staging originally lacked a dedicated trace worker and had only one healthy API replica because its old ECR image had been deleted. The user approved one trace worker at **1 vCPU / 4 GiB**, capped at one task. Infrastructure PR [cloud-platform#13](https://github.com/overmind-core/cloud-platform/pull/13) records that limit. Scoped deployment PassRole and queue-metrics permissions include the new staging worker.

The rebuilt image uses source commit `73a4a0e10e53f1cca2dd9dde8f1f9ea9eddbad96` and digest `sha256:a774f4704970aa04fbebb148ce955034501f3dacde12526c9d195d1db97502d5`. Migrations 0046–0048 completed successfully. [CI](https://github.com/overmind-core/overmind/actions/runs/39053102573) passed: 3,296 backend tests (15 skipped), 91 journey tests and 908 frontend tests, plus lint and migration checks.

The updated API source hashes matched all four corrected runtime/migration files. An actual deployed dispatcher probe used transaction-local copies of its three tables and a stub publisher:

- 10,000 waiting noisy-project items and one quiet-project item.
- Global limit four, per-project limit two.
- First dispatch admitted three items in 116.743 ms: two noisy items and the quiet item.
- The quiet item was claimed and completed while 9,998 noisy items remained waiting.
- A second dispatch admitted zero while the noisy project's two slots remained occupied.
- Empty-kind lookup used an index-only scan, no sort and no heap fetches; execution took 0.025 ms.
- All temporary data was rolled back. No broker messages or provider jobs were submitted.

The deployed drain helper restored ordinary IO while leaving traces assigned to the dedicated worker in a check with all Celery controls mocked. The real IO rollout also completed its drain before replacement.

Three enabled database scheduler entries referenced tasks absent from the current registry. These obsolete entries were disabled, preserving their records: `decision-jobs`, `sweep-unscored-traces`, and `sweep-workshop-executions`. Control and trace workers had no new ERROR/Traceback events in the subsequent verification window.

Final verification confirmed all nine services on the exact image digest, one completed deployment each and zero pending tasks. API revision 221 and every configured container health check were healthy; control, batch and beat have no container health status and were verified through ECS steady state. Both new API replicas became healthy before the existing request-based autoscaling policy reduced desired capacity from two to one at 17:57:15 UTC. One healthy API target remained; autoscaling limits were not changed. The trace worker was confirmed at min=max=1.

Staging MCP returned the expected HTTPS endpoint and 76-tool catalogue. API, control and trace logs contained zero ERROR/Traceback events in the final verification window. The runtime probe emitted the existing missing FIELD_ENCRYPTION_KEY configuration warning; encrypted-field configuration was not changed by this rollout.

These measurements verify the two corrections and bounded admission behavior; they do not certify thousands of concurrent end-user workflows or provider capacity. No Modal jobs were triggered, no production service was changed, and neither PR was merged.
