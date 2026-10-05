from __future__ import annotations

import json
from datetime import timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from conftest import frozen_dataset
from django.utils import timezone

from overbae.models import EvalRun, EvalSample, EvalVariant, Project
from overbae.services.eval import runner
from overbae.tasks import eval as eval_tasks

pytestmark = pytest.mark.django_db


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


def _sample(*, turns: int = 2, strategy: str = "per_assistant_turn") -> EvalSample:
    project = Project.objects.create(name="P", slug="generation-progress")
    dataset = frozen_dataset(
        project,
        [
            {
                "input": {
                    "messages": _messages(turns),
                    "tools": [{"name": "search", "parameters": {}}],
                },
                "expected_output": "result",
            }
        ],
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        status=EvalRun.Status.RUNNING,
        dataset=dataset,
        cell=dataset.active_cell,
    )
    variant = EvalVariant.objects.create(
        run=run,
        label="v",
        mode="generate",
        model_name="gpt-5-mini",
        params={"generation_strategy": strategy, "max_steps": 4},
    )
    return EvalSample.objects.create(run=run, variant=variant, row_index=0)


def _decisions(fake_llm) -> list:
    return [r for r in fake_llm.requests if r.model == "openai/gpt-5-mini"]


def _prepare(sample: EvalSample) -> EvalSample:
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    sample.refresh_from_db()
    return sample


@pytest.mark.parametrize("turns", [1, 2])
def test_per_turn_makes_one_decision_without_replaying_tools(fake_llm, turns):
    fake_llm.on(
        lambda r: r.model == "openai/gpt-5-mini",
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "new",
                    "type": "function",
                    "function": {"name": "search", "arguments": json.dumps({"q": "different"})},
                }
            ],
        },
    )
    sample = _prepare(_sample(turns=turns))

    calls = _decisions(fake_llm)
    assert sample.error == ""
    assert len(calls) == turns
    assert len(sample.structured["per_turn"]) == turns
    assert sample.trajectory["metadata"]["generation_strategy"] == "per_assistant_turn"
    assert sample.trajectory["metadata"]["steps"] == turns
    assert sample.degraded is False
    assert not any(m["role"] == "tool" for m in calls[0].messages)
    if turns == 2:
        assert any(m.get("content") == "recorded result 0" for m in calls[1].messages)
        assert not any("different" in json.dumps(m) for m in calls[1].messages)


def test_single_completion_records_tool_calls_without_executing_them(fake_llm):
    fake_llm.on(
        lambda r: r.model == "openai/gpt-5-mini",
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "new",
                    "type": "function",
                    "function": {"name": "search", "arguments": json.dumps({"q": "x"})},
                }
            ],
        },
    )
    sample = _prepare(_sample(turns=0, strategy="single_completion"))

    calls = _decisions(fake_llm)
    messages = sample.trajectory["messages"]
    assert len(calls) == 1
    assert calls[0].body["tools"]
    assert messages[-1]["tool_calls"][0]["name"] == "search"
    assert not any(m.get("role") == "tool" for m in messages)


def test_empty_per_turn_generation_is_degraded(fake_llm):
    fake_llm.on(lambda r: r.model == "openai/gpt-5-mini", "")
    sample = _prepare(_sample(turns=1))
    assert sample.degraded is True
    assert sample.degraded_reason.startswith("no_decisions:")


@pytest.mark.parametrize("method", ["run_capability", "generate_decision"])
def test_generation_does_not_swallow_worker_soft_timeout(fake_llm, slept, method):
    calls = []

    def timeout_once(request):
        calls.append(request)
        if len(calls) == 1:
            raise SoftTimeLimitExceeded()
        return "late answer"

    fake_llm.on(lambda r: True, timeout_once)
    with pytest.raises(SoftTimeLimitExceeded):
        getattr(runner, method)(
            input_messages=[{"role": "user", "content": "q"}],
            tool_provider=runner.ReplayToolProvider(),
            model="gpt-5-mini",
        )


def test_worker_timeout_stops_the_remaining_recorded_turns(fake_llm):
    calls = []

    def timeout(request):
        calls.append(request)
        raise SoftTimeLimitExceeded()

    fake_llm.on(lambda r: r.model == "openai/gpt-5-mini", timeout)
    sample = _prepare(_sample(turns=2))
    assert len(calls) == 1
    assert sample.error == "generation timed out (exceeded soft_time_limit)"
    assert not sample.trajectory


@pytest.mark.parametrize("failure", [False, True])
def test_finished_generation_refreshes_run_activity(fake_llm, failure):
    sample = _sample(turns=1, strategy="full")
    old = timezone.now() - timedelta(minutes=35)
    EvalRun.objects.filter(pk=sample.run_id).update(updated_at=old)
    if failure:
        fake_llm.fail(lambda r: r.model == "openai/gpt-5-mini", 400, "bad request")
    else:
        fake_llm.on(lambda r: r.model == "openai/gpt-5-mini", "done")

    sample = _prepare(sample)

    sample.run.refresh_from_db()
    assert sample.run.updated_at > old
    assert bool(sample.error) == failure


def test_each_generated_decision_refreshes_activity_before_next_call(fake_llm):
    sample = _sample(turns=2)
    old = timezone.now() - timedelta(minutes=35)
    EvalRun.objects.filter(pk=sample.run_id).update(updated_at=old)
    seen = []

    def decision(request):
        seen.append(EvalRun.objects.get(pk=sample.run_id).updated_at)
        return "decision"

    fake_llm.on(lambda r: r.model == "openai/gpt-5-mini", decision)
    _prepare(sample)
    assert len(seen) == 2
    assert seen[1] > old


def test_late_generation_does_not_touch_terminal_run(fake_llm):
    sample = _sample(turns=1, strategy="full")
    old = timezone.now() - timedelta(minutes=35)
    EvalRun.objects.filter(pk=sample.run_id).update(updated_at=old, status=EvalRun.Status.CANCELLED)
    fake_llm.on(lambda r: r.model == "openai/gpt-5-mini", "done")
    _prepare(sample)
    sample.run.refresh_from_db()
    assert sample.run.updated_at == old
    assert sample.run.status == EvalRun.Status.CANCELLED
