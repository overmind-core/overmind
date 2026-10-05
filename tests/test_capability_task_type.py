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


class _Classifier:
    def __init__(self, fake_llm):
        self.fake_llm = fake_llm
        self.reply = '{"task_type":"classification"}'
        self.down = False

    @staticmethod
    def asked(request) -> bool:
        return request.system.startswith("Classify the task an AI capability")

    @property
    def calls(self) -> list:
        return [r for r in self.fake_llm.requests if self.asked(r)]

    def evidence(self, index: int = -1) -> dict:
        return json.loads(self.calls[index].messages[-1]["content"])


@pytest.fixture
def llm(fake_llm):
    cache.clear()
    classifier = _Classifier(fake_llm)
    fake_llm.fail(lambda r: classifier.down and classifier.asked(r), 400, "provider unavailable")
    fake_llm.on(classifier.asked, lambda r: classifier.reply)
    return classifier


def test_classifies_the_recorded_task_and_prompt(capability, llm):
    assert task_type.classify_capability_task(capability) == TaskType.CLASSIFICATION

    evidence = llm.evidence()
    assert "Route requests" in evidence["task"]
    assert "Classify the request" in evidence["system_prompt"]
    assert "owns the request" in evidence["success_criteria"]
    assert "Choose the team" in evidence["decision_logic"]
    assert "dataset" not in evidence


def test_reuses_classification_until_codebase_context_changes(capability, llm):
    assert task_type.classify_capability_task(capability) == TaskType.CLASSIFICATION
    capability.usage_stats = {"tool_calls": 1_000}
    assert task_type.classify_capability_task(capability) == TaskType.CLASSIFICATION
    assert len(llm.calls) == 1

    capability.improvement_metadata["system_prompt"] = "Summarize the support request."
    llm.reply = '{"task_type":"summarization"}'
    assert task_type.classify_capability_task(capability) == TaskType.SUMMARIZATION
    assert len(llm.calls) == 2


def test_cache_is_scoped_to_the_selected_capability(capability, llm):
    task_type.classify_capability_task(capability)
    capability.id = uuid.uuid4()
    task_type.classify_capability_task(capability)
    capability.project_id = uuid.uuid4()
    task_type.classify_capability_task(capability)
    assert len(llm.calls) == 3


@pytest.mark.parametrize("value", TaskType.values)
def test_accepts_the_shared_task_taxonomy(capability, llm, value):
    llm.reply = json.dumps({"task_type": value})
    assert task_type.classify_capability_task(capability) == value


@pytest.mark.parametrize(
    "response", ["not json", '{"task_type":"not_a_task"}', '{"task_type":"unknown"}']
)
def test_unresolved_context_stays_unknown(capability, llm, response):
    llm.reply = response
    assert task_type.classify_capability_task(capability) == "unknown"


def test_provider_failure_is_temporarily_cached(capability, llm):
    llm.down = True
    assert task_type.classify_capability_task(capability) == "unknown"
    assert task_type.classify_capability_task(capability) == "unknown"
    assert len(llm.calls) == 1


def test_no_recorded_context_does_not_call_an_llm(capability, llm):
    capability.improvement_metadata = {}
    capability.description = ""
    capability.decision_logic = ""
    capability.policy_markdown = ""
    capability.output_fields = {"label": "string"}
    assert task_type.classify_capability_task(capability) == "unknown"
    assert llm.calls == []


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
    evidence = llm.evidence()
    assert len(evidence["input_schema"]) <= 4_000
    assert "Route requests" in evidence["task"]
    assert "owns the request" in evidence["success_criteria"]

    capability.input_schema["field"] += "changed"
    task_type.classify_capability_task(capability)
    assert len(llm.calls) == 2
