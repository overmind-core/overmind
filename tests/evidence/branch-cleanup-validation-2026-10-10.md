# Branch cleanup and verification

Date: 2026-10-10 (America/Los_Angeles)

Branch: `codex/general-decision-training`. Starting commit: `53c4241`.
Verification used the existing local development stack. No production deployment,
main-branch push, training launch or provider inference was performed.

## Tracking

Product source, generated API models, migrations, reusable test scripts, browser
fixtures and verification records are tracked. Local `scratch/`,
`data_preparation/` and installed copies of the SDK's Overmind skills are ignored;
their files were preserved. Repository engineering skills and the SDK's canonical
skills remain tracked.

The two large MCP preparation audit receipts are tracked as deterministic gzip
files. Decompression was checked against the original bytes. Their original JSON,
the already-documented benchmark JSON and full BoolQ report remain local artifacts.
No added file exceeds the repository's 500 KiB limit. The staged files passed the
private-key hook, and the evidence JSON scan found no populated credential fields.

Formatter cleanup changed whitespace and quoting in four replay scripts; their
parsed Python ASTs were checked against the staged originals and were identical.

## Checks and observed results

From `frontend/`:

```sh
bun run test
bun run typecheck
bun run check:all
bun run build
```

- Frontend: 867 tests passed in 117 files.
- Typecheck, design, contrast and control checks passed.
- Production build passed. Vite retained its non-failing large-chunk warning.

From the repository root:

```sh
make test WORKERS=4
make -C overmind test WORKERS=2
make -C overmind lint-check
make check-migrations
uv run ruff check --no-fix
uv run pre-commit run
git diff --cached --check
```

- Platform: 4,317 passed, 10 skipped. No failures.
- SDK: 591 passed; lint and format checks passed.
- Django detected no missing model migrations; branch migration checks passed.
- Platform lint, staged-file hooks and whitespace checks passed after formatter
  cleanup.

Five environment-dependent skips were then rerun against Redis database 15 and
the already-built local Workshop container image:

```sh
TEST_REDIS_URL=redis://localhost:6379/15 \
WORKSHOP_REDIS_URL=redis://localhost:6379/15 \
WORKSHOP_TEST_IMAGE=sha256:0a0764841fcc41e7bb73a8cae6afe3ecd4237ac4edc2d2de023cfe57b6b1d0ea \
uv run pytest tests/test_task_lock.py \
  tests/test_reusable_workshop_journey.py::test_script_branch_declarations_have_code_evidence_and_measured_outputs \
  tests/test_reusable_workshop_journey.py::test_real_isolated_script_execution_reproduction_and_timeout \
  -q -rs
```

All five passed, covering Redis lease recovery and real isolated Workshop
execution, reproducibility, branch receipts, timeout cleanup and preservation of
published cells. The remaining five suite skips are guards for evaluator types
that are not compiled (`card-constraints` and `tool-vocabulary-selection`), in
`test_eval_api.py` and `test_eval_generation.py`.

Detailed Console browser verification is retained in the sibling evidence notes,
including `training-metric-edge-layout.md`, `graph-tooltip-split-rows.md`,
`dataset-ui-activation-removal.md` and `ui-full-width-dividers.md`.
