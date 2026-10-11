# Workshop script inspection

Failure cases to cover before implementation:

- An entrypoint hides meaningful logic in a retained helper that cannot be opened.
- Source inspection executes package code, leaks another project's files, accepts traversal,
  silently truncates Unicode content, or reads bytes whose checksum has changed.
- A cell claims platform execution based only on editable/unverified review text or borrows
  a different run's package; external imports and old unattributed cells look executed.
- Revision changes rewrite historical scripts or change labels, flags, groups or outputs.
- Loading/error states leave the file selector showing the previous file's contents.

## Implementation and scope

MCP contract 5.5 and REST now share cell transformation attribution and retained
package file inspection. Attribution requires completed publication membership and
matching output fingerprints; a copied review claim remains unrecorded. File reads
verify the retained ZIP digest, manifest and inventory, then return exact UTF-8
source with character offsets and file SHA-256. No source code executes on read.

The existing cell Script section adds a package-file picker, revision and execution
label. External imports explicitly state that Overmind has not verified execution.
No cell frame, rows grid or canvas layout was redesigned. Historical scripts remain
unchanged. The opaque fixture dispatcher was replaced by visible per-step scripts
and a shared JSON I/O / hashing module.

## Local environment

- Existing Docker Compose API, database and restricted Workshop runner; no new dev servers.
- Local MCP at `http://localhost:8000/api/mcp/`, Console at `http://localhost:5173`.
- Installed CLI `/Users/tyleredwards/.local/bin/overmind`, saved address-bound connection.
- Runtime `sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea`.
- Project `e18b29b5-915d-45a7-80cd-77ffe6559205`; all platform mutations used MCP,
  local bytes used the installed CLI. No training, evaluation or paid provider calls.

## Repeatable verification

From the repository root:

```sh
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync pytest tests/test_workshop_package_transfer_journey.py tests/test_reusable_workshop_journey.py tests/test_mcp_resources.py tests/test_mcp_datasets.py tests/test_mcp_prompts.py tests/test_mcp_catalog.py -q --tb=short
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_converged_journey.py --pipeline 08f8c60a-e3e6-4493-bb5c-2d42cc9e2a40 --run visible-scripts-repeat --repeats 2 --report /private/tmp/workshop-visible-scripts-repeat.json
UV_CACHE_DIR=/private/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_real_journeys.py --run visible-scripts-repeat --cli /Users/tyleredwards/.local/bin/overmind --repeats 1 --only documents tabular chat
```

Change `--run` for new executions; repeating a request key recovers its existing
receipt. The real-data commands require host access to the local API. The initial
sandbox connection denial was resolved using scoped host execution, without changing
credentials, endpoints or sandbox policy.

From `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
bun run test --run src/components/datasets/notebook/flow.test.ts src/components/datasets/notebook/cell-motion.test.tsx src/components/datasets/notebook/diff.test.ts
```

## Observed results — 2026-10-09

- Backend: **99 passed, 3 skipped**. Skips are optional isolated-Docker tests requiring
  `WORKSHOP_TEST_IMAGE`; live MCP runs below did execute in the local restricted runner.
- New acceptance checks cover REST/MCP source parity, Unicode pagination, traversal,
  missing files, invalid bounds, cross-project denial, package checksum corruption,
  external attribution and copied/unverified execution claims.
- Frontend: typecheck, lint, design, contrast and controls checks pass; **12 tests pass**.
- Production build passes (existing large-chunk warning); scoped pre-commit hooks and
  `git diff --check` pass. The protected local skill's hook required scoped host access.
- Final connected catalogue: 67 tools, contract 5.5.0, SHA-256
  `f132b4f8ad72f324f108b2e4db88c912127eeb3a0eee6b29858acb97ff4c16e4`.
- API client regenerated with the existing generator. Its existing schema warnings
  remain; generated types compile.
- Browser: opened the audit entrypoint, the file inventory, shared helper and manifest
  from the cell. Manifest JSON parsed and exposed all four tabular steps. No browser
  console errors observed. Browser work was inspection only; no platform data mutations.

| Live source                    | Result                                                                    | Measured duration                    |
| ------------------------------ | ------------------------------------------------------------------------- | ------------------------------------ |
| Entity pairs, 10,000 rows      | Preview plus two full publications; all row values match the prior output | 20.4s preview; 50.6s / 46.1s publish |
| PDF-derived evidence, 320 rows | Seven pages; exact page text and complete source-row coverage             | 37.2s end-to-end                     |
| Titanic tabular data, 891 rows | 891 records; labels and missing values preserved                          | 48.6s end-to-end                     |
| Chat data, 1,000 records       | 1,000 records; exact original messages preserved                          | 37.4s end-to-end                     |

Output comparisons exclude renewed `_overmind_` lineage fields: each publication
has its own receipt and parent cell identities, while original data values remain equal.

Main dataset `dece0a89-ab26-4293-aa3a-b292d3a278c9` now uses revision **6**,
`08f8c60a-e3e6-4493-bb5c-2d42cc9e2a40`, with active cell
`7892ba81-dd38-403c-8236-a6cd4033e6fb` (version 1.36). Both branches feed the final
message-construction script. Labels remain 7,690 positive / 2,310 negative;
all 119 review flags remain unresolved and visible.

Machine-readable receipts: [main repetitions](workshop-visible-scripts-results.json)
and [other data types](workshop-real-visible-scripts.json). Their commands independently
compare values, branch membership, source coverage and retained-file checksums, not
just successful tool responses. All retained package files were read through MCP in
the varied-data journey, and every published cell's script and package binding checked.

## Boundaries

These are technical and reproducibility checks, not independent semantic review or
fresh-agent usability experiments. The PDF case reused already extracted evidence;
it did not retest PDF upload/OCR. No model training was launched. Entity matching is
not a full KYC approval task. Inspectability does not resolve questionable labels or
prove training suitability. The sample recipes read rows into memory; arbitrary-size
streaming and external dependencies beyond the pinned runtime are not established.
