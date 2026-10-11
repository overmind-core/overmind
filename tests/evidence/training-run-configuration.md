# Training run configuration table

Verified against the local Console on 2026-10-10. API: `http://localhost:8000`;
Console: `http://localhost:5173`. The existing Docker development stack and an
authenticated local Console session are required. No training was launched or
modified for this check.

## Browser replay

1. Open `/training?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8&groupId=0af7dc70-5ab2-48fc-80ed-ce7839836893&jobId=811bbc0e-9c18-418b-9708-fe9f82325e87`.
1. Expand **Run configuration**. Inspect the Setting/Value table, its section
   headings and the source labels. The saved request shows one epoch, seed
   `20261011`, learning rate `0.0002`, LoRA rank `16`, packing disabled, adaptive
   monitoring, 1,034,657 training rows and 7,948 validation rows.
1. Inspect the training and validation dataset links. They point to datasets
   `0b0d60b5-d321-416d-8881-f5934558d337` and
   `2e9d8ec4-0a99-47ee-ac14-2bd51c3eae77` with the current project ID.
1. Scroll within the table. The column header remains sticky and nested settings,
   exact cell IDs, fingerprints, worker release and launch receipt are accessible.
   The table has 92 rows including headers; at the tested viewport its scroll
   width and client width both measure 1,164 px. Vertical content is 3,202 px,
   inside a bounded scroll region. Missing effective configuration reads
   **Not recorded**.
1. Collapse the disclosure. The table disappears and the plain Run configuration
   header remains. Search and chip-removal verification is recorded in
   `training-run-configuration-search.md`.
1. Open `/training?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8&groupId=9e61cda4-9bdc-4655-af5d-01aea112a876&jobId=535888b9-70ac-43c1-96c0-164fc9f54762`.
1. Expand **Run configuration** on this completed run. Its effective configuration
   shows GPU type `H100`, GPU count `1`, per-device batch `16`, gradient
   accumulation `1`, context length `4096`, LoRA alpha `32`, warmup ratio `0.05`
   and `overmind-dev`. These are labelled **Recorded at provider submission**,
   separately from the frozen request.

Observed all steps above. Screenshot: `/private/tmp/run-configuration-expanded.jpg`.

## Automated checks

From `frontend/`:

```sh
bun run test src/components/finetuning/run-configuration.test.ts src/components/finetuning/training-monitor.test.tsx
bun run typecheck
bun run lint
bun run check:all
```

21 tests passed. Typecheck, lint, design, contrast and control checks passed.
The first monitor test run failed because its harness lacked router context for
the dataset links. The harness now provides a real in-memory router; all 13
previously failing cases passed on rerun. Configuration tests cover frozen versus
mutable values, unresolved effective settings, nested options, exact numeric
precision and distinct false/zero/null/empty states.

Logs: `/private/tmp/run-configuration-tests.log`,
`/private/tmp/run-configuration-typecheck.log`,
`/private/tmp/run-configuration-lint.log`,
`/private/tmp/run-configuration-design.log`.

This is a Console presentation change using the existing generated job contract;
REST, MCP, training dispatch and stored run records are unchanged.
