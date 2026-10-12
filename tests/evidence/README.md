# Local verification artifacts

Keep generated receipts, screenshots, model probes and one-off replay scripts in
this ignored directory or `scratch/`. They are not application code or automated
regression fixtures. Put executable regressions in `tests/test_*.py`,
`tests/journeys/`, or the frontend test suite; required data belongs in
`tests/fixtures/`.

Historical decision benchmark results and the frozen protocol are retained in
[the benchmark record](../../docs/decision-benchmarks.md). The original evidence
is recoverable from commit `63caac868ac9fc1cf6b66525f029e84e1b90a0de`; local files
were preserved under `scratch/branch-cleanup-2026-10-12/tests/evidence/`.

Run `make test`, `make test-journeys`, `bun run --cwd frontend test`, and
`make -C overmind test` for the maintained regression suites. Tests fake external
providers; passing them does not establish live GPU performance or model quality.
