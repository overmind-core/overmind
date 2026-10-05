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
