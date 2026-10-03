# Workshop local readiness

Checkout: `Wokshop-v3` at `6c3fcf3`, with the functional changes from merged PR
#139 applied locally. Docker Compose uses bind mounts from this checkout.

## Live ChatGPT stream failure

A synthetic `Reply with exactly OK.` request through the saved ChatGPT connection
returned an `OK` delta and a completed message in `response.output_item.done`,
followed by `response.completed` with `output: []`. The parser discarded the
completed output items. Log: `/private/tmp/workshop-stream-shape.log`.

Failure modes defined before the repair:

- Completed streamed text disappears from saved workshop chat or replay context.
- Completed streamed tool calls are dropped, so requested operations never run.
- A semantic audit cannot parse its streamed JSON answer.
- Collecting streamed items executes tools before successful terminal completion.
- Repeated output events duplicate calls or change their response order.

The existing connection → workshop tool → semantic audit → saved chat → zero
ledger integration test is parameterized with the observed provider stream.
Existing interrupted/failed/incomplete stream tests guard premature execution.

## Observed results

- Docker API `/health` and `/api/chatgpt/login/`: HTTP 200; local ChatGPT enabled.
- `manage.py check`: no issues; `migrate --check`: exit 0; four Celery nodes pong.
- Container document dependencies: docling-slim 2.131.0, pypdfium2 5.13.0,
  python-docx 1.2.0. No image rebuild needed.
- Browser: datasets load; Use ChatGPT toggles on, exposes five account models,
  and toggles off again. Original server-funding preference restored.
- Backend regression selection: 179 passed. SDK init/sync selection: 36 passed.
- Added provider-stream case failed before the repair (saved `Nothing to do.`)
  and passed after it. Related ChatGPT/agent/semantic/intent suite: 118 passed.
- Frontend `bun run typecheck` and `bun run check`: passed.
- Pre-commit passed on all changed files.
- The terminal CLI was still 0.1.73 with the old local Codex restriction. Installed
  this checkout editable with `uv tool install --force --editable ./overmind`.
  The real `overmind init --ide codex --env local` command now passes in a
  disposable directory and emits the localhost MCP URL.
- Set the ignored local frontend `VITE_SDK_EDITABLE_PATH` to this SDK checkout,
  restarted the existing frontend container and verified Vite serves the setting.
  New local onboarding instructions now install the fixed checkout.
- Live training replay: MCP start → PostgreSQL queued job → get_job passed.
  Only Celery dispatch was intercepted; the transaction was rolled back.
- Live ChatGPT replay: real account model executed rename, saved the answer,
  and recorded zero-charge usage. Synthetic rows only. Project, dataset and
  ledger rolled back, temporary files removed. Connection recycling is disabled
  only inside this harness to preserve the surrounding rollback transaction.

## Repeatable local commands

Requires the running local Docker Compose stack, an existing saved ChatGPT
connection, and the KYC dataset/evaluation fixtures named in the training script.
The ChatGPT check consumes a small amount of the connected plan allocation.
It does not use platform model credentials. Neither check launches GPU training.

```sh
docker compose exec -T api python manage.py shell < tests/evidence/workshop_training_replay.py
docker compose exec -T api python manage.py shell < tests/evidence/workshop_chatgpt_replay.py
.venv/bin/pytest tests/test_chatgpt_workshop.py tests/test_dataset_agent.py tests/test_workshop_semantic_checks.py tests/test_workshop_intent.py -q
```

Logs: `/private/tmp/workshop-training-live.log`,
`/private/tmp/workshop-chatgpt-e2e.log`, `/private/tmp/workshop-stream-before.log`,
`/private/tmp/workshop-stream-after.log`. Paid GPU execution and a fresh browser
OAuth consent were not exercised in this check. Login callbacks are covered by
the integration suite; the existing connection was verified against OpenAI live.
