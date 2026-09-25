"""One row per llm_call, idle, with no trace reconstruction key."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from overbae.models import Capability, Dataset, Project, Span
from overbae.services.datasets import dispatch, paths, store
from overbae.services.datasets.land import LandError
from overbae.services.datasets.llm_calls import call_record, hash_split, shape
from overbae.services.finetuning_validator import validate_rows
from overbae.tasks import datasets as dataset_tasks

pytestmark = pytest.mark.django_db

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Weather for a city",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }
]


def _span(**fields):
    defaults = {
        "span_id": uuid.uuid4().hex[:16],
        "trace_id": uuid.uuid4().hex,
        "status_code": 0,
        "attributes": {},
        "usage": {},
        "capability_id": None,
    }
    defaults.update(fields)
    return SimpleNamespace(**defaults)


def _project():
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _call_span(project, capability, span_id, *, model="openai/gpt-5-mini", error=False, tool=False):
    if tool:
        request = {"messages": [{"role": "user", "content": "weather?"}], "tools": _TOOLS}
        completion = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                    }
                ],
            }
        ]
    else:
        request = [{"role": "user", "content": "question"}]
        completion = [{"role": "assistant", "content": "recorded"}]
    return Span.objects.create(
        span_id=span_id,
        trace_id=uuid.uuid4().hex,
        project=project,
        capability=capability,
        span_type="llm_call",
        name="llm_call",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
        status_code=2 if error else 0,
        attributes={
            "overmind.input.data": json.dumps(request),
            "overmind.output.data": json.dumps(completion),
            "genai.model": model,
        },
        usage={"genai.model": model},
    )


def _ids_for_split(percent=20):
    train, evaluation = [], []
    index = 0
    while len(train) < 2 or len(evaluation) < 2:
        span_id = f"{index:016x}"
        bucket = int(hashlib.sha256(span_id.encode()).hexdigest()[:8], 16) % 100
        (evaluation if bucket < percent else train).append(span_id)
        index += 1
    return train[:2], evaluation[:2]


def test_call_record_keeps_text_and_a_final_tool_call():
    text = call_record(
        _span(
            attributes={
                "overmind.input.data": json.dumps([{"role": "user", "content": "hi"}]),
                "overmind.output.data": json.dumps([{"role": "assistant", "content": "hello"}]),
            }
        )
    )
    assert text["expected_output"]["content"] == "hello"
    assert "trace_id" not in text
    assert "trace_id" not in shape([text], "eval")[0]

    tool = call_record(
        _span(
            attributes={
                "overmind.input.data": json.dumps(
                    {"messages": [{"role": "user", "content": "weather?"}], "tools": _TOOLS}
                ),
                "overmind.output.data": json.dumps(
                    [
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"city": "Paris"}',
                                    },
                                }
                            ],
                        }
                    ]
                ),
            }
        )
    )
    assert tool["expected_output"]["tool_calls"][0]["function"]["name"] == "get_weather"
    assert validate_rows([{"messages": tool["messages"], "tools": tool["tools"]}]).valid
    assert call_record(_span(status_code=2, attributes={"overmind.input.data": "[]"})) is None


def test_hash_split_is_stable_and_refuses_an_empty_side():
    records = [{"span_id": f"s{i}"} for i in range(40)]
    train, evaluation = hash_split(records, 20)
    assert [row["span_id"] for row in train] == [
        row["span_id"] for row in hash_split(records, 20)[0]
    ]
    assert train and evaluation
    one_side = []
    index = 0
    while len(one_side) < 3:
        span_id = f"only-{index}"
        bucket = int(hashlib.sha256(span_id.encode()).hexdigest()[:8], 16) % 100
        if bucket >= 20:
            one_side.append({"span_id": span_id})
        index += 1
    with pytest.raises(LandError):
        hash_split(one_side, 20)


def test_a_final_tool_call_trains_and_a_gap_does_not():
    tools = _TOOLS
    final = {
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                    }
                ],
            },
        ],
        "tools": tools,
    }
    assert validate_rows([final]).valid
    gap = {
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                    }
                ],
            },
            {"role": "user", "content": "and tomorrow?"},
            {"role": "assistant", "content": "I still need the city."},
        ],
        "tools": tools,
    }
    assert not validate_rows([gap]).valid


def test_landing_settles_idle_without_a_workshop_turn(monkeypatch):
    queued = []
    monkeypatch.setattr(
        dataset_tasks.diagnose, "apply_async", lambda *args, **kwargs: queued.append(kwargs)
    )
    project = _project()
    capability = Capability.objects.create(project=project, name="Support", slug="support")
    _call_span(project, capability, uuid.uuid4().hex[:16])
    dataset = dispatch.create_dataset(
        project=project,
        user=None,
        name="Calls",
        source={
            "llm_calls": {
                "capability_id": str(capability.id),
                "since": (timezone.now() - timedelta(hours=1)).isoformat(),
                "limit": 10,
            }
        },
        intent="eval",
        capability=capability,
        infer_capability=False,
    )
    dataset.refresh_from_db()
    assert queued == []
    assert dataset.state == Dataset.State.IDLE, dataset.error
    assert dataset.active_cell.fits("eval")[0]
    frame = store.read_frame(paths.cell_path(dataset.id, dataset.source.id))
    assert "trace_id" not in set(frame.columns)
    assert frame.iloc[0]["origin_trace_id"]


def test_error_spans_leave_the_dataset_in_error():
    project = _project()
    capability = Capability.objects.create(project=project, name="Support", slug="support")
    _call_span(project, capability, uuid.uuid4().hex[:16], error=True)
    dataset = dispatch.create_dataset(
        project=project,
        user=None,
        name="Calls",
        source={
            "llm_calls": {
                "capability_id": str(capability.id),
                "since": (timezone.now() - timedelta(hours=1)).isoformat(),
            }
        },
        intent="eval",
        capability=capability,
        infer_capability=False,
    )
    dataset.refresh_from_db()
    assert dataset.state == Dataset.State.ERROR
    assert "No LLM calls" in dataset.error


def test_hash_split_lands_train_and_eval():
    project = _project()
    capability = Capability.objects.create(project=project, name="Support", slug="support")
    train_ids, eval_ids = _ids_for_split()
    for span_id in [*train_ids, *eval_ids]:
        _call_span(project, capability, span_id, tool=True)
    train, evaluation = dispatch.create_split(
        project=project,
        user=None,
        name="Calls",
        source={
            "llm_calls": {
                "capability_id": str(capability.id),
                "since": (timezone.now() - timedelta(hours=1)).isoformat(),
                "limit": 10,
            }
        },
        eval_percent=20,
        position="hash",
        capability=capability,
        infer_capability=False,
    )
    train.refresh_from_db()
    evaluation.refresh_from_db()
    assert train.state == Dataset.State.IDLE, train.error
    assert evaluation.state == Dataset.State.IDLE, evaluation.error
    assert train.active_cell.fits("train")[0]
    assert evaluation.active_cell.fits("eval")[0]
    assert {train.source.rows, evaluation.source.rows} == {2}
