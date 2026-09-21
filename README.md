<img width="6000" height="2000" alt="X Company Banner Stone (1)" src="https://github.com/user-attachments/assets/8ba6a64f-0819-47bd-9d58-af89ee3e7bad" />

# The Training Platform for Specialized Model

<p align="center">
  <a href="https://console.overmindlab.ai/">Console</a> | <a href="https://www.overmindlab.ai/">Site</a> | <a href="#run-it-yourself">Self-host</a>
</p>
<p align="center">
  <a href="https://docs.overmindlab.ai"><img src="https://img.shields.io/badge/Docs-docs.overmindlab.ai-ed670f?style=for-the-badge" alt="Documentation"></a>
  <a href="https://discord.gg/TPF722ZKuj"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://pypi.org/project/overmind/"><img src="https://img.shields.io/pypi/v/overmind?style=for-the-badge&label=PyPI&color=3b1b06" alt="PyPI"></a>
  <a href="https://github.com/overmind-core/overmind/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/overmind-core/overmind/ci.yml?style=for-the-badge&label=CI" alt="CI"></a>
</p>

**Overmind continuously trains & improves models you own, on data from your production traces**
Point it at your agent's codebase and it turns production traces (or any dataset) into a fine-tuned model, benchmarked against the eval metrics you define and served via 1 unified API, with no ML infrastructure to build.

> The weights are yours to download, retrain or roll back.

Available from the [Console](https://console.overmindlab.ai/), the `overmind` CLI, the [REST API](https://docs.overmindlab.ai/latest/platform/api.md), and an [MCP server](#connect-your-coding-agent) for Cursor, Claude Code, OpenCode and Codex. Hosted at [console.overmindlab.ai](https://console.overmindlab.ai/) or [run it yourself](#run-it-yourself).

<table>
<tr><td><b><a href="https://docs.overmindlab.ai/latest/core/capabilities.md">Agent & Capabilities</a></b></td><td>A graph of your agent — capabilities, prompts, tools, and tasks — scanned from the repo.</td></tr>
<tr><td><b><a href="https://docs.overmindlab.ai/latest/core/observability.md">Observability</a></b></td><td>OpenTelemetry traces, scored as they arrive and matched to the capability that produced them.</td></tr>
<tr><td><b><a href="https://docs.overmindlab.ai/latest/core/datasets.md">Datasets</a></b></td><td>Production traces or uploaded files become versioned eval and training datasets.</td></tr>
<tr><td><b><a href="https://docs.overmindlab.ai/latest/agent-testing/eval.md">Eval</a></b></td><td>What "good" means per capability, measured on live traces and in batch.</td></tr>
<tr><td><b><a href="https://docs.overmindlab.ai/latest/agent-testing/optimisers.md">Optimisers</a></b></td><td>Prompt, tool, and control-flow experiments in your repo; the winner is a git diff.</td></tr>
<tr><td><b><a href="https://docs.overmindlab.ai/latest/models/training.md">Models</a></b></td><td>Fine-tunes you own, trained on your data and benchmarked against production.</td></tr>
<tr><td><b><a href="https://docs.overmindlab.ai/latest/models/inference.md">Inference</a></b></td><td>Trained and frontier models on one OpenAI-compatible API.</td></tr>
</table>

<p align="center">
  <a href="https://youtu.be/m5V7Ox9OkrQ">
    <img width="9872" height="5543" alt="playframe" src="https://github.com/user-attachments/assets/246a21c5-e07a-4414-981e-f60d6308a729" />
  </a>
</p>

## Get started

### Hosted

Sign up at [console.overmindlab.ai](https://console.overmindlab.ai/), pick your coding agent on **Get started** — Cursor, Claude Code, OpenCode or Codex — and paste the onboarding prompt into it with your agent's repo open. It installs `overmind`, runs `overmind init` and `overmind sync`, and builds the context graph. From then on everything is a `/overmind` command in the same chat:

| Command                    | What it does                                  |
| -------------------------- | --------------------------------------------- |
| `/overmind ensure-tracing` | Inspect traces and instrument the agent       |
| `/overmind dataset`        | Build, clean, upload or export a dataset      |
| `/overmind finetune`       | Fine-tune, deploy and smoke-test a model      |
| `/overmind optimise`       | Run prompt and code optimisation              |
| `/overmind backtest`       | Compare models against the agent's own traces |

### Run it yourself

Self-hosting keeps traces and training data inside your own network. The hosted and self-hosted stacks are the same code.

```bash
git clone https://github.com/overmind-core/overmind.git && cd overmind
cp .env.example .env                        # OpenRouter, S3, a training backend (Modal or Baseten), an LLM key for the Data Workshop agent
docker compose up -d                        # Postgres, Redis, API on :8000, Celery workers, beat, Grafana on :3001
cd frontend && bun install && bun run dev   # Console on :5173
```

On first boot the API runs migrations and seeds the built-in evaluators; Swagger is at `/api/docs/`. `docker compose exec -T api python manage.py shell < seed.py` loads a full demo workspace.

<details>
<summary><b>What the API needs to boot</b></summary>

The API will not start without these. `.env.example` documents every other key.

| Group            | Variables                                                                                                                 |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Object storage   | `AWS_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — checkpoint archive                                      |
| Training backend | `FINETUNING_BACKEND=baseten` + `BASETEN_API_KEY`, or `FINETUNING_BACKEND=modal` + `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` |
| Serving          | `INFERENCE_API_URL` — the Modal vLLM endpoint printed by `modal deploy`                                                   |

Two keys gate features rather than boot: `OPENROUTER_API_KEY` for judges, evals and every routed model call, and one LLM key for the Data Workshop agent — it uses the first of `CURSOR_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` it finds. Without `STRIPE_SECRET_KEY`, usage is metered and shown with no remaining-credit cap.

</details>

### Send a first trace

```bash
pip install "overmind[tracing]"
export OVERMIND_API_KEY=ovr_…   # project key from Console → Settings; add OVERMIND_API_URL for self-host
```

```python
import overmind

overmind.init(
    service_name="support-agent", capability_id="<capability-uuid>", providers="auto"
)


@overmind.tool()
def search(query: str) -> list[dict]: ...


def handle(request: dict, session_id: str) -> dict:
    with overmind.run(
        "support-run", intent=request["question"], conversation_id=session_id
    ) as run:
        answer = agent(request)
        run.deliver(answer)  # the final output that gets scored
        return answer
```

`providers="auto"` instruments the LLM SDKs you already use over OpenTelemetry; without a key, tracing is off and nothing breaks. Any OTel exporter can `POST /api/v1/traces` instead, and existing traces in Langfuse, LangSmith, Braintrust or Galileo can be synced through a connector. Open **Observability → Task executions** to see the trace and its score.

## Connect your coding agent

Overmind ships an MCP server at `/api/mcp/`: 32 tools, 14 resources and 11 prompts covering everything the Console can do, scoped to one project by its API key. Every tool declares what it costs to run (`free`, `compute`, `llm`, `gpu`) and none can delete anything. `overmind init --ide <cursor|claude|opencode|codex>` writes the config for you, or by hand:

<details>
<summary><b>Cursor</b> — <code>.cursor/mcp.json</code></summary>

```json
{
  "mcpServers": {
    "overmind": {
      "url": "https://api.overmindlab.ai/api/mcp/",
      "headers": { "X-Api-Key": "ovr_…" }
    }
  }
}
```

</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add --transport http overmind https://api.overmindlab.ai/api/mcp/ --header "X-Api-Key: ovr_…"
```

</details>

<details>
<summary><b>OpenCode</b> — <code>opencode.json</code></summary>

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "overmind": {
      "type": "remote",
      "url": "https://api.overmindlab.ai/api/mcp/",
      "enabled": true,
      "headers": { "X-Api-Key": "ovr_…" }
    }
  }
}
```

</details>

<details>
<summary><b>Codex</b> — <code>.codex/config.toml</code></summary>

```toml
[mcp_servers.overmind]
url = "https://api.overmindlab.ai/api/mcp/"
http_headers = { "X-Api-Key" = "ovr_…" }
```

</details>

<details>
<summary><b>What the tools cover</b></summary>

| Domain          | Tools                                                                                                                                               |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Observability   | `inspect_capability_health`, `query_failures`, `query_traces`, `query_task_executions`, `get_job`                                                   |
| Datasets        | `list_datasets`, `inspect_dataset`, `query_dataset`, `create_dataset_from_traces`, `message_dataset_agent`, `run_dataset`                           |
| Evaluations     | `check_evaluation_readiness`, `upsert_evaluator`, `run_evaluation`, `compare_evaluations`, `annotate_evaluation_sample`                             |
| Training        | `check_finetune_readiness`, `estimate_finetune`, `start_finetune`, `retry_deployment`, `set_active_model`, `run_inference`, `get_model_swap_prompt` |
| Optimiser       | `check_optimizer_readiness`, `start_optimizer`, `inspect_optimizer_result`                                                                          |
| Connectors      | `inspect_connectors`, `configure_connector`, `sync_connector`                                                                                       |
| Instrumentation | `get_instrumentation_plan`, `verify_instrumentation`                                                                                                |
| Catalog         | `get_model_catalog`                                                                                                                                 |

Prompts such as `investigate-capability`, `finetune-capability` and `ship-model` chain the tools into complete workflows.

</details>

For a self-hosted instance, replace the host with your API URL (`http://localhost:8000` locally). Keys are written to git-ignored files only; `overmind sync` will not write a key into a tracked file.

## Documentation

All documentation lives at **[docs.overmindlab.ai](https://docs.overmindlab.ai)**:

| Section                                                                                    | What's covered                                                              |
| ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| [Quickstart](https://docs.overmindlab.ai/latest/quickstart.md)                             | Sign up, paste one prompt into your coding agent, run `/overmind` commands  |
| [Agent & Capabilities](https://docs.overmindlab.ai/latest/core/capabilities.md)            | The context graph: repo scans, capabilities, tasks, telemetry attribution   |
| [Observability](https://docs.overmindlab.ai/latest/core/observability.md)                  | OTLP ingest, span model, attribute mapping, the trace explorer              |
| [Python SDK](https://docs.overmindlab.ai/latest/tracing/sdk-python.md)                     | `init()`, auto-instrumentation, `run()`, decorators, tasks and capabilities |
| [Trace scoring](https://docs.overmindlab.ai/latest/agent-testing/trace-scoring.md)         | How a production trace becomes scored task executions and session scores    |
| [Datasets](https://docs.overmindlab.ai/latest/core/datasets.md)                            | Source, cells, versions, the data agent, trace-to-dataset                   |
| [Eval](https://docs.overmindlab.ai/latest/agent-testing/eval.md)                           | Evaluator kinds, eval sets, live scoring, eval runs                         |
| [Optimisers](https://docs.overmindlab.ai/latest/agent-testing/optimisers.md)               | The optimisation loop, the local executioner, the winning diff              |
| [Training](https://docs.overmindlab.ai/latest/models/training.md)                          | Dataset validation, model recommendations, loss curves, the benchmark       |
| [Inference](https://docs.overmindlab.ai/latest/models/inference.md)                        | Serving lifecycle and `/api/v1/chat/completions`                            |
| [REST API](https://docs.overmindlab.ai/latest/platform/api.md)                             | Auth, endpoint map, conventions, Swagger                                    |
| [Projects & Administration](https://docs.overmindlab.ai/latest/platform/administration.md) | Projects, API keys, connectors, jobs, billing                               |
| [Glossary](https://docs.overmindlab.ai/latest/platform/glossary.md)                        | Terms as they appear in the Console and the API                             |

______________________________________________________________________

## Repository

```
overbae/      Django 6 API — api/ (DRF, OTLP, OpenAI-compatible), models/, services/ (eval, datasets, mcp, sft_assets), tasks/ (Celery), modal/ (GPU workers)
frontend/     React 19 Console — src/openapi/ is generated by `make generate_api_client`, never hand-edited
overmind/     Python SDK + CLI, published to PyPI as `overmind`; skills/overmind/ is the /overmind skill
tests/        pytest — `make test`
AGENTS.md     how we work, for humans and coding agents; .claude/skills/ documents each subsystem
```

Backend is **uv** (`make test`, `make lint-backend`, `make check-migrations`); frontend is **Bun** (`bun run typecheck`, `bun run lint`, `bun run test`); SDK is `make -C overmind test`. Training and serving run on Modal or Baseten (`FINETUNING_BACKEND`); inference is vLLM behind `/api/v1/chat/completions`.

______________________________________________________________________

## Contributing

Open an [issue](https://github.com/overmind-core/overmind/issues/new), or a PR from a feature branch using `.github/PULL_REQUEST_TEMPLATE.md` — `main` is protected and `AGENTS.md` describes how we work. Questions go to the [Discord](https://discord.gg/TPF722ZKuj).

______________________________________________________________________

## Telemetry

The SDK and CLI send anonymous usage analytics to PostHog — one `cli.invoked` event per CLI run and `sdk_init` on library use; never prompts, trace contents, keys or dataset contents. Opt out with `OVERMIND_ANALYTICS_ENABLED=false` or `DO_NOT_TRACK=1`; analytics is also off when `CI` is set. Your traces go only to your own project.

<p align="center">
  <img alt="Overmind" src="frontend/src/assets/overmind-eye-copper.svg" width="96">
</p>

<p align="center">
  <a href="https://docs.overmindlab.ai">docs.overmindlab.ai</a> · <a href="https://www.overmindlab.ai/">overmindlab.ai</a>
</p>
