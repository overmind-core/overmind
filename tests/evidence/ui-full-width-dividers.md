# Full-width UI dividers

Date: 2026-10-10

## Environment and scope

- Existing local Docker stack, Console at `http://localhost:5173`.
- Local project: `a279d834-899c-4ac6-888a-157f0c7d9cf8` (Jev local validation).
- Frontend layout changes only. No jobs, deployments, connections or feedback were submitted.
- Audited divider/table markup and its padded ancestors across 67 frontend files.
- Updated 18 components/routes covering training, evaluations, Optimiser, inference,
  integrations, trace details/filters, dataset cell popovers, capability arguments,
  onboarding, feedback and OAuth authorization.
- Shared dialog/sheet headers, footers, table shells and menu separators already
  reach their containing edges. Chart axes, prose rules and nested bordered surfaces
  retain their own boundaries.

## Repeatable checks

Run from `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
bun run test src/components/finetuning/development-monitor.test.tsx src/components/finetuning/training-monitor.test.tsx src/components/traces/filters.test.tsx src/components/finetuning/train/__tests__/evidence.test.tsx
```

Observed: typecheck, lint and design/contrast/control checks passed. Four test files,
64 tests passed. Vitest emitted the existing `--localstorage-file` warning; no test
failures. No new unit tests were added for CSS class changes.

## Browser replay

Use the project above and existing retained records. All measurements were made
at the normal 1280 × 720 browser viewport with the sidebar collapsed.

1. Open Training group `9e61cda4-9bdc-4655-af5d-01aea112a876`, job
   `535888b9-70ac-43c1-96c0-164fc9f54762` (Titanic survival Jev-style decision model).
   Inspect step 80 under Development monitoring. Confirm the checks table,
   selected evidence section, prediction examples, retained checkpoints and frozen
   samples reach the card edges while text retains its padding.
1. Open evaluation run `6fdcb13b-c86a-47fe-b0ad-16274f92dd6d`
   (Jev paired KYC generation · 4 rows). Inspect the metadata and actions separators.
1. Open Evaluations → Eval library. Inspect the unassigned evaluator cards.
1. Open Feedback from the sidebar. Inspect the separator above the Discord link,
   then close without submitting.
1. Open Observability → Root traces → All time. Inspect the separator above
   Custom range without applying a range.

For a separator, compare its `getBoundingClientRect()` with its containing bordered
card/panel/dialog. The left and right inset should each equal the container's 1 px
border. Table containers should have the same relationship. Content remains inset
by its original padding.

Observed geometry:

| Surface                                     | Container width | Left inset | Right inset |
| ------------------------------------------- | --------------: | ---------: | ----------: |
| Training run card sections                  |         1166 px |       1 px |        1 px |
| Development monitoring sections and table   |         1166 px |       1 px |        1 px |
| Evaluation run card sections                |         1166 px |       1 px |        1 px |
| Evaluator card footer (six inspected cards) |      366.664 px |       1 px |        1 px |
| Feedback dialog separator                   |          640 px |       1 px |        1 px |
| Time-range popover separator                |          320 px |       1 px |        1 px |

Screenshots retained locally:

- `/private/tmp/ui-dividers-evaluation.jpg`
- `/private/tmp/ui-dividers-evaluator-library.jpg`
- `/private/tmp/ui-dividers-monitoring.jpg`

## Coverage limits

This project has no Optimiser runs, inference deployments or integration credentials.
Those conditional sections, OAuth authorization, message-array/tool-parameter
variants and other conditional table states were reviewed against their containing
padding in source rather than exercised with newly created data. The retained
Parcel trace detail was opened successfully but contains plain input/output, not
the changed message-array variant. No responsive viewport override was applied.
