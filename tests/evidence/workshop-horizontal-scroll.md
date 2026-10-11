# Workshop horizontal scrolling

Verified locally on 2026-10-10 using the running Docker Compose stack and Codex
in-app browser (1280 × 720). Frontend: `http://localhost:5173`; API:
`http://localhost:8000`. No dataset mutations or remote deployments.

## Fixture and reproduction

- Dataset: `ecabb7ed-4b75-4f2b-8572-35223f1c414c`
- Project: `a279d834-899c-4ac6-888a-157f0c7d9cf8`
- Cell: `e5563a89-f9b2-4dfd-bd68-c1dc6c71babd`
- Title: Validate and preserve decision observations
- Data: 1,034,657 rows, 15 columns, 10 rows per page.

Open `/datasets/ecabb7ed-4b75-4f2b-8572-35223f1c414c?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8`
with an authenticated local Console session. Click Reset view to focus the active
cell. With the pointer over its data rows, scroll right to the final column, then
left to the first column. Scroll over the empty canvas to check panning separately.

Browser commands after binding the tab as `scrollTab`:

```js
await scrollTab.playwright.getByRole('button', {name: 'Reset view', exact: true}).click();
await scrollTab.getAXState({emit: false});
await scrollTab.scroll([700, 325], 'right', 1);
await scrollTab.getAXState({emit: false});
await scrollTab.scroll([700, 325], 'right', 2);
await scrollTab.getAXState({emit: false});
await scrollTab.scroll([700, 325], 'left', 3);
await scrollTab.getAXState({emit: false});
await scrollTab.scroll([1000, 610], 'right', 0.25);
await scrollTab.getAXState({emit: false});
```

Read the table parent's `scrollLeft`, `clientWidth`, and `scrollWidth`, and the
`.react-flow__viewport` element's inline transform after each gesture. Coordinates
above apply to the recorded viewport; locate the data rows first on other sizes.

## Observed results

Before the fix, one rightward gesture left the table at `scrollLeft=0` and moved
the canvas from `translate(20px, -348px)` to `translate(-620px, -348px)` at scale 1.

After the fix:

| Interaction            | Table scrollLeft | Canvas transform (scale 1) |
| ---------------------- | ---------------: | -------------------------- |
| Reset view             |                0 | translate(20px, -568px)    |
| Right, one page        |             1280 | translate(20px, -568px)    |
| Right, to end          |             2540 | translate(20px, -568px)    |
| Left, to start         |                0 | translate(20px, -568px)    |
| Right, on empty canvas |                0 | translate(-140px, -568px)  |

The table measured 3671px across in a 1131px viewport. Its maximum scroll offset
was 2540px. The final view showed `decision`, `input`, `expected_output`, and
`source_metadata`. The canvas was reset after verification.

The grid now uses React Flow's `nowheel` class so native table scrolling receives
wheel gestures. Table sizing, row rendering, pagination and canvas panning are
unchanged.

## Static checks

From `frontend/`, all exited 0:

```sh
bun run typecheck
bun run lint
bun run check:all
```

Logs: `/private/tmp/workshop-horizontal-scroll-{typecheck,lint,design}.log`.
Screenshot: `/private/tmp/workshop-horizontal-scroll-right.jpg`.
Measurements: `/private/tmp/workshop-horizontal-scroll-observations.json`.
The full test suite was not run for this one-line UI fix; the browser journey
above exercises the reported failure directly.
