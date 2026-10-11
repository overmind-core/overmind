# OpenRouter model routing

Scope: foundation-model identity and pre-training benchmark routing only. Evaluator
validation and failure reporting are not changed.

Failure cases to exercise before implementation:

- A platform checkpoint name differs from its OpenRouter slug (Llama 3.1,
  Qwen 2.5, Liquid and Nemotron).
- An exact model is absent; a similarly named size, base/instruct variant or
  another publisher must not be substituted.
- A configured slug is removed, or the catalogue is unavailable. Neither is
  evidence that the model can receive requests.
- An unconfigured model subsequently appears with an exact checkpoint identity.
- A ready hosted base hides an available OpenRouter route before evaluation starts.
- A started evaluation or unresolved deployment is rerouted or duplicated.
- MCP and REST omit the mapping/status or disagree with actual routing.

The scheduler journey retains real database receipts and stubs only upstream
catalogue responses and paid dispatch. Live verification reads the public catalogue
and local MCP; it must not submit training, evaluation or inference.

## Verification — 9 October 2026, America/Los_Angeles

MCP-ready: the existing `get_model_catalog` tool and REST
`GET /api/finetuning-jobs/models/` share mapping and live-cached availability.
No new tools, UI controls, database migrations, provider jobs or Modal releases.
The generated OpenAPI client includes both fields. Existing chat-model registry
slugs are reused; all 53 foundation/backend entries now have an explicit mapping
field (null when no provider ID is known).

### Regression journey

Environment: repository `.venv`, `tests.settings`, temporary test database/storage,
offline OpenRouter responses and intercepted evaluation/GPU dispatch.

```sh
.venv/bin/pytest tests/test_finetuning_eval_schedule.py tests/test_mcp_model_catalog.py -k 'registered_identity or exact_mapping' -q
.venv/bin/pytest tests/test_finetuning_eval_schedule.py tests/test_model_catalog.py tests/test_mcp_model_catalog.py tests/test_finetuning_catalog.py tests/test_model_registry.py -q
.venv/bin/pytest tests/test_mcp_finetuning.py tests/test_mcp_manifest.py tests/test_mcp_catalog.py tests/test_mcp_result_compat.py -q
```

- Before implementation: all 10 new selected journey cases failed (no benchmark
  receipt for mapped aliases; missing MCP identity/status fields).
- After implementation: 147 catalogue/routing checks passed; 73 additional MCP
  checks passed. Existing test-secret JWT length warnings remain.
- Alias fixtures: Llama 3.1 Reference, Qwen 2.5 7B, Liquid 2.5 2.6B and Nemotron
  3.5 Lightning, both with and without a ready hosted base. The real scheduler
  persists the expected OpenRouter ModelRef and does not provision a base.
- A second scheduler pass after catalogue failure keeps the same receipt/route.
  Separate cases cover absent sizes, base/instruct mismatches, foreign publishers,
  private fine-tunes, batch-only listings, newly listed and renamed exact checkpoints,
  missing credentials and unresolved deployments.

Logs: `/tmp/overmind-openrouter-before.log`, `/tmp/overmind-openrouter-after.log`,
`/tmp/overmind-openrouter-mcp-regression.log`.

### Live local verification

`list_projects` confirmed `http://localhost:8000/api/mcp/`. `get_model_catalog({})`
returned all 40 Modal entries with both fields: 20 available, 20 not listed.
The public upstream was `https://openrouter.ai/api/v1/models`.

All-backend inspection checked the actual resolver against every registry row:
53 entries, 28 available, 25 not listed (backend duplicates included).
The retained result is [openrouter-live-catalog.json](openrouter-live-catalog.json).
These are catalogue matches, not paid inference or provider health probes.

```sh
docker compose exec -T api python manage.py shell -c '
from collections import Counter
from overbae.modal.model_registry import all_model_entries
from overbae.services.model_catalog import fetch_model_catalog, training_openrouter_match, resolve_training_openrouter_slug
catalog = fetch_model_catalog()
results = []
for model in all_model_entries():
    assert "openrouter_id" in model
    match = training_openrouter_match(model["id"], catalog=catalog)
    expected = match["openrouter_id"] if match["openrouter_status"] == "available" else None
    assert resolve_training_openrouter_slug(model["id"]) == expected
    results.append(match["openrouter_status"])
print(len(results), dict(Counter(results)))
'
```

Qwen3.5-9B, Llama3.1-8B, Liquid2.5-2.6B and Nemotron3.5-Lightning resolved to
their provider IDs. Qwen3.5-4B remained null/not_listed. Existing historical
Qwen3-8B mappings remained visible but not_listed, not silently assumed available.

### Build checks

`UV_CACHE_DIR=/tmp/overmind-uv-cache make generate_api_client` completed with Docker
access; the sandboxed attempt could not access the Docker socket. The generator
still reports unrelated serializer/schema diagnostics for completion, OTLP and
health endpoints. Those were not changed. `bun run typecheck` passed.
Scoped `pre-commit run --files ...` and `git diff --check` passed.
No paid inference, evaluation or training was launched. Evaluator validation and
the existing run's missing-checklist failure remain outside this change.
