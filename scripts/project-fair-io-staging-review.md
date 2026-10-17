# Staging review corrections

PR #210 follow-up, 2026-10-17. No Modal, training, inference or model-backed scoring jobs were launched for these checks.

## Corrections

- Mixed-worker draining resumes ordinary IO before returning. Dedicated trace workers retain the trace queue. Replacement task registration happens before draining, and unacknowledged consumer restoration fails visibly.
- Live scoring selects the first waiting item by kind using a composite `(project, state, kind, id)` index. Migration 0048 creates the replacement index concurrently before removing the old index.

## Verification

The existing staging database probe used transaction-local synthetic tables, rolled back afterward. Original selection times were 0.424 ms for 1,000 waiting rows, 3.984 ms for 10,000, and 38.761 ms for 100,000. At 100,000 rows the proposed empty-kind and matching-kind lookups took 1.363 ms combined. These are individual PostgreSQL query measurements, not application throughput or concurrent fairness certification.

The corrected focused suite passed **34 tests**, covering drain/recovery, deployment rendering, per-project fairness, duplicate delivery, expiry and the actual PostgreSQL selection plan. The three new regression cases failed against the original implementations and passed with the corrections. The plan check requires no sort and at most one examined row per selection-plan node.

Docker was stopped. Tests used an isolated temporary PostgreSQL instance and copies of the three test modules under the repository's existing conftest. Only the unused sandbox parser fixture was overridden; external-network guards and provider fakes remained active. Temporary test copies were removed after verification. This does not replace CI or rollout verification.

## Rollout prerequisites

Staging was running the earlier release, with no dedicated trace-worker service. Its API desired count was two, but only one replica was healthy; replacement failed because both the `sha-bdec185` ECR tag and the running replica's image digest were missing. A rebuild is required. Additional AWS capacity requires user approval. Deployment and live verification are not yet complete.
