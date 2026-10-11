# Workshop attached connectors — 2026-10-09

Scope: attach incoming and outgoing ports to the visible cell frame, preserving
cell dimensions, contents, branch layout, zoom controls and condition chips.

## Environment and checks

Use the existing local Compose/Vite services and signed-in Console. Synthetic
dataset `9b028cb6-a7d2-40e4-b22f-410954744e26`, project
`e18b29b5-915d-45a7-80cd-77ffe6559205`; no dataset or provider writes.

Route:
`http://localhost:5173/datasets/9b028cb6-a7d2-40e4-b22f-410954744e26?projectId=e18b29b5-915d-45a7-80cd-77ffe6559205`.

Before editing, measured all three cells at scale 1: incoming handle centers were
24px above the visible frame, outgoing centers 12px below it. Node width 1158px.

Failure cases to exercise in browser:

- Ports still bound to the padded outer node instead of the visible border.
- Edge paths retain stale coordinates after moving the ports.
- Expanding/collapsing cells leaves either endpoint behind.
- Zoom, grid movement or responsive resize detaches the ports.
- Cell width, controls, conditional chip borders or elbow routing change.
- The outer wrapper still paints over otherwise correctly positioned edges.
- Port centers miss the center of normal or active border strokes.
- Unconnected inputs/outputs show unused dots, including after version filtering.

Commands from `frontend/`: `bun run typecheck`, `bun run lint`,
`bun run check:all`. Inspect desktop/mobile screenshots and browser warnings.

## Results

Passed against the running app:

- Ports moved inside the bordered frame. Their centers are 0.5px inside normal
  1px borders and 0.75px inside the active 1.5px border: exactly the stroke centers.
- An intermediate screenshot exposed that correct coordinates alone were not
  enough: the padded node's opaque background still masked the lines. Moving the
  card fill to the visible frame leaves the padding transparent. The final desktop
  screenshot visibly confirms continuous paths into the ports.
- Both edge endpoints measured 0px from the corresponding port edge at scale 1.
  Collapsing the source retained 0px endpoint gaps; expanding restored its rows.
  Mobile Fit View at scale 0.25 retained 0px gaps and centered ports.
- Source shows only its outgoing port; both terminal branches show only their
  incoming port. Selecting version 1.0 rendered one node, zero edges and zero
  visible ports. All iterations restored the fork without changing active 1.2.
- Pointer drag moved the source from (1060,20) to (1100,40), remaining on the 20px
  grid and connected. The original position was restored afterward.
- Node widths remain 1158px at 1280×720 and 324px at 390×844; page width on mobile
  remains 390px. Cell contents, condition chips and square elbows are preserved.
- Typecheck, frontend lint, formatting and design/contrast/control checks passed.
  An initial formatter failure was corrected and the check rerun successfully.
- Two development hot-reload nodeTypes warnings were observed while editing;
  final clean load had no new warnings/errors and retained 0px endpoint gaps.
  No provider or dataset writes. Temporary viewport restored and test tab closed.

Full unit/backend suites and physical touch input were not run for this UI-only
correction. Browser captures use the existing dark theme; static contrast checks
covered both themes.

Captures in `.impeccable/review/`:

- `workshop-attached-connectors-desktop.jpg` — 1280×720, natural cell size.
- `workshop-attached-connectors-mobile.jpg` — 390×844, graph overview.

## Inline visual review

Disposition: ship. Review performed inline for the user's narrow refinement.
The supplied screenshots were the reference for the reported defects; the final
captures confirm attached lines and removal of unconnected dots.

Persistence: DESIGN.md records border-centered, connected-only ports and
transparent outer spacing. Fidelity: existing type, neutral surfaces, cell
dimensions, padding and controls remain; the card fill now follows its frame.
Ceiling: reached for the connector correction. Material fixes: none outstanding
in the verified scope. Keep: original cells, grid movement and lineage semantics.

Documentation preserves existing tokens and geometry; no new visual system or
MCP/API change. No unrelated design or product work included.
