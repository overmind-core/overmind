# Workshop condition connectors — 2026-10-09

Scope: condition-label borders and square connector routing only. No cell,
lineage, API or MCP behavior changed. Review used the existing neutral chip
outline, card fill and 2px radius from DESIGN.md.

## Environment and repeatable checks

Existing local Compose/Vite services, signed-in Console and synthetic fixture
from `workshop-flow-ui.md`; no provider jobs or dataset writes.

Open `/datasets/9b028cb6-a7d2-40e4-b22f-410954744e26?projectId=e18b29b5-915d-45a7-80cd-77ffe6559205`
at `http://localhost:5173`. Select Reset view. Inspect both conditional branches
at 1280×720, then resize to 390×844 and reset again. Inspect rendered
`.react-flow__edge-path` paths and `.react-flow__edge-textbg` rectangles.

Commands, from `frontend/`:

```sh
bun run typecheck
bun run check src/components/datasets/notebook/flow.ts
bun run check:all
```

## Observed results

- All commands passed. No fresh browser warnings or errors after reload.
- Before: 40px end stubs crossed inside the 65.75px handle gap, producing two
  horizontal levels and a backward vertical jog. After: zero minimum stub length
  produces one horizontal segment and square elbows; both branch paths share
  y=343.125 between source y=310.25 and target y=376 in canvas coordinates.
  SVG zero-radius quadratic commands have identical start/control/end points,
  so they do not introduce curves.
- Both chips have a 1px neutral outline, 2px corners and 8px horizontal padding.
  Measured label rectangles are 99.45px and 104.92px wide, both 22.5px high.
- Cell widths remain 1158px at desktop and 324px at mobile, with scale 1.
  Mobile document width remains 390px; the graph pans separately.
- Browser viewport restored; only the temporary verification tab was closed.
- Full test suite skipped for this narrow visual correction. No light-theme
  browser pass claimed; static contrast checks passed for both themes.

## Inline visual review

Disposition: ship. Desktop and mobile captures inspected. Existing cells,
typography and surfaces preserved; clean orthogonal connectors and outlined
labels meet the requested scope. No broader visual redesign or new primitive.
DESIGN.md records the connector and condition-label treatment.

Captures under `.impeccable/review/`:

- `workshop-condition-connectors-desktop.jpg`
- `workshop-condition-connectors-mobile.jpg`
