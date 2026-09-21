from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.cache import cache

from overbae.services.benchmarks.taxonomy import TaskType
from overbae.services.codebase import task_type


@pytest.fixture
def capability():
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        description="Route support requests to the appropriate team.",
        decision_logic="Choose the team from the request's subject.",
        policy_markdown="Return exactly one team.",
        model="",
        input_schema={},
        output_fields={},
        improvement_metadata={
            "system_prompt": "Classify the request into one support queue.",
            "capability_card": {
                "task": "Route requests to teams",
                "success_criteria": ["The assigned team owns the request"],
                "output_fields": {"team": "string"},
            },
        },
    )


@pytest.fixture
def llm(monkeypatch):
    cache.clear()
    call = Mock(return_value=('{"task_type":"classification"}', {}))
    monkeypatch.setattr(task_type, "call_llm", call)
    monkeypatch.setattr(task_type, "resolve_model", lambda _: "test-model")
    monkeypatch.setattr(task_type, "model_chain", lambda _: ["test-model"])
    return call


def test_classifies_the_recorded_task_and_prompt(capability, llm):
    assert task_type.classify_capability_task(capability) == TaskType.CLASSIFICATION

    evidence = json.loads(llm.call_args.args[0])
    assert "Route requests" in evidence["task"]
    assert "Classify the request" in evidence["system_prompt"]
    assert "owns the request" in evidence["success_criteria"]
    assert "Choose the team" in evidence["decision_logic"]
    assert "dataset" not in evidence


def test_reuses_classification_until_codebase_context_changes(capability, llm):
    assert task_type.classify_capability_task(capability) == TaskType.CLASSIFICATION
    capability.usage_stats = {"tool_calls": 1_000}
    assert task_type.classify_capability_task(capability) == TaskType.CLASSIFICATION
    llm.assert_called_once()

    capability.improvement_metadata["system_prompt"] = "Summarize the support request."
    llm.return_value = ('{"task_type":"summarization"}', {})
    assert task_type.classify_capability_task(capability) == TaskType.SUMMARIZATION
    assert llm.call_count == 2


def test_cache_is_scoped_to_the_selected_capability(capability, llm):
    task_type.classify_capability_task(capability)
    capability.id = uuid.uuid4()
    task_type.classify_capability_task(capability)
    capability.project_id = uuid.uuid4()
    task_type.classify_capability_task(capability)
    assert llm.call_count == 3


@pytest.mark.parametrize("value", TaskType.values)
def test_accepts_the_shared_task_taxonomy(capability, llm, value):
    llm.return_value = (json.dumps({"task_type": value}), {})
    assert task_type.classify_capability_task(capability) == value


@pytest.mark.parametrize(
    "response", ["not json", '{"task_type":"not_a_task"}', '{"task_type":"unknown"}']
)
def test_unresolved_context_stays_unknown(capability, llm, response):
    llm.return_value = (response, {})
    assert task_type.classify_capability_task(capability) == "unknown"


def test_provider_failure_is_temporarily_cached(capability, llm):
    llm.side_effect = RuntimeError("provider unavailable")
    assert task_type.classify_capability_task(capability) == "unknown"
    assert task_type.classify_capability_task(capability) == "unknown"
    llm.assert_called_once()


def test_no_recorded_context_does_not_call_an_llm(capability, llm):
    capability.improvement_metadata = {}
    capability.description = ""
    capability.decision_logic = ""
    capability.policy_markdown = ""
    capability.output_fields = {"label": "string"}
    assert task_type.classify_capability_task(capability) == "unknown"
    llm.assert_not_called()


def test_cache_outage_does_not_block_classification(capability, llm, monkeypatch):
    monkeypatch.setattr(
        task_type,
        "cache",
        Mock(get=Mock(side_effect=RuntimeError), set=Mock(side_effect=RuntimeError)),
    )
    assert task_type.classify_capability_task(capability) == TaskType.CLASSIFICATION


def test_large_schema_cannot_displace_task_evidence(capability, llm):
    capability.input_schema = {"field": "x" * 100_000}
    task_type.classify_capability_task(capability)
    evidence = json.loads(llm.call_args.args[0])
    assert len(evidence["input_schema"]) <= 4_000
    assert "Route requests" in evidence["task"]
    assert "owns the request" in evidence["success_criteria"]

    capability.input_schema["field"] += "changed"
    task_type.classify_capability_task(capability)
    assert llm.call_count == 2
