# Workshop canvas navigation — 2026-10-09

## Follow-up: compact left-hand controls

The later user request replaces the outline described in the original receipt
below: remove the cell-selector strip and cell search, and move the minimap
toggle into the same left-hand box as the dataset sidebar button.

Repeated against the same local fixture and URL below on 2026-10-09:

1. At 1280×720, Workshop controls contains exactly the sidebar and minimap
   buttons. Both are 28×28px, aligned at x=63, with y=82 and y=110. Cell search,
   cell-selector strip and the top-right map toggle are absent.
1. Show minimap displays the map and sets `aria-pressed=true`. Reload preserves
   the visible map and pressed state.
1. Expand datasets opens project navigation; Collapse datasets closes it.
1. At 390×844, both controls and the visible map remain within the viewport.
   Hide minimap sets `aria-pressed=false` and restores the original preference.
1. Source rows remain Example A / true, Example B / false and Example C / true.
   No dataset or backend state changed. Temporary viewport and tab cleaned up.

Commands from `frontend/`:

```sh
bun run typecheck && bun run lint && bun run check:all
```

Passed typecheck, lint (459 files), design, contrast and control checks. No unit
or backend suite run for this narrow controls change. Existing shared tokens
were retained; dark-theme desktop/mobile visually checked, both themes covered
by static contrast checks. Layout guidance kept the remaining controls grouped
in the existing opaque box without changing cells or graph navigation.

Captures: `.impeccable/review/workshop-compact-controls-desktop.jpg` and
`.impeccable/review/workshop-compact-controls-mobile.jpg`.

## Direction contract

THESIS: navigate a large transformation graph without changing its cells.
OWN-WORLD: reuse the Agent trajectory's existing zoom controls, neutral minimap
and toggle button; preserve Workshop cells and conditional connectors.
STORY: open at natural size, zoom out to inspect branches, navigate the map,
return to a cell at natural size.
FIRST VIEWPORT: existing cells and outline; zoom/fit/reset bottom-left,
minimap toggle top-right and optional map bottom-right.
FORM: user-pinned extension of the existing Operate surface; no new visual world.
FINISH: unreviewed and undocumented is unfinished; this build ends with the
finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.

## Verification plan

Use the running local Compose/Vite services and signed-in Console. Existing
synthetic dataset `9b028cb6-a7d2-40e4-b22f-410954744e26`, project
`e18b29b5-915d-45a7-80cd-77ffe6559205`; no platform data writes or paid jobs.

Open `http://localhost:5173/datasets/9b028cb6-a7d2-40e4-b22f-410954744e26?projectId=e18b29b5-915d-45a7-80cd-77ffe6559205`.

Failure cases to exercise through the browser:

- Zoom controls are visible but still clamped to scale 1.
- Fit view changes cell width rather than camera scale, or excludes a branch.
- Minimap toggle does not show/hide the map, has no pressed state, or resets on reload.
- Minimap navigation does not move the viewport.
- Resize or opening a dataset sidebar unexpectedly resets the chosen zoom.
- Reset or outline focus fails to restore natural-size cells.
- Controls or minimap overflow narrow screens or obscure each other.
- Existing straight connectors, conditional chip borders or cell controls regress.

Commands from `frontend/`: `bun run typecheck`, `bun run lint`,
`bun run check:all`. Inspect desktop and mobile screenshots and fresh console logs.

## Results

Browser verification passed on the running local app:

- Initial camera scale 1, cells 1158px wide at 1280×720, minimap hidden.
- Zoom Out changed scale to 0.833333 without changing cell widths. Zoom In
  increased the scale. Limits reached 0.25 and 1.4 with their respective controls
  disabled at the boundary.
- Fit View used scale 0.429525. All three node bounds were inside the canvas;
  sibling branches retained their shared horizontal layer.
- Show minimap mounted three node rectangles and set `aria-pressed=true`.
  Dragging the map changed camera x from -21.31 to 321.00 at unchanged scale
  0.51543. A node click alone does not recenter, matching the Agent minimap's
  drag-to-pan behavior.
- Visible state survived reload. Hide minimap removed it, set
  `aria-pressed=false`, and hidden state also survived reload.
- Opening and closing dataset navigation preserved the selected zoom. Resizing
  to 390×844 preserved 0.429525 while responsive cell widths became 324px.
- Reset and outline focus each returned to scale 1. Mobile document width was
  390px; map and controls remained in bounds and separate.
- Conditional chip borders, orthogonal connectors and original cell contents
  remained present in captures. No cell component code changed.
- No warnings or errors in this verification tab. Temporary viewport restored,
  temporary tab closed, minimap preference returned to its initial hidden state.

Typecheck, frontend lint (457 files), design/contrast/control checks and targeted
pre-commit checks passed. Full unit/backend suites were not run for this UI-only
change. Pinch support is enabled through the existing React Flow implementation;
physical touch/trackpad pinch was not exercised by browser automation. Dark-theme
browser captures only; static contrast checks covered both themes.

Captures in `.impeccable/review/`:

- `workshop-navigation-desktop.jpg` — 1280×720, Fit View and visible minimap.
- `workshop-navigation-mobile.jpg` — 390×844, natural-size source and visible minimap.

## Inline finish review

Disposition: ship. Review performed inline, without a subagent, for this narrow
user-pinned extension. Captures were visually inspected; no comp or new visual
world applies.

### Persistence

Pass: PRODUCT.md and DESIGN.md describe the new navigation. Guidance and pipeline
documentation no longer prohibit user-selected zoom. Existing tokens unchanged.

### Fidelity

Type, material and ground: match the existing Console. Cells and connectors:
unchanged. Zoom controls, minimap and toggle: match the Agent trajectory treatment.
Map and controls stay usable on desktop and mobile. Full-size initial view retained.

### Ceiling

Reached for the requested navigation extension; no cell redesign or new controls
beyond zoom, Fit View, existing Reset and the map toggle.

### Material fixes

None outstanding within the exercised scope. Physical pinch remains unverified.

### Keep

Keep natural cell dimensions, downward branch layout and scale-1 entry/reset.

## Inline documenter

Compared CellFlow, TrajectoryFlowTab, existing Button, PRODUCT.md and DESIGN.md.
Updated only Workshop behavior descriptions, not the design system or sidecar.

- Palette: existing semantic card, accent, border and muted-foreground tokens.
- Type: existing Console face and control sizing.
- Depth: existing flat borders; no new shadows.
- Geometry: existing small radii and 20px node grid.
- Rule: zoom changes the camera, not cell dimensions or transformation lineage.

No new token family or primitive. Pre-existing typography wording in the frontend
skill was not repaired or promoted as part of this scope.
