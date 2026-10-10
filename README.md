<img width="6000" height="2000" alt="X Company Banner Stone (1)" src="https://github.com/user-attachments/assets/8ba6a64f-0819-47bd-9d58-af89ee3e7bad" />

# The Training Platform for Specialized Agents

<p align="center">
  <a href="https://console.overmindlab.ai/">Console</a> | <a href="https://www.overmindlab.ai/">Site</a> | <a href="#run-it-yourself">Self-host</a>
</p>
<p align="center">
  <a href="https://docs.overmindlab.ai"><img src="https://img.shields.io/badge/Docs-ed670f?style=for-the-badge" alt="Documentation"></a>
  <a href="https://discord.gg/TPF722ZKuj"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://pypi.org/project/overmind/"><img src="https://img.shields.io/pypi/v/overmind?style=for-the-badge&label=PyPI&color=3b1b06" alt="PyPI"></a>
  <a href="https://github.com/overmind-core/overmind/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/overmind-core/overmind/ci.yml?style=for-the-badge&label=CI" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/Platform-AGPL--3.0-3b1b06?style=for-the-badge" alt="Platform AGPL-3.0"></a>
  <a href="overmind/LICENSE"><img src="https://img.shields.io/badge/SDK-MIT-3b1b06?style=for-the-badge" alt="SDK MIT"></a>
</p>

**Overmind continuously trains & improves your agents, with data from your production traces**

The SDK and CLI you install (`pip install overmind`) are MIT. The platform behind them is AGPL-3.0 and you can self-host it. Details in [Licence](#licence).

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
  <a href="https://youtu.be/DWC0BO48154">
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
cp .env.example .env    # set the required keys below
docker compose up -d    # Postgres, Redis, API on :8000, Console on :5173, Celery workers, beat, Grafana on :3001
```

Document uploads include local English OCR for scanned PDFs, embedded images and direct PNG/JPEG/WebP uploads. Direct images are capped at 100 MB and 64 megapixels; animated images are not supported. The Docker image includes Tesseract and its English/orientation data. For workers or backend tests outside Docker, install `tesseract-ocr`, `tesseract-ocr-eng` and `tesseract-ocr-osd` on Debian/Ubuntu, or `brew install tesseract` on macOS. No external OCR service is required.

On first boot the API runs migrations and seeds the built-in evaluators; Swagger is at `/api/docs/`. Sign in at `http://localhost:5173` with any email and password. `docker compose exec api python manage.py seed_demo --owner <your email>` loads a full demo workspace.

<details>
<summary><b>What the API needs to boot</b></summary>

The API refuses to start until every required key is set, and the error names each missing one. `.env.example` documents every key.

| Required                                                        | Used for                                                                              |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `OPENROUTER_API_KEY`                                            | Judges, evals, trace scoring, optimiser scoring, frontier inference                   |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`                          | Modal training and serving workers (set `MODAL_ENVIRONMENT` explicitly)               |
| `INFERENCE_API_URL`, `INFERENCE_API_KEY`                        | Serving trained models — the endpoint printed by `modal deploy` and its shared secret |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BUCKET_NAME` | The fine-tuning checkpoint archive                                                    |
| `HF_TOKEN`                                                      | Gated Hugging Face base models; also set it in the Modal secret                       |

| Optional            | Effect                                                                  |
| ------------------- | ----------------------------------------------------------------------- |
| `OPENAI_API_KEY`    | The `embedding_cosine` evaluator                                        |
| `STRIPE_SECRET_KEY` | Paid plans and the credit cap; without it, usage is metered with no cap |

Fine-tuning and serving also need the Modal workers deployed (`modal deploy overbae/modal/modal_vllm_worker.py`, `register_model.py` and `modal_sft_worker.py`) and a Modal secret named `overmind-inference` with the AWS keys, `INFERENCE_API_KEY` and `HF_TOKEN`.

Self-hosted login uses email and password. Keep Clerk and Stripe secrets blank.
Data Workshop transformations are authored by your native coding agent through MCP;
the platform stores source files, immutable cell versions and execution receipts.
There is no platform Workshop agent or ChatGPT account connection.
Reusable scripts, cross-source revisions and continuous bindings use the dedicated
[Workshop runtime and package protocol](docs/workshop-pipelines.md).
See [self-hosting](https://docs.overmindlab.ai/platform/self-hosting) for configuration
and private VM setup.

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

Overmind ships an MCP server at `/api/mcp/` with tools, resources and prompts for the platform's agent workflows. Account API keys and OAuth connections can access every active project their account is authorized to use: call `list_projects`, then pass the selected `project_id` on project tools and resource URIs. Membership is checked on every operation. Project API keys remain limited to their configured project. Every tool declares what it costs to run (`free`, `compute`, `llm`, `gpu`) and none can delete anything.

The [optional plugin](overmind/README.md#mcp-and-optional-plugins) packages the MCP connection, Overmind branding and nine workflow skills. OAuth connections remain authorized until revoked; access tokens expire after one hour and refresh tokens rotate. Direct MCP connections can use an API key without OAuth or a plugin.

`overmind init --ide <cursor|claude|opencode|codex>` prepares the local configuration and `overmind sync` installs the repository's project API key. To configure an API key by hand:

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

| Domain          | Tools                                                                                                                                                                                                                                        |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Projects        | `list_projects`                                                                                                                                                                                                                              |
| Observability   | `inspect_capability_health`, `query_failures`, `query_traces`, `query_task_executions`, `get_job`                                                                                                                                            |
| Datasets        | `list_datasets`, `inspect_dataset`, `query_dataset`, `create_dataset_from_traces`, `create_dataset_from_llm_calls`, `inspect_dataset_workbench`, `save_dataset_pipeline`, `run_dataset_pipeline`, `import_dataset_version`, `update_dataset` |
| Evaluations     | `check_evaluation_readiness`, `upsert_evaluator`, `run_evaluation`, `compare_evaluations`, `annotate_evaluation_sample`                                                                                                                      |
| Training        | `check_finetune_readiness`, `estimate_finetune`, `start_finetune`, `retry_deployment`, `set_active_model`, `run_inference`, `get_model_swap_prompt`                                                                                          |
| Optimiser       | `check_optimizer_readiness`, `start_optimizer`, `inspect_optimizer_result`                                                                                                                                                                   |
| Connectors      | `inspect_connectors`, `configure_connector`, `sync_connector`                                                                                                                                                                                |
| Instrumentation | `get_instrumentation_plan`, `verify_instrumentation`                                                                                                                                                                                         |
| Catalog         | `get_model_catalog`                                                                                                                                                                                                                          |

Prompts such as `investigate-capability`, `finetune-capability` and `ship-model` chain the tools into complete workflows.

</details>

For a self-hosted instance, replace the host with your API URL (`http://localhost:8000` locally). Keys are written to git-ignored files only; `overmind sync` will not write a key into a tracked file.

______________________________________________________________________

## Contributing

Open an [issue](https://github.com/overmind-core/overmind/issues/new), or a PR from a feature branch using `.github/PULL_REQUEST_TEMPLATE.md` — `main` is protected and `AGENTS.md` describes how we work. Questions go to the [Discord](https://discord.gg/TPF722ZKuj).

______________________________________________________________________

## Telemetry

The SDK and CLI send anonymous usage analytics to PostHog — one `cli.invoked` event per CLI run and `sdk_init` on library use; never prompts, trace contents, keys or dataset contents. Opt out with `OVERMIND_ANALYTICS_ENABLED=false` or `DO_NOT_TRACK=1`; analytics is also off when `CI` is set. Your traces go only to your own project.

______________________________________________________________________

## Licence

This repository contains two licences. The platform (`overbae/`, `frontend/`, and everything else outside `overmind/`) is [AGPL-3.0](LICENSE); the root `LICENSE` file is the verbatim AGPL-3.0 text. The SDK, CLI and client libraries under `overmind/` are [MIT](overmind/LICENSE). Copyright (c) 2026 Overmind Ltd. A commercial licence is a paid alternative from Overmind Ltd if you need different terms — [support@overmindlab.ai](mailto:support@overmindlab.ai).

<p align="center">
  <img alt="Overmind" src="frontend/src/assets/overmind-eye-copper.svg" width="96">
</p>

<p align="center">
  <a href="https://docs.overmindlab.ai">docs.overmindlab.ai</a> · <a href="https://www.overmindlab.ai/">overmindlab.ai</a>
</p>
