import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from pydantic import BaseModel

from modal_shared.context_budget import DEFAULT_OUTPUT_TOKENS, completion_body, reserve_output
from overbae.core import llms
from overbae.models import (
    EvalRun,
    EvalSample,
    Evaluator,
    EvalVariant,
    FinetuningJob,
    FinetuningJobEval,
    Project,
    RunEvaluator,
    Score,
)
from overbae.services import deployment, serving_context
from overbae.services.eval import funnel, runner, snapshots
from overbae.services.eval.comparison import compare_runs
from overbae.services.inference_client import ContextBudgetError, InferenceClient
from overbae.tasks import eval as eval_tasks


@pytest.mark.parametrize("payload", [{}, {"max_tokens": None}, {"max_completion_tokens": None}])
def test_missing_output_limit_reserves_capacity(payload):
    assert reserve_output(payload)["max_tokens"] == DEFAULT_OUTPUT_TOKENS
    assert payload.get("max_tokens") is None


def test_modern_output_budget_wins_without_duplicate_parameters():
    assert reserve_output({"max_tokens": 32, "max_completion_tokens": 64}) == {"max_tokens": 64}


@pytest.mark.parametrize("limit", [0, -1, True, "100", 1.5])
def test_invalid_output_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="positive integer"):
        reserve_output({"max_tokens": limit})


def test_serving_never_silently_truncates_the_prompt():
    with pytest.raises(ValueError, match="Prompt truncation"):
        reserve_output({"truncate_prompt_tokens": 2048})


@pytest.mark.parametrize("stream", [False, True])
def test_worker_request_budget_includes_streams_and_tools(stream):
    payload = {
        "model": "ft-model",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "read"}}],
        "stream": stream,
    }
    guarded = json.loads(completion_body("/v1/chat/completions", json.dumps(payload).encode()))
    assert guarded == {**payload, "max_tokens": DEFAULT_OUTPUT_TOKENS}
    assert completion_body("/health", b"") == b""


def test_serving_plan_uses_native_window_without_rope_scaling(monkeypatch):
    monkeypatch.setattr(
        serving_context,
        "get_model_config_any_backend",
        lambda _: {
            "context_length": 131072,
            "finetuning": {"context_length": 32768},
            "inference": {"max_model_len": 4096},
        },
    )
    plan = serving_context.serving_plan("model", serving_context.InferenceBudget(5000, 10000))
    assert plan["max_model_len"] == 16384
    assert plan["model_context_limit"] == 32768
    oversized = serving_context.serving_plan("model", serving_context.InferenceBudget(30000, 10000))
    assert oversized["max_model_len"] == 32768
    assert "reserved output need" in oversized["warnings"][0]


@pytest.mark.parametrize("text", [None, '{"answer":'])
def test_llm_preserves_incomplete_output_and_usage_without_json_repair(monkeypatch, text):
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=None), finish_reason="length"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=3109, completion_tokens=987),
        model="ft-model",
    )
    monkeypatch.setattr(llms, "_openrouter_client", Mock())
    completion = Mock(return_value=response)
    monkeypatch.setattr(llms, "_do_openai_completion", completion)
    with pytest.raises(llms.IncompleteCompletionError) as error:
        llms.call_llm("hello", model="gpt-4.1")
    assert error.value.content == (text or "")
    assert error.value.stats["finish_reason"] == "length"
    assert error.value.stats["completion_tokens"] == 987
    assert completion.call_count == 1


@pytest.mark.parametrize("generate", [runner.run_capability, runner.generate_decision])
def test_incomplete_tool_call_is_never_executed(monkeypatch, generate):
    tool = Mock()
    tool.tool_definitions.return_value = []
    partial = '{"tool_calls":[{"id":"a","function":{"name":"write","arguments":"{}"}}]}'
    monkeypatch.setattr(
        runner,
        "call_llm",
        Mock(
            side_effect=llms.IncompleteCompletionError(
                partial,
                {"finish_reason": "length", "prompt_tokens": 3000, "completion_tokens": 1096},
            )
        ),
    )
    result = generate(input_messages=[{"role": "user", "content": "work"}], tool_provider=tool)
    assert result.truncated is True
    assert result.finish_reasons == ["length"]
    assert result.output_messages == [{"role": "assistant", "content": partial}]
    assert result.completion_tokens == 1096
    tool.execute.assert_not_called()


@pytest.mark.django_db
def test_deployment_and_eval_budget_are_independent_of_training_length():
    project = Project.objects.create(name="Context")
    train = frozen_dataset(project, TRAIN_ROWS)
    evaluation = frozen_dataset(project, [{"input": "i" * 9000, "expected_output": "o" * 18000}])
    job = FinetuningJob.objects.create(
        project=project,
        dataset=train,
        cell=train.active_cell,
        eval_dataset=evaluation,
        eval_cell=evaluation.active_cell,
        base_model="Qwen/Qwen3.5-27B",
        provider="modal",
        status="succeeded",
        remote_job_id="job:done",
        hyperparameters={"context_length": 4096},
    )
    plan = serving_context.job_serving_plan(job)
    assert plan["rows"] == 1
    assert plan["output_tokens"] > DEFAULT_OUTPUT_TOKENS
    assert plan["max_model_len"] >= plan["required_context"] > 4096
    deployed = deployment.ensure_training_deployment(str(job.pk))
    assert deployed.max_model_len == plan["max_model_len"]
    job.refresh_from_db()
    assert job.hyperparameters["context_length"] == 4096


@pytest.mark.django_db
def test_truncated_generation_stays_visible_but_cannot_be_a_quality_score(monkeypatch):
    project = Project.objects.create(name="Incomplete eval")
    evaluation = frozen_dataset(project, [{"input": "packet", "expected_output": "report"}])
    run = EvalRun.objects.create(
        project=project,
        dataset=evaluation,
        cell=evaluation.active_cell,
        name="run",
        status="running",
    )
    variant = EvalVariant.objects.create(run=run, label="model", mode="generate")
    sample = EvalSample.objects.create(run=run, variant=variant, row_index=0)
    evaluator = Evaluator.objects.create(
        project=project, name="Quality", kind="deterministic", config={"check": "json_valid"}
    )
    run_evaluator = RunEvaluator.objects.create(
        run=run, evaluator=evaluator, snapshot=snapshots.build_snapshot(evaluator)
    )
    train = frozen_dataset(project, TRAIN_ROWS)
    job = FinetuningJob.objects.create(
        project=project, dataset=train, base_model="Qwen/Qwen3.5-27B", status="succeeded"
    )
    link = FinetuningJobEval.objects.create(job=job, kind="final", eval_run=run, status="running")
    monkeypatch.setattr(
        runner,
        "call_llm",
        Mock(
            side_effect=llms.IncompleteCompletionError(
                '{"report":',
                {"finish_reason": "length", "prompt_tokens": 3000, "completion_tokens": 1096},
            )
        ),
    )
    eval_tasks.prepare_sample.run(sample_id=str(sample.pk))
    sample.refresh_from_db()
    assert sample.degraded is True
    assert sample.degraded_reason.startswith("output_token_limit:")
    assert sample.trajectory["metadata"]["truncated"] is True
    assert sample.trajectory["metadata"]["finish_reasons"] == ["length"]
    eval_tasks.execute_evaluator.run(
        sample_id=str(sample.pk), run_evaluator_id=str(run_evaluator.pk)
    )
    score = Score.objects.get(sample=sample)
    assert score.outcome == "skipped" and score.value is None
    eval_tasks.aggregate_run.run(eval_run_id=str(run.pk))
    run.refresh_from_db()
    link.refresh_from_db()
    assert run.summary["trust"]["degraded"] == 1
    assert compare_runs(run.summary, run.summary)["trust"]["current"]["trusted"] is False
    assert link.status == "failed" and link.aggregate_score is None
    assert "incomplete or degraded" in link.error_message


def test_per_turn_truncation_cannot_be_hidden_by_other_successful_turns():
    degraded, reason = eval_tasks._assess_degradation(
        {
            "metadata": {"generation_strategy": "per_assistant_turn", "output_truncated": True},
            "final_output": "later valid output",
        },
        {"per_turn": [{"generated": "valid"}]},
        [],
        is_generate=True,
    )
    assert degraded and reason.startswith("output_token_limit:")


def test_judge_does_not_repair_or_cache_truncated_json_but_keeps_usage(monkeypatch):
    class Result(BaseModel):
        score: float

    monkeypatch.setattr(
        funnel,
        "call_llm",
        Mock(
            side_effect=llms.IncompleteCompletionError(
                '{"score": 1', {"finish_reason": "length", "response_cost": 0.02}
            )
        ),
    )
    outcome = funnel.invoke_judge(
        "evidence",
        response_format=Result,
        use_cache=False,
        judge=funnel.ResolvedJudge("gpt-4.1", None, "openai"),
    )
    assert outcome.parsed is None
    assert outcome.stats["response_cost"] == 0.02
    assert not outcome.cached


@pytest.mark.parametrize("stream", [False, True])
def test_provider_context_rejection_is_actionable_without_leaking_body(monkeypatch, stream):
    response = Mock(ok=False, status_code=400, text="maximum context length exceeded; private body")
    post = Mock(return_value=response)
    monkeypatch.setattr("overbae.services.inference_client.requests.post", post)
    client = InferenceClient(base_url="https://inference.test", api_key="test")
    with pytest.raises(ContextBudgetError, match="reserved output exceed") as error:
        client.chat_completions(
            model_id="model", messages=[{"role": "user", "content": "q"}], stream=stream
        )
    assert "private body" not in str(error.value)
    assert post.call_args.kwargs["json"]["max_tokens"] == DEFAULT_OUTPUT_TOKENS


def test_impossible_gpu_context_is_rejected_instead_of_selecting_an_undersized_gpu(monkeypatch):
    monkeypatch.setattr(
        serving_context,
        "get_model_config_any_backend",
        lambda _: {
            "context_length": 131072,
            "finetuning": {"context_length": 131072},
            "num_attn_layers": 80,
            "num_kv_heads": 64,
            "head_dim": 128,
            "total_params_b": 200,
            "fp8_supported": False,
        },
    )
    with pytest.raises(ValueError, match="single-GPU capacity"):
        serving_context.serving_plan("oversized", serving_context.InferenceBudget(20000, 10000))
