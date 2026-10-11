# Training dataset chip

Verified locally on 2026-10-10 using the running Console at
`http://localhost:5173`, the local API at `http://localhost:8000`, and an
authenticated Codex in-app browser session at 1280 × 720.

The single-run card and grouped-run header now use the existing dataset
`EntityRef` chip. Links retain the project ID, shared hover card, dataset icon,
truncation and keyboard-focus styling.

## Browser replay

1. Open `/training?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8`.
1. Open the row for `Tasksource JEV typed decisions · prepared train`.
1. In run `0af7dc70-5ab2-48fc-80ed-ce7839836893`, verify the dataset name appears
   as a chip in the top-right metadata. The active job is
   `811bbc0e-9c18-418b-9708-fe9f82325e87`.
1. Click the dataset chip. Observed destination:
   `/datasets/0b0d60b5-d321-416d-8881-f5934558d337?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8`.
   The dataset switcher displayed `Tasksource JEV typed decisions · prepared train`.

The screenshot before navigation is `/private/tmp/training-dataset-chip.jpg`.
No training controls were operated. The grouped header uses the same chip but was
not separately exercised in the browser.

## Checks

From `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
```

All passed. Logs: `/private/tmp/training-dataset-chip-{typecheck,lint,design}.log`.
The full test suite was not run for this small presentation/link change.
