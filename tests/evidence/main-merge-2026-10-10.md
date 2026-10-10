# Main integration verification

Merged `origin/main` at `3e3e9a05eec8aee93dde7e6f0097d0618390a80c` into
`codex/general-decision-training`, starting from
`c65491c2489f27f3043c684227db6160e6dfc6af`.

## Resolution decisions

- Retained native Unsloth decision training, full probability targets, native
  comparisons, development monitoring, retained Workshop packages and explicit
  preparation. The decision engine, native evaluation service, monitoring service
  and committed benchmark evidence are unchanged from the branch tip.
- Incorporated main's serving GPU floor, shared-base loading, volume refresh order,
  chat attention/packing corrections, bounded evaluation admission, dedicated
  landing capacity and centralized agent configuration.
- Combined source-import recovery with Workshop's immutable pipeline model.
  REST, MCP and Console resume the same stopped import receipt. Retired agent
  conversations, mutable cells and automatic preparation remain removed.
- Fixed a stale-reaper race, split cancellation leaving its partner stranded,
  CLI transfers missing an import receipt before broker dispatch, and a module
  import cycle exposed by serving's new GPU selection path.
- Preserved applied migration identities from both histories. Migration 0034 joins
  the graph and archives retired ownership/handoff metadata. Temporary-database
  upgrade tests cover both starting histories and retained cells/training results.
- Updated main's journey fixtures to use endpoint-bound CLI credentials, explicit
  retained-source transformations, stable training request keys and an exact
  catalogue-listed baseline. Both the training/activation journey and the
  trace-to-optimisation journey passed after these corrections.
- A combined journey run exposed delayed monitoring collection after its job had
  been deleted. A deterministic real-worker regression reproduced the crash;
  collection now stops when the job is absent. The same regression passed.
  The Modal fake also implements the asynchronous volume-reader interface.
- The 167 original untracked files were checked against pre-merge SHA-256 hashes;
  none changed. They are excluded from this commit.

## Reproduction and results

Use the pinned uv environment, Bun dependencies, local PostgreSQL and Redis, and
Tesseract/PDFium for document extraction. Tests use temporary databases and files;
external model/compute providers are faked. No GPU training or deployment was
launched for this merge. The existing real benchmark results remain retained.

| Command                                                                                         | Result                                                             |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `make test WORKERS=8 test_args='--no-cov'`                                                      | 4,327 passed, 13 skipped                                           |
| `make test-journeys test_args='--ignore=tests/journeys/console --no-cov'`                       | 41 passed, 2 existing expected failures                            |
| `cd frontend && bun run test`                                                                   | 873 passed                                                         |
| `cd frontend && bun run typecheck && bun run check:all`                                         | Passed                                                             |
| `make -C overmind test test_args='--ignore=tests/test_checkpoint_transfer_journey.py'`          | 585 passed                                                         |
| `make check-migrations`                                                                         | No model drift; historical migration identities accepted unchanged |
| `git diff --cached --name-only -z --diff-filter=ACMR \| xargs -0 uv run pre-commit run --files` | Passed                                                             |

The SDK exclusion is a pre-existing, untracked checkpoint experiment. A run that
included it failed its repository-free credential assertion; that unrelated file
was not modified. The standard SDK target already excludes its span test file.

The two expected journey failures already present on main concern trace-source
PII redaction and capability attribution for traces spanning multiple capabilities.
They remain outside this merge's scope.

The two Console browser journeys were not run. Automatic approval review rejected
them because repository instructions require explicit authorization for Console
UI work. The frontend component suite and static checks ran successfully.

Full backend logs: `/tmp/decision-main-full-final.log`. Frontend and SDK logs:
`/tmp/decision-main-frontend-tests.log`, `/tmp/decision-main-sdk-tracked-tests.log`.
The final journey log is `/tmp/decision-main-journeys-final-green.log`; per-test
receipts, fixture digests and environment facts are in
`tests/journeys/.runs/20261010T214721Z/`. Pre-commit results are in
`/tmp/decision-main-precommit-final.log`.
Migration upgrade fixtures live in `tests/test_source_import_migration.py`; the
numbering guard's real Git-history regression is `tests/test_migration_history_check.py`.
The missing-job worker regression is in `tests/journeys/test_periodic_tasks_recover.py`;
its failing and passing logs are `/tmp/decision-main-evidence-missing-red.log` and
`/tmp/decision-main-evidence-missing-green.log`.
