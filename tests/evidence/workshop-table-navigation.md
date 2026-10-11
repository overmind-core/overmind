# Workshop dataset table and navigation

MCP impact: table layout and navigation are frontend-only; dataset state remains
available through the existing MCP catalogue. Upload prompt/resource guidance is
CLI-guided: native agents inspect record boundaries and verify landed data.

## Acceptance journey

Use the existing local Console and API, with an authenticated account and the
financial-services project. Do not launch providers or alter dataset contents.

1. Open `/datasets?projectId=e18b29b5-915d-45a7-80cd-77ffe6559205`.
   Expect a dataset table, count, search and purpose/capability filters;
   no chat-style prompt composer. Sidebar starts collapsed.
1. Search for `sample_10000.json`; verify 10,000 rows and source/version facts.
   Click its row and verify the existing source and transformation cells.
1. Open the folder button; expect only the selected project's datasets, compact
   rows, a selected-row indicator, search and pagination, without a separate header.
   On mobile, the close control sits beside search. Use the Datasets breadcrumb
   to return to the table.
   Close and reopen without resetting the notebook. Select another dataset.
1. Return to the table. Navigate to its last page, then apply a search/filter;
   results must reset to a valid page. Clear filters, sort and navigate again.
1. Verify no New dataset button appears in the listing or its empty state.
1. Inspect desktop and narrow layouts in light/dark themes. Check keyboard
   navigation and errors, empty results and long names without changing data.

Failure risks: a first-page-only fetch silently omits datasets; a later-page error
can masquerade as complete results; project changes can retain another project's
cached rows; filters can strand pagination; the desktop landing's forced sidebar
can consume table width; sidebar navigation can lose project context; shrinking
rows can hide keyboard focus or make touch actions unusable.

An isolated pagination test covers more than 100 datasets and a second-page
failure; the live project has fewer than 100, so that failure cannot be verified
by the existing live fixture alone.

## Observed results — 2026-10-08

Verified against the existing local stack using the in-app browser, without
creating, deleting or changing any dataset. The temporary light theme and
390 × 844 viewport were restored to the original dark theme and default viewport.

- Landing displayed all 64 datasets with search, filters and sortable facts;
  no prompt composer remained. The sidebar was collapsed.
- Page 3 was reachable. Searching `sample_10000.json` reset to page 1 of 1 and
  showed 10,000 rows, version 1.1, file source and pending purpose.
- Clicking the row and pressing Enter on the focused row both opened dataset
  `2340e11f-7fa3-47b2-b958-b100fbd955e3` with its two existing cells.
- Opening and closing the folder retained the notebook. Escape from sidebar
  search closed it. Escape while a tooltip is open first dismisses that tooltip.
- Sidebar loading reached 30, then 60, then all 64 unique dataset links. Every
  link carried the selected project ID. The active dataset had `aria-current`.
  Measured links were 28px on desktop and 36px on mobile, with no row gaps.
- Sidebar search and selection navigated to `KYCMCP Test` in the same project
  and collapsed navigation. All datasets returned to the table.
- An unmatched search showed No results, not an empty-project claim. Exploration
  filtering returned the existing exploration dataset and retained its URL filter.
  Row-count sorting put zero-row drafts first.
- Both creation menus opened their respective file/trace forms and were cancelled.
  `?create=true` opened once, consumed the parameter and stayed closed after reload.
- Desktop dark/light and narrow light layouts were inspected. Mobile navigation
  hid the mounted notebook and closing it restored the cells. Both cell table
  footers still reported 1–10 of 10,000 and page 1 of 1,000.

The live run caught a shared pagination bug: page 3 said 51–75 of 64. The range
now clamps to the total, and the same journey verified 51–64 of 64. The narrow
footer also wrapped words into a vertical stack; grouped wrapping now keeps
the count and controls readable. Both dataset table implementations use this footer.
These were the single batched corrections after the initial visual inspection.

The browser logged one transient Vite reload error while the old composer module
was being removed. A full reload displayed the new surface with no further error
entries. No browser/API authentication configuration was changed.

## Repeatable checks

From `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
bun run test src/hooks/project-datasets.test.ts \
  src/components/datasets/new-dataset-dialog.test.tsx \
  src/components/datasets/dataset-split.test.ts \
  src/components/datasets/notebook/diff.test.ts
bun run test src/components/ui/data-table.a11y.test.tsx \
  src/components/ui/data-table.empty-state.test.tsx \
  src/components/ui/data-table.fill.test.tsx \
  src/components/ui/data-table.visibility.test.tsx \
  src/components/ui/data-table.widths.test.ts
```

Observed: typecheck, lint and design/contrast/control checks passed. Frontend
suites passed 22 and 29 tests respectively (51 distinct tests). The isolated
130-dataset test was first run before implementation and failed because the new
fetcher did not exist; it passed after implementation. A test-only optional
argument type error was fixed before the successful typecheck.

From the repository root:

```sh
uv --cache-dir /tmp/overmind-mcp-audit-uv-cache run --no-sync pytest \
  tests/test_mcp_prompts.py tests/test_mcp_resources.py -q
.venv/bin/python /Users/tyleredwards/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  overmind/skills/overmind-datasets
```

Observed: 47 MCP tests passed; updated skill validated. The supported CLI skill
sync installs the updated dataset guidance. Targeted pre-commit checks passed.
The Impeccable layout detector returned no findings using its already installed
0.1.12 engine; the newly updated launcher's default engine was unavailable, and
no new runtime was installed.

Logs: `/tmp/workshop-table-tests.log`, `/tmp/workshop-shared-table-tests.log`,
`/tmp/workshop-table-mcp-tests.log`, `/tmp/workshop-table-typecheck-retest.log`,
`/tmp/workshop-table-lint.log`, `/tmp/workshop-table-design.log`, and
`/tmp/workshop-table-precommit.log`.

## Scope and limits

### Follow-up refinement — 2026-10-08

Removed the table's Status column, made all intent chips monochrome outlines,
and removed the sidebar's All datasets header. The notebook folder button remains;
mobile retains a close button beside search because the notebook is hidden.
The earlier All datasets observation above describes the initial implementation.

Repeated the acceptance journey's table-to-cell navigation and sidebar controls
against the same local project and dataset. The table rendered Name, Intent,
Version, Rows, Source, Capability and Updated, with no Status header. Pending,
Train, Eval and Data exploration chips were monochrome in light and dark themes.
Desktop sidebar began directly with search. At 390 × 844 there was one visible
close control beside search, and closing it restored the notebook and folder
button. The Datasets breadcrumb returned to the table. Browser error log was empty.
Theme and viewport were restored. No dataset contents were changed.

Repeated `bun run typecheck`, `bun run lint` and `bun run check:all` from
`frontend/`; all passed. Logs: `/tmp/workshop-refinement-typecheck.log`,
`/tmp/workshop-refinement-lint.log`, `/tmp/workshop-refinement-design.log`.
The Impeccable detector returned no findings using installed engine 0.1.12.
Unit/backend suites were not rerun for this presentation-only refinement.

### Listing creation button removal — 2026-10-08

Removed New dataset from the shared dataset toolbar and empty states, including
the capability's dataset tab. Deleted the now-unused button component and its
button-only test. Onboarding deep links, Observability trace imports and MCP
creation remain unchanged; the shared creation dialog remains for those callers.
The earlier button-opening observations describe the initial implementation.

Reopened the same project table in the local browser. Observed zero New dataset
buttons and a visible table of 64 datasets. Inspected the toolbar on desktop and
at 390 × 844, including light and dark themes; search and filters remained aligned.
Browser error log was empty. Restored theme and viewport; no data was changed.
The empty-project and capability views were checked in code, not with new fixtures.

Repeated `bun run typecheck`, `bun run lint`, and `bun run check:all` from
`frontend/`; all passed. The detector returned no findings. Unit/backend suites
were not rerun for the button removal. Logs:
`/tmp/workshop-remove-create-typecheck.log`, `/tmp/workshop-remove-create-lint.log`,
and `/tmp/workshop-remove-create-design.log`.

### Original scope

Subtitle verification on 2026-10-08: reopened the same project table and confirmed
“Source data and step-by-step transformations for training and evaluation.” beneath
Datasets using the shared page header. Inspected desktop and 390 × 844 layouts;
the subtitle wrapped to two lines on mobile without overlapping search. Restored
viewport; browser error log was empty. `bun run typecheck`, `bun run lint` and
`bun run check:all` passed (including both-theme contrast checks). Logs:
`/tmp/workshop-subtitle-typecheck.log`, `/tmp/workshop-subtitle-lint.log`,
`/tmp/workshop-subtitle-design.log`. Unit/backend suites skipped for this copy-only edit.

The native-agent upload prompt, resource and installed skill now require
pre-upload record inspection and post-landing count/value verification. This is
agent workflow guidance, not a new automatic parser or semantic quality gate.
The existing manual Console upload form is unchanged by that guidance.

The inventory follows every API page before client-side search/sort and does not
reuse another project's previous results. Pagination and mid-fetch failure are
covered beyond the live fixture size. This is not a large-project performance
benchmark or a transactional snapshot across concurrent API page changes.

Read-only UI checks did not deliberately interrupt the shared API to force its
error surface, nor create/delete test data for an empty project. Existing shared
table empty/error and accessibility tests cover those component boundaries.
