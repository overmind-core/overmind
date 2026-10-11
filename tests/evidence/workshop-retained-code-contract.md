# Retained transformation code contract

Failure modes covered before implementation:

- MCP accepts a transformation without retaining executable code.
- REST silently ignores obsolete step definitions, including when a package is supplied.
- Historical package-free revisions remain runnable through validation, direct runs or bindings.
- A rejected request changes the active source, creates work, or dispatches execution.
- Historical recipes become unreadable or acquire misleading script-execution attribution.

Focused regression command (repository root; test dependencies installed):

```sh
DJANGO_SETTINGS_MODULE=tests.settings .venv/bin/pytest tests/test_workshop_retained_code_contract.py -q
```

The tests use an isolated SQLite database and temporary storage; no paid services.

## Result

Final combined run: **173 passed in 49.78 seconds**, with the restricted Docker
runtime enabled and no skipped cases. Log: `/tmp/workshop-retained-final.log`.
The live MCP journey and all-row comparison also passed.

The eight initial contract checks failed before the fix. The corrected contract
requires a retained package in MCP and REST, removes the in-process declarative
executor and rejects new work against historical package-free revisions. Existing
cells and receipts stay readable with their original attribution. The runner also
rejects already-queued package-free work instead of leaving it pending indefinitely.
External-result imports remain distinct from retained script execution.

The regression migration found and fixed an empty-input contract gap: required
input columns must exist even when the input has no rows. Tests retain coverage
for revisions, reuse, branch fan-in, identity overlap, schema mismatches, empty
branches, cancellations, failures, bindings, tenant isolation and exact consumer
versions. The small service journeys use an explicit trusted-fixture executor;
the Docker tests and live MCP run exercise actual restricted execution.

## Repeatable regression commands

Environment: repository `.venv`, local Docker socket, and the approved runtime image
already built. Test storage/database are isolated. No paid providers are invoked.

```sh
export DJANGO_SETTINGS_MODULE=tests.settings
export WORKSHOP_DOCKER_SOCKET=/Users/tyleredwards/.docker/run/docker.sock
export WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea
.venv/bin/pytest tests/test_workshop_retained_code_contract.py tests/test_workshop_redesign.py tests/test_workshop_mcp_acceptance.py tests/test_reusable_workshop_journey.py tests/test_dataset_gate.py tests/test_workshop_package_transfer_journey.py tests/test_mcp_catalog.py tests/test_mcp_prompts.py tests/test_mcp_resources.py tests/test_mcp_authorization.py tests/test_mcp_manifest.py tests/test_mcp_workflow_friction.py tests/test_mcp_datasets.py tests/test_reusable_workshop_migration.py -q
```

The Docker cases cover 10,000 and 100,000 rows, repeated identical-source execution,
conditional routing, restricted runtime configuration, and bounded timeouts.

Additional checks: `make generate_api_client`, `bun run typecheck` in `frontend`,
skill-creator validation for both changed shipped skills, and pre-commit on the
changed implementation, guidance and test files. Repository and global Codex
skill copies were synced and compared byte-for-byte against the shipped files.

## Live local MCP journey

```sh
.venv/bin/python tests/evidence/retained_contract_live.py /private/tmp/overmind-kyc-script.WwOfGW/source.jsonl /private/tmp/workshop-retained-live-results.json
```

Prerequisites: local services and Workshop runner, saved localhost account connection,
and an exported original KYC source with `tokens` and `kyc_risk_bucket` fields.
The harness checks the endpoint before writes, selects the financial-services
project, and creates a named disposable dataset from the first 200 real rows.
The script path is the retained three-stage package in `kyc_retained_pipeline/`.
No original dataset is modified and no training/evaluation/inference is launched.

Verified against MCP contract 6.0:

- Fresh `tools/list` requires `package` and has no inline `steps` input.
- Package-free save returns actionable upload guidance; historical validation fails
  with `pipeline_package_required` while historical inspection remains readable.
- CLI raw-data upload lands exactly 200 rows. Package upload/download verifies SHA-256.
- Three retained Python entrypoints register, validate and execute in containers.
- Preview processes 25 rows at each stage without creating cells.
- Publication processes all 200 rows at each stage, atomically creates three cells,
  and binds each to the retained package and exact execution receipt.
- Repeating either request key recovers the same run, without additional cells.
- Export comparison checks every row's text, target, identity and messages against
  the source. All 200 match; no new interpretation or target labels are introduced.

Measured runner times were 2.431 seconds for preview and 2.423 seconds for
publication; these are not whole-journey times or cold-start benchmarks.
Receipts: [workshop-retained-code-live-results.json](workshop-retained-code-live-results.json).

The first live harness attempts exposed two harness mistakes (a Python argument
name collision and an unsupported export `--project-id` flag). Both were corrected.
The earlier publication succeeded and remains a separate named test dataset;
the final complete journey passed, including export verification. These were not
platform failures and were not hidden by retrying an unresolved operation.

This verifies code retention and execution, not semantic correctness of arbitrary
agent-authored scripts. Raw source uploads and genuine external results remain
valid and do not claim reproducible transformation execution. Historical evidence
scripts targeting the retired inline-operation contract are not current acceptance
commands; use the commands above for this contract.
