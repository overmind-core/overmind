# Workshop joined search and filter verification

Date: 2026-10-09

## Environment and fixture

Existing local Console on port 5173 and API running, authenticated in the
financial-services project. No new servers or dataset mutations.

Fixture: `canvas-verification-synthetic.jsonl`, with columns `item` and `eligible`:
Example A / true, Example B / false, Example C / true.

Open:
`http://localhost:5173/datasets/9b028cb6-a7d2-40e4-b22f-410954744e26?projectId=e18b29b5-915d-45a7-80cd-77ffe6559205`

## Repeatable UI checks and observed results

1. At 1280 × 720, inspect the source and both branch row toolbars. Search and
   Filter each measure 24px high with zero top-position difference. Borders
   overlap by 1px, forming a single seam with matching border colors. The filter
   icon remains unchanged.
1. Search the source for `Example B`. Only row 1, Example B / false, remains.
1. Clear search. Open Filter, select `item` / `contains`, enter `Example B`, and
   Add. The same single row remains, with a removable filter chip outside the
   connected control.
1. Remove the filter. All three source rows return.
1. At 390 × 844, the connected group fits the source cell at 237.38px wide.
   Both controls remain 24px high, with zero top-position difference and the
   same 1px border overlap.
1. Restore the browser viewport and close the temporary verification tab.

Before the fix, the inline input baseline made its wrapper 25.25px high, offset
the input from the button by 0.625px, and left a 6px gap between them. The fix is
scoped to the Workshop row toolbar; shared SearchInput and DataTable primitives
are unchanged.

Screenshots retained locally:

- `.impeccable/review/workshop-joined-search-desktop.jpg`
- `.impeccable/review/workshop-joined-search-mobile.jpg`

## Automated checks

From `frontend/`:

```sh
bun run typecheck && bun run lint && bun run check:all
```

Passed: TypeScript, Biome lint (459 files), design guardrails, contrast and
control conventions. Full/unit suites were not run for this scoped layout fix;
the real browser interactions above verify search and filter behavior.
