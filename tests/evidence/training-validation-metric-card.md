# Validation loss metric card

Date: 2026-10-10

The training UI replaces the Development monitoring detail panel with a Validation
loss graph inside the existing metrics grid. All metric cards share the same card
component. Monitoring tables, timing counters, examples, checkpoint lists and policy
disclosures are removed from this UI. REST/MCP receipts and training execution are
unchanged.

## Verification plan

Failure cases considered before implementation: hiding measurements after the first
history page; mixing the final full-population measurement into the fixed-sample
curve; plotting missing/non-finite values as zero; hiding a single valid measurement;
and presenting a failed history request as an empty or partial successful result.
The existing component tests were replaced before implementation to cover those
cases. Browser verification checks actual graph rendering and grid placement.

## Commands

From `frontend/`, using the existing local Docker stack:

```sh
bun run typecheck
bun run lint
bun run check:all
bun run test src/components/finetuning/development-monitor.test.tsx src/components/finetuning/training-monitor.test.tsx
```

Observed: all checks passed; 22 tests in two files passed. Vitest emitted the existing
`--localstorage-file` warning, with no failed tests.

## Browser replay

Open the local Console training run:

```text
http://localhost:5173/training?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8&groupId=9e61cda4-9bdc-4655-af5d-01aea112a876&jobId=535888b9-70ac-43c1-96c0-164fc9f54762
```

Scroll to Metrics. At the normal 1280 × 720 viewport, observed:

- Loss, Validation loss, Learning rate and Grad norm occupy the same two-column grid.
- Each card measures 577 × 230 px and contains one rendered graph.
- Validation loss has four points, corresponding to fixed-sample steps 0, 40, 80
  and 120. The full-development result does not add a fifth point.
- No Development monitoring heading, check table, Inspect controls, timing counters,
  sample details or policy disclosure remains below the grid.
- No browser console errors were recorded.
- No training jobs or evaluation runs were started.

Screenshot: `/private/tmp/training-validation-metric-card.jpg`.

Pagination, a single zero-valued measurement, missing data and a failed later page
were checked with deterministic component fixtures; the live run supplied the
populated chart used for browser verification.
