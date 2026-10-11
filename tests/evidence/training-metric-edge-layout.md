# Training metric layout

## Verification plan

Failure cases considered before implementation:

- Missing or non-finite measurements becoming zero-valued statistics.
- Out-of-order receipts producing an incorrect latest value or change.
- Downsampled charts changing the reported observed extrema.
- A single measurement implying a measured change; a zero first value causing an
  invalid relative change.
- Combining different models into a misleading summary in comparison mode.
- Constant or single-point series disappearing; endpoint labels clipping at small
  widths; hover and keyboard navigation regressing when chart axes are hidden.
- Failed/cancelled runs labelling their last measurement as a completed final score.

Pure summary fixtures cover arithmetic and missing-data cases before implementation.
Browser verification will use existing local training runs, without launching work,
to inspect graph width, endpoint labels, summaries, comparisons and tooltips.

## Repeatable checks

Use the existing local Docker stack (Console `http://localhost:5173`, API
`http://localhost:8000`). From `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
bun run test src/components/finetuning/metric-summary.test.ts src/components/finetuning/loss-chart.test.ts src/components/finetuning/development-monitor.test.tsx src/components/finetuning/training-monitor.test.tsx
```

Observed: 32 tests passed in four files. Typecheck, lint and design/contrast/control
checks passed. The initial design check rejected arbitrary 10px label classes;
these were replaced with the shared text-xs scale and the checks rerun successfully.

## Existing training run

Open the authenticated local Console at:

```text
http://localhost:5173/training?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8&groupId=9e61cda4-9bdc-4655-af5d-01aea112a876&jobId=535888b9-70ac-43c1-96c0-164fc9f54762
```

Scroll to Metrics. At 1280 × 720 each card was 571px wide, including its two 1px
outer borders; each plot measured 569px. Header and footer dividers reached both
inner edges. There is no nested graph frame or axis gutter. Following the requested
iteration, the faint dashed grid follows numeric axis ticks. Non-negative graphs
start at zero and omit the bottom zero label. The final plot removes all margins
and places endpoint labels over the graph: its clip rectangle is exactly
`x=0, y=0, width=569, height=180`, matching the whole SVG. It reaches the title and
summary dividers without label gutters above or below.

- Validation loss: last recorded 0.6084, lowest 0.6084, change from first -0.0791.
- Learning rate: last recorded 3.80e-8, peak 2.00e-4, initial 0.00e+0.
- Gradient norm: last recorded 2.3951, peak 6.0244, average 1.0257.
- Pointer selection at validation step 80 showed Qwen3 0.6B and 0.8031 in the
  existing split tooltip, with no pointer focus outline.
- No application console errors were recorded. No jobs were launched.

Screenshot: `/private/tmp/training-metric-edge-cards.jpg`.

Final grid/frame correction: `/private/tmp/metric-grid-full-frame.jpg`.

## Browser fixture

Open `http://localhost:5173/tests/training-metrics.html`. The retained fixture uses
the production chart and card components with explicitly synthetic measurements.

- Comparison keeps separate model summaries: 0.5000 / 0.3000 / -0.3000 and
  0.2000 / 0.2000 / -0.7000.
- After the grid correction, converting its SVG coordinates back through the
  fixture's known domains yields steps 0, 10, 20, 30, 40 and values 0, 0.25, 0.5,
  0.75, 0.9. These are axis ticks, not fractional frame positions.
- Show finished switches Latest to Last recorded and retains the measurements.
- One zero measurement renders one point, zero-valued summaries and an unmeasured
  change. A constant series remains visible. Empty history shows No loss recorded
  and three dashes.
- Tab from the comparison help button into the chart, then ArrowRight: tooltips
  advance from step 10 (0.6000, Qwen3.5 0.8B) to step 20 (0.3000, Qwen3 0.6B).
- At 390 × 844, the cards become one column; document and scroll widths both equal
  390px. Endpoint labels and all three summary columns remain visible. The viewport
  override was reset after inspection.
- The fixture initially warned about duplicate React roots during hot reload;
  disposing its root on module replacement fixes that fixture lifecycle. A fresh
  tab and subsequent hot reload produced no console errors.

Narrow screenshot: `/private/tmp/training-metric-edge-mobile.jpg`.
