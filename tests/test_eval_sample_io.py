import asyncio
import json
from copy import deepcopy

import pytest
from conftest import frozen_dataset
from mcp.shared.exceptions import McpError

from overbae.api.eval_serializers import EvalSampleSerializer
from overbae.models import APIToken, EvalRun, EvalSample, EvalVariant, Project, User
from overbae.services.datasets import rows
from overbae.services.eval import normalizer, runner
from overbae.services.eval.sample_io import sample_io
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.resources import read_resource

pytestmark = pytest.mark.django_db


@pytest.fixture
def sample():
    project = Project.objects.create(name="Sample IO", slug="sample-io")
    dataset = frozen_dataset(
        project, [{"input": {"documents": ["evidence"]}, "expected_output": "reference"}]
    )
    run = EvalRun.objects.create(project=project, dataset=dataset, cell=dataset.active_cell)
    variant = EvalVariant.objects.create(run=run, label="model", mode="generate")
    return EvalSample.objects.create(run=run, variant=variant, row_index=0, expected="reference")


@pytest.mark.parametrize("generate", [runner.run_capability, runner.generate_decision])
def test_request_capture_includes_injected_system_and_excludes_generated_output(
    monkeypatch, generate
):
    calls = []

    def call(**kwargs):
        calls.append(deepcopy(kwargs))
        return "actual answer", {}

    monkeypatch.setattr(runner, "call_llm", call)
    result = generate(
        input_messages=[{"role": "user", "content": "evidence"}],
        system_prompt="canonical prompt",
        tool_provider=runner.ReplayToolProvider(),
    )
    assert result.request["messages"] == calls[0]["messages"]
    assert result.request["messages"][0] == {"role": "system", "content": "canonical prompt"}
    assert len(result.request["messages"]) == 2
    assert result.output_messages[0]["content"] == "actual answer"


def test_request_capture_preserves_tool_schema(monkeypatch):
    monkeypatch.setattr(runner, "call_llm", lambda **_: ("answer", {}))
    tools = [{"name": "read_document", "description": "Read", "parameters": {"type": "object"}}]
    provider = runner.ReplayToolProvider(tool_defs=tools)
    result = runner.run_capability(input_messages=[], tool_provider=provider)
    assert result.request["tools"] == provider.tool_definitions()


def test_sample_io_keeps_request_response_and_reference_distinct(sample):
    request = {
        "messages": [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "evidence"},
        ],
        "tools": [],
    }
    sample.trajectory = normalizer.normalize_generation(
        input_value=[{"role": "user", "content": "evidence"}],
        output_messages=[{"role": "assistant", "content": "actual answer"}],
        request=request,
    )
    result = EvalSampleSerializer(sample).data["io"]
    assert result["input_source"] == "recorded"
    assert result["input"] == request
    assert result["output"] == "actual answer"
    assert result["output_messages"] == [{"role": "assistant", "content": "actual answer"}]
    assert result["reference"] == "reference"


def test_historical_sample_uses_pinned_row_not_combined_transcript(sample):
    sample.trajectory = {
        "messages": [{"role": "assistant", "content": "generated"}],
        "final_output": "generated",
    }
    result = sample_io(sample)
    assert result["input_source"] == "dataset"
    assert result["input"] == {"documents": ["evidence"]}
    assert result["output_messages"] == []
    assert result["output"] == "generated"


def test_missing_source_is_unavailable_not_inferred_from_assistant_messages(sample):
    sample.row_index = None
    sample.trajectory = {
        "messages": [{"role": "assistant", "content": "output"}],
        "final_output": "output",
    }
    assert sample_io(sample)["input_source"] == "unavailable"
    assert sample_io(sample)["input"] is None


def test_changed_pinned_frame_is_not_presented_as_historical_input(sample, monkeypatch):
    def changed(_cell):
        raise rows.RowStoreError("Fingerprint mismatch")

    monkeypatch.setattr(rows, "verify", changed)
    assert sample_io(sample)["input_source"] == "unavailable"


def test_empty_generation_does_not_reuse_an_assistant_turn_from_input():
    result = normalizer.normalize_generation(
        input_value=[{"role": "assistant", "content": "old answer"}], output_messages=[]
    )
    assert result["final_output"] == ""
    assert result["metadata"]["output_start"] == 1


def test_request_truncation_is_reported(sample, monkeypatch):
    monkeypatch.setattr(normalizer, "_MAX_MESSAGE_CHARS", 10)
    sample.trajectory = normalizer.normalize_generation(
        input_value=[],
        output_messages=[],
        request={"messages": [{"role": "user", "content": "a" * 50}]},
    )
    assert sample_io(sample)["truncated"] is True


@pytest.mark.django_db(transaction=True)
def test_mcp_run_samples_expose_bounded_io_and_enforce_project_scope(sample):
    other = Project.objects.create(name="Other", slug="other-io")
    token = APIToken(scope={"scope": "project", "permission": ["read"]})

    async def read(project):
        context = MCPContext(user=User(email="io@example.com"), token=token, project=project)
        with bind_context(context):
            contents = list(await read_resource(f"overmind://eval-runs/{sample.run_id}"))
            return json.loads(contents[0].content)

    result = asyncio.run(read(sample.run.project))
    assert result["samples"][0]["io"]["input"] == {"documents": ["evidence"]}
    assert result["samples_truncated"] is False
    EvalSample.objects.bulk_create(
        [EvalSample(run=sample.run, variant=sample.variant, row_index=i) for i in range(1, 6)]
    )
    result = asyncio.run(read(sample.run.project))
    assert len(result["samples"]) == 5
    assert result["sample_count"] == 6
    assert result["samples_truncated"] is True
    with pytest.raises(McpError):
        asyncio.run(read(other))
