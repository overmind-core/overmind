from __future__ import annotations

import re
import uuid

import pytest
from mcp import types
from mcp.shared.exceptions import McpError
from starlette.testclient import TestClient

from overbae.models import APIToken, Project, ProjectMembership, User
from overbae.services.mcp.prompts import PROMPTS, get_prompt, list_prompts
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db(transaction=True)

MCP_URL = "/api/mcp/"


def _token() -> str:
    user = User.objects.create_user(
        email=f"mcp-prompt-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="MCP prompts", slug=f"mcp-prompts-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    raw_key, _ = APIToken.create_for_user(user, project=project)
    return raw_key


def _rpc(method: str, params: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def _post(client: TestClient, raw_key: str, body: dict):
    return client.post(
        MCP_URL,
        json=body,
        headers={"X-Api-Key": raw_key, "Accept": "application/json"},
    )


def _arguments(prompt: types.Prompt) -> dict[str, str]:
    values = {
        "path": "rows.jsonl",
        "project_id": "project-id",
        "capability": "capability-id",
        "dataset": "dataset-id",
        "eval_set": "eval-set-id",
        "baseline": "baseline-run-id",
        "model_ids": "openai/model-a, anthropic/model-b",
        "deployment": "deployment-id",
        "finetune": "finetune-job-id",
        "connector_type": "langfuse",
    }
    return {argument.name: values.get(argument.name, "") for argument in prompt.arguments or []}


def test_lists_the_small_prompt_manifest_over_transport():
    raw_key = _token()

    with TestClient(create_mcp_application()) as client:
        response = _post(client, raw_key, _rpc("prompts/list"))

    assert response.status_code == 200
    assert {prompt["name"] for prompt in response.json()["result"]["prompts"]} == {
        prompt.name for prompt in PROMPTS
    }
    assert len(PROMPTS) == 12


def test_upload_prompt_requires_local_path_and_keeps_optional_shape():
    prompt = next(prompt for prompt in PROMPTS if prompt.name == "upload-dataset-file")
    arguments = {argument.name: argument for argument in prompt.as_mcp_prompt().arguments or []}

    assert arguments["path"].required is True
    assert arguments["intent"].required is False
    assert arguments["project_id"].required is False
    assert set(arguments) == {"path", "intent", "project_id"}
    rendered = get_prompt("upload-dataset-file", {"path": "rows.jsonl"})
    text = rendered.messages[0].content.text
    assert "`overmind dataset upload FILE --json`" in text
    assert "FILE with the exact local path supplied by MCP as one shell argument" in text
    assert "--intent train|eval" in text
    assert "--project-id" in text
    assert "get_job(kind=dataset_run)" in text
    assert "inspect_dataset" in text


def test_instrument_repository_supports_project_wide_and_scoped_workflows():
    prompt = next(prompt for prompt in PROMPTS if prompt.name == "instrument-repository")
    arguments = {argument.name: argument for argument in prompt.as_mcp_prompt().arguments or []}

    assert arguments["capability"].required is False
    project_wide = get_prompt("instrument-repository", {}).messages[0].content.text
    assert 'Capability filter: "".' in project_wide
    scoped = get_prompt("instrument-repository", {"capability": "support"})
    text = scoped.messages[0].content.text
    assert 'Capability filter: "support".' in text
    assert "Real run (recommended)" in text
    assert "Smoke run" in text
    assert "human_action or no placements" in text
    assert "version_analyzed_sha" in text
    assert "required_spans[].target.file" in text
    assert "contract_fingerprint" in text
    assert "required_identity" in text
    assert "Do not run either mode before approval" in text
    assert "exact command or input" in text
    assert "conversation.id" in text
    assert "query_traces(session=<correlation>, all_spans=false, limit=2)" in text
    assert "page.total == 1" in text
    assert "overmind://traces/{trace_id}" in text
    assert "truncated == false" in text
    assert "span_count == len(spans)" in text
    assert "supplied spans unchanged" in text
    assert "A real-run retry needs fresh approval" in text


def test_dataset_prompts_do_not_use_retired_workshop_language():
    dataset_prompts = {
        "prepare-evaluation",
        "evaluate-change",
        "finetune-capability",
        "optimize-capability",
        "compare-models",
        "upload-dataset-file",
        "export-dataset",
    }
    text = " ".join(prompt.template for prompt in PROMPTS if prompt.name in dataset_prompts)

    for retired in (
        "configure_dataset_build",
        "commit_dataset_build",
        "analyze_dataset",
        "stage_dataset_changes",
        "review_dataset_changes",
        "commit_dataset_changes",
        "dataset_inspect",
        "dataset_use",
        "job_status",
        "dataset_build",
        "--surface",
    ):
        assert retired not in text


def test_compare_models_prompt_forbids_diffs_and_names_openrouter_swap():
    text = (
        get_prompt(
            "compare-models",
            {
                "capability": "capability-id",
                "dataset": "dataset-id",
                "model_ids": "openai/gpt-5,anthropic/claude-sonnet-4",
            },
        )
        .messages[0]
        .content.text
    )
    assert "Do not write candidate diffs" in text
    assert "OPENROUTER_MODEL" in text
    assert "one iteration per model" in text
    assert "mode model_comparison" in text


def test_export_prompt_describes_local_download_and_trace_workflow():
    prompt = next(prompt for prompt in PROMPTS if prompt.name == "export-dataset")
    arguments = {argument.name: argument for argument in prompt.as_mcp_prompt().arguments or []}

    assert arguments["dataset"].required is True
    assert set(arguments) == {"dataset"}
    rendered = get_prompt("export-dataset", {"dataset": "dataset-id"})
    text = rendered.messages[0].content.text
    assert "`overmind dataset export DATASET --json`" in text
    assert "DATASET with the exact dataset id resolved through MCP as one shell argument" in text
    assert (
        "select traces, call create_dataset_from_traces, poll get_job with kind dataset_run, "
        "call inspect_dataset, then run the dataset export locally" in text
    )
    assert "no export_trace MCP tool" in text


def test_connector_prompt_asks_the_human_to_run_cli():
    prompt = next(prompt for prompt in PROMPTS if prompt.name == "connect-traces")
    arguments = {argument.name: argument for argument in prompt.as_mcp_prompt().arguments or []}

    assert arguments["connector_type"].required is True
    rendered = get_prompt("connect-traces", {"connector_type": "langfuse"})
    text = rendered.messages[0].content.text
    assert "`overmind connector add langfuse --json`" in text
    assert "Do not run the CLI yourself" in text
    assert "include_source_projects=true" in text
    assert "configure_connector" in text
    assert "confirm_mapping=true" in text
    assert "suggested_boundaries" in text
    assert "capability boundaries" in text
    assert "mapping_options" in text
    assert "wait for the human" in text
    assert "alternatives" in text
    assert "stop until the human replies" in text
    assert "console_traces_url" in text
    assert "sync_connector" in text
    assert "get_job(kind=connector_sync)" in text


def test_checkpoint_prompt_uses_mcp_deployment_id_and_local_cli_boundary():
    prompt = next(prompt for prompt in PROMPTS if prompt.name == "download-checkpoint")
    arguments = {argument.name: argument for argument in prompt.as_mcp_prompt().arguments or []}

    assert arguments["deployment"].required is True
    rendered = get_prompt("download-checkpoint", {"deployment": "deployment-id"})
    text = rendered.messages[0].content.text
    assert "overmind://deployments/deployment-id" in text
    assert "`overmind model download-checkpoint DEPLOYMENT --json`" in text
    assert (
        "DEPLOYMENT with the exact deployment id resolved through MCP as one shell argument" in text
    )
    assert "deployment id supplied by MCP" in text
    assert "presigned URL" in text
    assert "baseten or modal" in text
    assert "local `path`" in text
    assert "`bytes_written`" in text
    assert "refuses to overwrite" in text


def test_finetune_prompt_discovers_models_before_dataset_readiness():
    text = (
        get_prompt(
            "finetune-capability",
            {"capability": "capability-id", "dataset": "dataset-id"},
        )
        .messages[0]
        .content.text
    )

    assert text.index("get_model_catalog") < text.index("check_finetune_readiness")
    assert "dataset-independent model discovery" in text
    assert "dataset-specific narrowing and recommendations" in text
    assert "get_job using kind deployment" in text
    assert "run_inference only when it is ready" in text
    assert "deploy_model" not in text


def test_ship_prompt_uses_retry_only_for_failed_or_deleted_deployments():
    text = (
        get_prompt(
            "ship-model",
            {"capability": "capability-id", "deployment": "deployment-id", "finetune": "job-id"},
        )
        .messages[0]
        .content.text
    )

    assert "run_inference when it is ready" in text
    assert "retry_deployment only to recover a failed or deleted deployment" in text
    assert "deploy_model" not in text


@pytest.mark.parametrize(
    ("prompt_name", "argument", "command"),
    [
        ("upload-dataset-file", "$(touch pwned)", "overmind dataset upload FILE --json"),
        ("export-dataset", "$(touch pwned)", "overmind dataset export DATASET --json"),
        (
            "download-checkpoint",
            "$(touch pwned)",
            "overmind model download-checkpoint DEPLOYMENT --json",
        ),
        ("connect-traces", "$(touch pwned)", "overmind connector add langfuse --json"),
    ],
)
def test_untrusted_prompt_arguments_never_appear_in_command_snippets(
    prompt_name, argument, command
):
    argument_name = next(
        prompt_argument.name
        for prompt_argument in next(
            prompt for prompt in PROMPTS if prompt.name == prompt_name
        ).arguments
        if prompt_argument.required
    )
    text = get_prompt(prompt_name, {argument_name: argument}).messages[0].content.text

    assert command in text
    assert all(argument not in snippet for snippet in re.findall(r"`([^`]+)`", text))


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda prompt: prompt.name)
def test_gets_each_prompt_as_a_user_message(prompt):
    result = get_prompt(prompt.name, _arguments(prompt.as_mcp_prompt()))

    assert isinstance(result, types.GetPromptResult)
    assert len(result.messages) == 1
    assert result.messages[0].role == "user"
    assert result.messages[0].content.type == "text"
    assert prompt.name in {item.name for item in list_prompts()}
    assert result.messages[0].content.text


def test_unknown_prompt_returns_json_rpc_invalid_params():
    with pytest.raises(McpError) as error:
        get_prompt("not-a-prompt", {})

    assert error.value.error.code == -32602
