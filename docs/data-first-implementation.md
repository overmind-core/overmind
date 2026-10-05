# Data-first model workflows

Implementation scope: complete data-first entry, typed target semantics, saved data partitions, standalone multi-model decision evaluation, training experiment groups, checkpoint qualification/selection, and reproducible quality/performance reports. MCP and REST share services; Console uses generated contracts. Existing jobs and immutable experiment artefacts remain intact. No paid research runs or model activation are part of implementation validation.

## Failure cases established before implementation

- A mean-only rating is expanded into invented probabilities, or soft votes become a hard answer.
- Reference fields or provenance enter an inference prompt.
- Mixed probability/mean supervision produces incorrect gradients, NaNs from padded options, or misleading distribution metrics.
- A partition separates content duplicates, declared groups or generated descendants; repeats lose rows or change their assigned role.
- Another project's dataset, participant, job or protocol is accessible through REST, MCP or a resource.
- Repeating a request creates a second paid job, or a changed request key silently returns another recipe.
- A native evaluation requires a training job even for unchanged/external models.
- Provider acknowledgement is lost and the same request is issued again; completed predictions are overwritten on resume.
- Incomplete calibration fits a temperature or final predictions begin before the fit is frozen.
- Missing predictions disappear from denominators, incompatible cases are hidden, or a mean-only label gets an invented accuracy/CE score.
- Participant identity, input checksum, precision or runtime changes during a comparison without detection.
- Evaluation data is used to select a training checkpoint without being declared development data.
- Measured GPU/API spend is presented as a complete invoice; batch execution time is presented as per-request serving latency.
- Onboarding still requires a repository; the Console and MCP expose different lifecycle rules.
- Existing chat training/evaluation, native base/candidate plans, workshop proposal review or serving activation regress.

## Verification strategy

Write behaviour tests before changing the relevant implementation. Prefer a project-scoped API/MCP workflow covering dataset landing, preparation, group-preserving partitioning, standalone evaluation and experiment inspection. Mock the external transport/GPU boundary, not persistence or domain transitions. Isolated tensor tests cover real gradient/normalisation failures that an offline API test cannot exercise. Exercise resumed operations, project isolation, malformed inputs and old native/chat regressions.

Run affected backend tests while implementing, then platform regressions, migration consistency, backend lint, generated-client checks, frontend type/lint/tests/design checks and bounded desktop/mobile browser inspection. Record exact commands, environment and observed results in the final validation artefact. Provider/GPU execution that has not been performed remains explicitly unverified.

## MCP impact

Task contracts, partition plans, evaluation plans and training experiment groups are MCP-ready. File bytes remain CLI-guided. Presentation remains Console-specific but underlying metrics and runtime evidence are available through tools/resources. Destructive operations and automatic production activation are outside scope.

## Delivery record

Implemented the eight reusable product items from the saved product backlog:

- Data-first onboarding and dataset-agent exploration before preparation.
- Explicit per-family supervision meaning, full probability targets and mean-only ordinal targets.
- Immutable partition recipes with content/group/lineage preservation and explicit holdouts.
- Standalone native comparisons with optional calibration and multiple foundation, trained and external participants.
- Durable provider requests, response identity/rounding diagnostics and bounded technical recovery.
- Saved training experiments, explicit candidate variants and separate launch.
- Exact model qualification, retained checkpoints and development-only checkpoint selection.
- Shared quality reports and saved client-observed performance/cost workloads.

The Workshop interprets source evidence and the user task; deterministic consumers enforce the declared contract. Numeric shape does not establish label meaning. The historical Jev training and evaluation artifacts remain unchanged.

## Observed validation

Platform regression command: `.venv/bin/pytest tests/ -n 4 -q`. Result: 4,188 passed, 14 skipped, two failures in outdated MCP prompt fixtures. The new prompt changed the manifest count and required a task argument. Corrected those fixtures, then ran `.venv/bin/pytest tests/test_mcp_prompts.py tests/test_mcp_data_first.py -q`: all 29 passed. No unresolved test failures remain from this run. Test settings use SQLite and mocked provider/GPU boundaries; selected tensor tests additionally use a local CPU Torch environment.

Frontend: `bun run typecheck`, `bun run check`, `bun run check:all` passed; `bun run test` passed 894 tests in 117 files. Design, contrast and controls checks passed. The contrast check covers both themes. Browser inspection covered data-first onboarding, desktop comparison setup and desktop/mobile training experiment setup. No paid launch or project creation was clicked during UI checks.

Focused checks also passed: 47 MCP/workflow integration tests; 18 partition/experiment/split regressions; five performance recovery tests; 11 tensor/checkpoint/foundation tests; two analytics tests with loopback permissions; and the terminal-only demo fixture. These overlap the platform suite and are not additional unique test counts.

Migration consistency reports no model changes. A real migration-executor regression preserves historical completed native results through migrations 0019/0020. The uncommitted graph was separately checked: 0018 → 0019 → 0020, one leaf. The repository numbering script only inspected committed HEAD; its scope does not verify these two uncommitted files. Rebase and rerun the normal migration gate before publishing.

OpenAPI contracts were regenerated with `make generate_api_client`; generated files were not hand edited. SDK bundled documentation version is 0.1.84 in package metadata, module and lock; dependency versions were preserved. MCP tools, resources, contracts and the `develop-model-from-data` prompt are included. Celery topology and safe terminal demo behavior are covered by regressions. Relevant repository skills, AGENTS.md, PRODUCT.md and four sibling documentation pages were updated.

## Limits and remaining operational qualification

This validates local product behavior, persistence, recovery, scoring, tensor objectives and UI. It does not establish live GPU fit, cloud checkpoint continuity, latency or quality for the new larger foundations. Those require the authorized experiment qualification steps. The modified Modal worker source must be deployed through the existing product deployment process before a remote run uses it; a hot-reloaded API does not update baked worker assets. No model was activated, and no new training or benchmark GPU work was launched for implementation validation.

Performance costs distinguish recorded GPU/API amounts from unknown CPU, memory, storage and invoice totals. Recovered requests with unknown original timing do not gain invented latency. Provider-reported served versions do not independently verify weights. Calibration-only metrics are in-sample. Existing exposed benchmarks are regression evidence, not fresh confirmation.

Conditional distillation, auxiliary rationale training, RL, native distributed/full-weight training, cache optimization and independent human adjudication are research follow-ons, not claimed shipped features.

A repeatable validation record, logs, screenshots and inline design review are saved in the task artifact directory `data-first-validation/`. The new experiment authorization and friction comparison are saved separately under `jev-parity-rd/execution/`.
