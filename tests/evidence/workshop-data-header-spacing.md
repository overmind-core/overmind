# Data-only cell title clearance

Verified 2026-10-09 against the existing local Console/API, signed into project
`e18b29b5-915d-45a7-80cd-77ffe6559205`. No dataset mutations or paid operations.

## Reproduce

Open
`http://localhost:5173/datasets/2340e11f-7fa3-47b2-b958-b100fbd955e3?projectId=e18b29b5-915d-45a7-80cd-77ffe6559205`.
The `Transfer verification: preserve pair fields` cell has 10,000 rows, three
columns and no preceding Source or Script section.

1. Inspect the Data button and title-chip bounds at 1280×720. Before the fix,
   the Data button began 9.5px above the chip's bottom. After the fix, it begins
   1.5px below it. The 24px chip and 1134px frame width are unchanged.
1. Compare the source cell: its 469px frame height and 46px chip-to-Data distance
   are unchanged. Collapse its Data section to bring the next cell into view.
1. Collapse and reopen the data-only cell. `aria-expanded` changes from true to
   false and back; the table returns.
1. At 390×844, the data-only cell still has 1.5px clearance. A pre-existing long
   title wraps inside its fixed-height chip at this width; this separate title
   overflow issue was not changed.
1. Inspect the two scripted cells in dataset
   `9b028cb6-a7d2-40e4-b22f-410954744e26` in the same project. Script headers retain
   their 32px height and 1px/1.5px clearance for inactive/active cells.

The frame now owns its 12px top inset. Source and Script no longer supply
duplicate spacers. Data-only cells do not draw an unnecessary section divider
under the title. Layout guidance kept dimensions and existing sections intact.

Temporary viewport overrides were reset and verification tabs closed. Captures:

- `.impeccable/review/workshop-data-clearance-desktop.jpg`
- `.impeccable/review/workshop-data-clearance-mobile.jpg`

## Checks

From `frontend/`:

```sh
bun run typecheck && bun run lint && bun run check:all
```

Passed TypeScript, Biome lint (459 files), design, contrast and control checks.
Dark-theme desktop/mobile browser verification; contrast checks cover both
themes. Full unit/backend suites skipped for this narrow spacing fix.
