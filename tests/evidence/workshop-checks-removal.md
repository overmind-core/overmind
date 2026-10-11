# Preparation-check feature removal

Scope: remove the user-facing cell findings/counts, findings write/read endpoints,
MCP tools and corresponding storage. Keep retained scripts, cell/branch geometry,
source extraction provenance, execution receipts and publication validation.
MCP impact: MCP-ready removal; retire the two tools and their result fields and
update prompts/resources/catalog identity with no compatibility shim.

Failure modes to verify before editing runtime code:

- Stale cell UI, generated API bindings or MCP descriptions still expose checks.
- Removing the shared finding service breaks normal dataset inspection.
- Removing the findings migration also removes automatic script retention.
- Removing the feature accidentally disables row/schema/consumer validation.
- Existing applied migrations become inconsistent or require rollback.

Verification: extend the existing Workshop journey to assert REST/MCP retirement
while uploading/publishing and reading the real retained-script process. Preserve
the existing script-association and correction-diff tests from the deleted
findings suite. Run existing invalid-target, atomic batch and partition journeys,
MCP contracts, generated-client checks, frontend checks and a local live audit.

## Observed result

- Deleted the check component/hook, finding serializers/service, two REST
  endpoints, two MCP tools and status fields. Regenerated the OpenAPI client.
- MCP contract is 7.0.0 because the two public tools and their fields were
  removed. Current prompts, server guidance, skills and sibling docs were updated.
- Applied `0038_remove_cell_preparation_findings` to the local development DB.
  Existing migration identities remain unchanged. Script-association fields,
  cells, sources, datasets and receipts remain intact.
- Backend regression run: **109 passed**, three Docker tests initially skipped
  because `WORKSHOP_TEST_IMAGE` was unset. Configured the existing runtime and
  ran those tests: **3 passed**. No skipped test remains from this run.
- Frontend typecheck, lint, design, contrast and controls checks passed. The
  notebook component suite passed all **3 tests**.
- Migration checks reported no model changes and validated 23 migrations,
  preserving three independently committed identities.
- Live MCP/API verification passed: retired tools absent and rejected, retired
  routes 404, fields omitted, four-cell process and exact 10,000-row output
  preserved. Correct preview completed; row-loss and invalid-target previews
  failed as expected without publication. See `workshop-checks-removal-live.json`.
- Automatic approval review rejected opening the Console because browser work
  was not explicitly requested. No visual/browser verification was performed or
  attempted through another route; verification used MCP/API and component tests.

No production deployment, training, model activation, commit or push occurred.

## Repeatable commands and inputs

Requires the existing local PostgreSQL/Redis/Docker deployment, installed CLI
account connection targeting localhost, and the pinned KYC audit dataset and
retained recipes recorded in the replay script. No provider credentials are
sent outside the local API by that script.

```bash
uv run --no-sync pytest tests/test_workshop_preparation_journey.py tests/test_reusable_workshop_journey.py tests/test_mcp_catalog.py tests/test_mcp_resources.py tests/test_mcp_prompts.py tests/test_mcp_result_compat.py tests/test_mcp_research_journey.py -q
WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea uv run --no-sync pytest tests/test_reusable_workshop_journey.py -k 'script_branch_declarations_have_code_evidence_and_measured_outputs or real_isolated_script_execution_reproduction_and_timeout' -q
make generate_api_client
make check-migrations
bun run --cwd frontend typecheck
bun run --cwd frontend lint
bun run --cwd frontend check:all
cd frontend && bun run test src/components/datasets/notebook/notebook-page.test.tsx
```

From the repository root, replay the live transport/runner verification:

```bash
PYTHONPATH=overmind uv run --no-sync python tests/evidence/workshop_checks_removal_replay.py
```

Stable request keys recover the same preview receipts; no output is published.
Original full KYC source and current output fingerprints are asserted by the
script. The actual valid preview run was
`6bd5eab6-6e69-4d99-9917-ba0c01146001`; row-loss control
`5a2b614a-5e61-4704-be5d-e4f3f64f93b5`; invalid-target control
`d7d20255-b932-47fc-aeaf-1ddeac75d6ee`.
