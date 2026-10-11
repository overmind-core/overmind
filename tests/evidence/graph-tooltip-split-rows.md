# Graph tooltips with flat split rows

Date: 2026-10-10

The shared GraphTooltip shows the step, epoch, iteration or time above full-width
series rows. Each row has its line colour and model/series name on the left, and
the formatted value on the right. One outer frame encloses the whole tooltip;
rows have no nested boxes, rounding or outer padding. Header and row separators
reach the frame edges.

Training single-model charts now label the series with the model name. Training
comparisons retain experiment names alongside the model when the same base appears
more than once. Evaluator, class and inference graphs retain their series identity.
Existing numeric precision, percentages, scientific notation and date formatting
are preserved; the historical loss chart identifies an epoch axis as Epoch.

## Commands

Run from `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
bun run test src/components/finetuning/loss-chart.test.ts src/components/finetuning/development-monitor.test.tsx src/components/finetuning/training-monitor.test.tsx
```

Observed: all checks passed; 27 tests in three files passed. No new unit tests were
added for the presentation change. The final removal of row boxes was checked with
Biome and in the browser; it did not change graph data or formatting logic.

## Browser replay

Environment: existing local Docker stack, Console at localhost:5173, normal
1280 × 720 viewport, collapsed sidebar. No runs or external actions were started.

Open:

```text
http://localhost:5173/training?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8&groupId=9e61cda4-9bdc-4655-af5d-01aea112a876&jobId=535888b9-70ac-43c1-96c0-164fc9f54762
```

Scroll to Metrics and point at the Validation loss point at step 80. Observed:

- Header: Step 80.
- Left: green line marker and Qwen3 0.6B.
- Right: 0.8031.
- The row reaches the inside of the tooltip frame: 1 px left/right inset, zero
  row side borders and zero row border radius.
- Learning rate at step 49 retains `1.40e-4` with the same model name.

Screenshots:

- Full view: `/private/tmp/graph-tooltip-flat-training.jpg`.
- Validation graph: `/private/tmp/graph-tooltip-flat-detail.jpg`.

The shared tooltip is also wired into Optimiser score history, capability evaluator
history, inference activity and training drawer charts. This local project has no
populated comparison run or evaluator history, so those variants were verified
through code review and typecheck rather than newly creating platform data.
