# Workshop flow canvas — 2026-10-09

The overview-scale layout and review below are historical. The user's later
full-size, downward-layered correction supersedes them; current evidence and
review are in `conditional-workshop.md`.

## Direction contract

Mode: Operate; ordinary extension of the existing Console, not a visual redesign.

- THESIS: inspect the same step-by-step transformation cells spatially.
- OWN-WORLD: preserve current warm-neutral tokens, pixel typography, flat borders
  and cell internals exactly. No new palette or cell styling.
- STORY: dataset table → selected dataset → lineage connections → exact iteration.
- FIRST VIEWPORT: dataset name with a compact version chip; readable source cell;
  existing folder/outline access, canvas navigation, no run/version toolbar.
- FORM: user's pinned flow-diagram structure, Agent React Flow infrastructure,
  elbow connections and 20px grid. No concept roll or replacement-world comp:
  the user explicitly fixed the structure and forbade other cell UI changes.

## Repeatable fixture and environment

Use the existing local Compose API and Vite service, saved account connection,
signed-in Console, and the financial-services project. No new dev server or paid
provider operation. The fixture is synthetic and named accordingly.

```sh
PYTHONPATH=/Users/tyleredwards/Documents/GitHub/overmind/overmind \
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync python \
  tests/evidence/workshop_canvas_fixture.py \
  --project e18b29b5-915d-45a7-80cd-77ffe6559205
```

Observed dataset: `9b028cb6-a7d2-40e4-b22f-410954744e26`. Three original rows,
two true-filter rows and one false-filter row. Both outputs retain the same source
cell `a3bfd617-2936-4e4c-ac44-602c300d678d`. Existing user data was not changed.

Route: `http://localhost:5173/datasets/9b028cb6-a7d2-40e4-b22f-410954744e26?projectId=e18b29b5-915d-45a7-80cd-77ffe6559205`.

## Browser results

- Version chip sits immediately beside the name; old top bar is absent.
- Selecting 1.0 shows its source without changing active 1.2. Selecting 1.1 shows
  that branch and its source; Restore selects the exact cell without rerunning.
  Restored 1.1 was visibly active, then restored 1.2 and verified its Active label.
  All iterations restores the full three-cell view and removes the iteration URL parameter.
- Two elbow paths connect the source to the two outputs, labeled eligible=true
  and eligible=false; there is no false chain between sibling branches.
- Keyboard movement changed x=20 to x=40 and survived reload. Pointer movement
  resulted in x=80, y=120: coordinates remain multiples of the 20px grid.
- Folder button opened populated project navigation; collapse returned to the canvas.
- Source row search for Example B returned one match; export opened the existing
  CSV/JSONL dialog. Script expansion showed the retained filter JSON. Table cells,
  controls and copy remained in the existing NotebookCell implementation.
- At 390×844, the name truncates and version chip remains within the viewport;
  no horizontal page overflow. Canvas panning/zooming is separate from page layout.
  At 1280×720 and 1440×900, Fit View shows the entire fork; outline focus returns
  cells to a readable scale. Fit View is an overview, not a text-reading scale.
- Theme not changed: browser review used the existing dark theme. Both themes
  passed the static contrast checks; light-theme visual verification is not claimed.

Captures (absolute paths relative to the repository root):

- `.impeccable/review/workshop-flow-desktop.jpg` — 1440×900 overview, active 1.1.
- `.impeccable/review/workshop-flow-mobile.jpg` — 390×844 focused source.
- `.impeccable/review/workshop-flow-user-1280.jpg` — 1280×720 overview, restored 1.2.

## Inline finish review

disposition: ship

Review performed inline, not by a separate subagent. Captures were opened and
visually inspected. No approved replacement-world comp or quality card applies
to this user-pinned ordinary extension.

### persistence

Pass: PRODUCT.md records flow/version semantics; existing DESIGN.md is preserved.
The direction contract above records the narrow scope. All three captures exist,
show the target page and have the expected dimensions and surrounding shell.

### fidelity

| Element             | Finding                                                                                             |
| ------------------- | --------------------------------------------------------------------------------------------------- |
| TYPE                | Match: existing pixel UI face and cell hierarchy, no new display treatment.                         |
| MATERIAL            | Match: flat token surfaces, 1px boundaries, no invented physical effects.                           |
| GROUND              | Match: existing warm-black Console/card tokens retained.                                            |
| Cells               | Match: original content and controls; only nonvisual drag exclusions added.                         |
| Connections/grid    | Match: elbows from recorded lineage; grid-snapped positions.                                        |
| Version control     | Match: name-adjacent chip; selection separate from restore.                                         |
| Responsive overview | Adaptation: pan/zoom and focus retain unchanged cells; required by the user's spatial flow request. |

### ceiling

Reached for the requested extension. Decorative additions, new typography and
cell redesign would violate the brief. Overview zoom deliberately trades readable
table text for topology; direct outline focus restores a readable scale.

### material_fixes

None within the reviewed UI scope. Operational limitations remain listed in
`reusable-workshop-pipelines.md`; this visual disposition does not remove them.

### keep

Keep the cell UI unchanged and never invent graph edges or verified semantics.

## Inline documenter

No changes to DESIGN.md. Checked DESIGN.md, PRODUCT.md, styles.css, NotebookCell,
CellFlow and DatasetVersionChip; this implementation adds no reusable visual system.

Palette: existing warm-neutral semantic tokens; no new color values.
Type ramp: existing Geist Pixel controls/data and current Console typography.
Flat surfaces: existing 1px borders; no added shadow vocabulary.
Geometry: existing small radii; canvas positions snap to 20px without restyling cells.
Named rule: platform records facts; graph labels distinguish declarations from measurements.

Not canonized or repaired: unrelated frontend-skill typography wording predates
this work; the actual CSS/DESIGN.md remain authoritative. No broad design refresh.
