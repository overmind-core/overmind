# Workshop source chips — 2026-10-09

## Repeatable verification

Use the existing local Console and API, authenticated to the financial-services
project `e18b29b5-915d-45a7-80cd-77ffe6559205`. No datasets were changed.

1. Open dataset `a14e5398-f0a4-4e97-b80c-8575d6c2b318` from the dataset table.
1. Inspect the source cell at 1280×720 and 390×844.
1. Confirm one outlined file chip, the PDF filename, 320 rows and the original-file
   download button. No brief, checksum, extraction commentary or disclosure remains
   in the source summary. On mobile, the full filename remains in the title and
   download label while its visible text truncates.
1. Open derived fixture `39376f8c-cfe2-5740-b331-02ba66a85b91`; confirm the source
   chip reads `Derived dataset · 1.0`, not an invented file identity.

Observed: all four checks passed. The PDF section is 45px high on desktop, with a
28px chip. Mobile chip width 278px fits the 298px source section. Browser viewport
overrides were restored. Download handler and backend source records are unchanged;
this pass did not download another copy of the original document.

From `frontend/`: `bun run typecheck && bun run lint && bun run check:all` passed.
No unit/full-suite run for this scoped presentation change.

Screenshots: `.impeccable/review/workshop-source-chip-desktop.jpg` and
`.impeccable/review/workshop-source-chip-mobile.jpg`.

The UI simplification skill guided removal of the text section while retaining
source identity and the download action. The existing cell layout was preserved.
