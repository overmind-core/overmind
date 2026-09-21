from __future__ import annotations

import json
from datetime import timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from overbae.models import EvalRun, EvalSample, EvalVariant, Project
from overbae.services.datasets.rows import DatasetRow
from overbae.services.eval import runner
from overbae.tasks import eval as eval_tasks


def _sample():
    project = Project.objects.create(name="P", slug="generation-progress")
    run = EvalRun.objects.create(project=project, name="r", status=EvalRun.Status.RUNNING)
    variant = EvalVariant.objects.create(
        run=run,
        label="v",
        mode="generate",
        model_name="gpt-5-mini",
        params={"generation_strategy": "per_assistant_turn", "max_steps": 4},
    )
    return EvalSample.objects.create(run=run, variant=variant)


def _messages(turns):
    messages = [{"role": "user", "content": "Find the result"}]
    for index in range(turns):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"recorded-{index}",
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": json.dumps({"q": f"recorded {index}"}),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": f"recorded-{index}",
                    "content": f"recorded result {index}",
                },
            ]
        )
    return messages


@pytest.mark.django_db
@pytest.mark.parametrize("turns", [1, 2])
def test_per_turn_makes_one_decision_without_replaying_tools(monkeypatch, turns):
    sample = _sample()
    original = _messages(turns)
    row = DatasetRow(
        index=0, input={"messages": original, "tools": [{"name": "search", "parameters": {}}]}
    )
    monkeypatch.setattr(eval_tasks, "_sample_row", lambda sample: row)
    monkeypatch.setattr(eval_tasks, "_variant_model", lambda variant: ("test-model", None))
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return json.dumps(
            {"tool_calls": [{"id": "new", "name": "search", "arguments": {"q": "different"}}]}
        ), {"response_cost": 0.1, "response_ms": 20}

    def no_replay(*args, **kwargs):
        pytest.fail("per-turn generation must not execute or replay candidate tools")

    monkeypatch.setattr(runner, "call_llm", completion)
    monkeypatch.setattr(runner.ReplayToolProvider, "execute", no_replay)
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    sample.refresh_from_db()
    assert sample.error == ""
    assert len(calls) == turns
    assert len(sample.structured["per_turn"]) == turns
    assert sample.trajectory["metadata"]["generation_strategy"] == "per_assistant_turn"
    assert sample.trajectory["metadata"]["steps"] == turns
    assert sample.degraded is False  # a tool decision needs no final answer
    assert all(not any(m["role"] == "tool" for m in call["messages"]) for call in calls[:1])
    if turns == 2:
        assert any(m.get("content") == "recorded result 0" for m in calls[1]["messages"])
        assert not any("different" in json.dumps(m) for m in calls[1]["messages"])


@pytest.mark.parametrize("method", ["run_capability", "generate_decision"])
def test_generation_does_not_swallow_worker_soft_timeout(monkeypatch, method):
    def timeout(**kwargs):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(runner, "call_llm", timeout)
    with pytest.raises(SoftTimeLimitExceeded):
        getattr(runner, method)(
            input_messages=[{"role": "user", "content": "q"}],
            tool_provider=runner.ReplayToolProvider(),
        )


@pytest.mark.django_db
def test_worker_timeout_stops_remaining_recorded_turns(monkeypatch):
    sample = _sample()
    row = DatasetRow(index=0, input={"messages": _messages(2), "tools": [{"name": "search"}]})
    monkeypatch.setattr(eval_tasks, "_sample_row", lambda sample: row)
    monkeypatch.setattr(eval_tasks, "_variant_model", lambda variant: ("test-model", None))
    calls = []

    def timeout(**kwargs):
        calls.append(kwargs)
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(runner, "call_llm", timeout)
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    sample.refresh_from_db()
    assert len(calls) == 1
    assert sample.error == "generation timed out (exceeded soft_time_limit)"
    assert not sample.trajectory


def test_empty_per_turn_generation_is_degraded():
    degraded, reason = eval_tasks._assess_degradation(
        {"metadata": {"generation_strategy": "per_assistant_turn"}},
        {"per_turn": [{"generated": [], "generated_final": ""}]},
        [],
        is_generate=True,
    )
    assert degraded is True
    assert reason.startswith("no_decisions:")


@pytest.mark.django_db
@pytest.mark.parametrize("failure", [False, True])
def test_finished_generation_refreshes_run_activity(monkeypatch, failure):
    sample = _sample()
    old = timezone.now() - timedelta(minutes=35)
    EvalRun.objects.filter(pk=sample.run_id).update(updated_at=old)
    monkeypatch.setattr(eval_tasks, "_sample_row", lambda sample: None)

    def generate(sample):
        if failure:
            raise SoftTimeLimitExceeded()
        return {"messages": [{"role": "assistant", "content": "done"}], "final_output": "done"}

    monkeypatch.setattr(eval_tasks, "_generate_sample", generate)
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    sample.refresh_from_db()
    sample.run.refresh_from_db()
    assert sample.run.updated_at > old
    assert bool(sample.error) == failure


@pytest.mark.django_db
def test_each_generated_decision_refreshes_activity_before_next_call(monkeypatch):
    sample = _sample()
    old = timezone.now() - timedelta(minutes=35)
    EvalRun.objects.filter(pk=sample.run_id).update(updated_at=old)
    row = DatasetRow(index=0, input={"messages": _messages(2), "tools": [{"name": "search"}]})
    monkeypatch.setattr(eval_tasks, "_sample_row", lambda sample: row)
    monkeypatch.setattr(eval_tasks, "_variant_model", lambda variant: ("test-model", None))
    seen = []

    def completion(**kwargs):
        seen.append(EvalRun.objects.get(pk=sample.run_id).updated_at)
        return "decision", {}

    monkeypatch.setattr(runner, "call_llm", completion)
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    assert len(seen) == 2
    assert seen[1] > old


@pytest.mark.django_db
def test_late_generation_does_not_touch_terminal_run(monkeypatch):
    sample = _sample()
    old = timezone.now() - timedelta(minutes=35)
    EvalRun.objects.filter(pk=sample.run_id).update(updated_at=old, status=EvalRun.Status.CANCELLED)
    monkeypatch.setattr(eval_tasks, "_sample_row", lambda sample: None)
    monkeypatch.setattr(
        eval_tasks, "_generate_sample", lambda sample: {"messages": [], "final_output": "done"}
    )
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    sample.run.refresh_from_db()
    assert sample.run.updated_at == old
    assert sample.run.status == EvalRun.Status.CANCELLED
