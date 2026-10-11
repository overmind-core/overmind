# Dataset activation controls removed

Verified on 2026-10-10 against the existing local Docker deployment, with the
Console at `http://localhost:5173` and API at `http://localhost:8000`.

## Fixture and replay

Open `/datasets/adc58aaa-d0bd-5ec7-9cf3-226bb7734abc?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8`
in the authenticated local Console. This is the existing Titanic survival
stratified decision split training dataset, with 625 rows and six canvas cells.

1. Inspect the cells, using Fit View to reach the full process.
1. Confirm no cell offers Set active. The current cell retains its passive Active
   indicator and Train a model action; the other five cells have no action footer.
1. Click Train a model. Confirm the training dialog selects this dataset, shows
   Decision as the task and 625 rows. Close without starting training.
1. Return to the dataset and use Reset view.

Observed: zero Set active buttons across all six rendered cells. Training setup
opened with the correct dataset selected. No training was launched.

Screenshot: `/private/tmp/dataset-no-activation-controls.jpg`.

## Source and static checks

From the repository root:

```sh
rg -n 'onActivate|patch\.mutate\(\{ active|Set active' frontend/src/components/datasets frontend/src/hooks/use-datasets.ts
```

Observed: no matches. The handoff only navigates to consumer setup; it no longer
patches the dataset's active cell. Backend and MCP activation contracts are outside
this UI-only change.

From `frontend/`:

```sh
bun run typecheck > /private/tmp/dataset-activation-ui-typecheck.log 2>&1
bun run lint > /private/tmp/dataset-activation-ui-lint.log 2>&1
bun run check:all > /private/tmp/dataset-activation-ui-design.log 2>&1
```

Observed: all passed. The unit suite was not run for this action removal; the
remaining consumer handoff was verified in the browser.
