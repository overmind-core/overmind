# Generation throughput verification

The linked live generation repeated full source passages in model-generated input messages. Recent batches saved eight rows in 48–87 seconds; sampled user inputs contained 4,845–5,610 characters while answers contained 240–341 characters. Quotation mismatches rejected entire groups and caused repeated submissions. The final change generates the full target in one continuous session, saves ready rows without an exact group size, expands pinned source references and attaches evidence automatically. The pilot qualification path was removed. Review and supported repair follow publication.

## Failure cases identified before implementation

- A reference could bind to the wrong seed, silently lose whitespace, or leak an unresolved object into a training message.
- A missing or non-text source field, malformed prefix/suffix, or unknown reference key could silently produce different data.
- A failed reference could save the valid half of a batch.
- Retrying a compact submission could duplicate rows, or bypass duplicate detection against an equivalent expanded submission.
- Accepted examples could still resend entire source passages and preserve the output-copying bottleneck.
- Increasing row count could exceed the input budget or request more than the remaining target.
- A continuous session could start a new provider request for each save, count usage twice, lose a saved row on interruption, or keep requesting work after pause.
- Requiring generated quotations could reject otherwise readable source-bound examples before the final quality review.

The isolated service integration tests exercise these failure cases without fault injection into the live paid run. Live verification uses the existing generation and preserves all accepted batch files.

## Reproduction and results

Run from the repository root with the existing development/test environment:

```sh
.venv/bin/pytest tests/test_workshop_source_references.py tests/test_workshop_execution.py tests/test_dataset_agent.py tests/test_workshop_derivation.py tests/test_chatgpt_workshop.py -q
.venv/bin/python manage.py shell --settings=tests.settings < tests/evidence/workshop_continuous_generation_replay.py
UV_CACHE_DIR=/private/tmp/overmind-uv-cache .venv/bin/pre-commit run --files overbae/services/datasets/generation.py overbae/services/datasets/generation_worker.py overbae/services/datasets/notebook/agent.py overbae/services/datasets/notebook/engines/native.py overbae/services/datasets/notebook/prompts.py tests/test_workshop_execution.py tests/test_workshop_source_references.py tests/evidence/workshop_continuous_generation_replay.py
```

The replay refuses non-test settings or any database other than in-memory SQLite. It creates 90 controlled source passages in temporary storage, requests 450 rows, uses the real native engine tool loop with a controlled model transport and deliberately repeats one submission. It makes no real provider calls. Rows, files, publication, receipts and usage aggregation go through the generation services.

Observed replay result:

```json
{"rows":450,"generation_sessions":1,"controlled_model_responses":19,"replayed_submissions":1,"published_versions":1,"prompt_tokens_counted":1900,"source_preserved":true,"unresolved_references":0,"real_provider_calls":0}
```

The five-file regression run passed 160 tests. After the final automatic-evidence changes, the affected generation/derivation files passed all 56 tests. Existing JWT fixture key-length warnings remained. Pre-commit passed. Frontend progress verification and its 21 existing chat tests are recorded in `workshop-background-generation.md`.

The original live run (`f6cc6f4c-59b1-441d-ad88-5eee44ea9f3f`) was paused through the lifecycle service, allowing its active response to finish at 128 rows before worker edits. The initial compact-output trial returned ten source references, but quotation validation still rejected it; this was not a successful throughput measurement. Before the continuous workflow could be tried on that run, the dataset (`8abda57a-a667-4ebc-988a-379d953fc9de`) and all its runs were absent from the local database. No live speedup is claimed and the dataset was not recreated.

MCP impact: MCP-ready through the existing shared dataset workflow. Console and MCP start the same generation service, inspect the same saved counts and control the same revision-checked lifecycle. No public tool arguments, response schema, Celery routing, model schema or generated client changed. Agent tool descriptions, preparation prompts, repository guidance and sibling `docs/core/datasets.mdx` describe the continuous generation and final-review order.

## Local deployment verification — 2026-10-06

Environment: the existing `overmind-oss` Compose project, including its local override, local credentials and named data volumes. Deployment uses the current working checkout, including uncommitted changes. It does not pull or merge another Git branch.

```sh
docker compose build api
docker compose up -d --no-deps --force-recreate --wait --wait-timeout 180 api frontend
docker compose up -d --no-deps --force-recreate --wait --wait-timeout 180 celery-control-worker celery-io-worker celery-interactive-worker celery-beat
docker compose up -d --no-build --wait --wait-timeout 120 postgres redis grafana
docker compose exec -T api python manage.py migrate --check
docker compose exec -T api python manage.py check
docker compose exec -T api python -m pip check
docker compose ps
```

The build completed. API startup reported no migrations to apply; Django reported no system-check issues. API `/health`, Console `/` with its localhost Host header, and Grafana `/api/health` returned HTTP 200. Grafana reported its database healthy. The Console rendered the authenticated project and reported no browser console errors. Authenticated MCP `list_projects` returned the localhost connection and 59 tools; `get_job` retained the Titanic job's `succeeded` status. SHA-256 hashes for all five changed generation/agent Python files matched between the checkout and API container. The runtime exposes 300 generation rounds, no generation tool-result truncation, and no removed pilot-quality module.

The batch worker had active evaluations. It stopped accepting new work before replacement. Its auto-reloader restarted the worker after a remote warm shutdown, so the final replacement waited for both active and reserved task counts to reach zero. Both reached zero, then `docker compose up -d --no-deps --force-recreate --wait --wait-timeout 180 celery-batch-worker` completed. Automatic approval review rejected suspending the auto-reloader; that action was not performed.

All ten services were running after deployment. API, Console, PostgreSQL and Redis passed their configured health checks; Grafana passed its HTTP check. The API, four workers and scheduler all use rebuilt image `sha256:7b212b7ddc99147d56bc0143a0396ee80e859d4fb39263edda2ca27bd6df33ec`. The first worker ping caught batch startup and returned three workers, while the following queue inspection already showed all four workers and five queues. The repeated check passed: all four workers replied `pong`, and `control`, `io`, `io_traces`, `interactive` and `batch` were all consuming.

The runtime dependency check still reports three pre-existing conflicts: `clerk-backend-api 6.0.1` requires `cryptography<49` but the repository installs 50.0.0; `overmind 0.1.71` requires `rich>=14` but Truss installs 13.9.4; and `truss 0.18.32` requires `watchfiles<0.20` while the Dockerfile restores 1.3.0 for Uvicorn. These were not changed by this deployment. Application and MCP smoke checks passed, but this is not a clean dependency-compatibility result.
