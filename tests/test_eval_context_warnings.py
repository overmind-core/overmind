from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import frozen_dataset
from pydantic import BaseModel

from overbae.core import llms
from overbae.models import DeployedModel, Evaluator, Project
from overbae.services import llm_context
from overbae.services.eval import context_check, funnel, runner


class Verdict(BaseModel):
    score: float


def test_unknown_limit_is_not_claimed_to_fit():
    check = llm_context.assess_context(
        model="custom",
        inputs=[200],
        limits=llm_context.ModelLimits(),
        role="generation",
        label="Custom",
    )
    assert check["status"] == "unknown"
    assert check["estimated"] is True


def test_context_check_counts_rows_and_reserves_output_without_mutating_input():
    inputs = [100, 4000, 5000]
    check = llm_context.assess_context(
        model="model",
        inputs=inputs,
        output_tokens=2000,
        limits=llm_context.ModelLimits(6000, 3000),
        role="generation",
        label="Candidate",
        row_indices=[0, 7, 9],
    )
    assert check["status"] == "warning"
    assert check["affected_rows"] == 1
    assert check["row_indices"] == [9]
    assert check["required_context"] == 7000
    assert inputs == [100, 4000, 5000]


def test_output_limit_is_checked_separately_from_context():
    check = llm_context.assess_context(
        model="model",
        inputs=[200],
        output_tokens=5000,
        limits=llm_context.ModelLimits(100000, 2000),
        role="judge",
        label="Judge",
    )
    assert check["status"] == "warning"
    assert check["affected_rows"] == 1


def test_unicode_estimates_include_utf8_size():
    assert llm_context.estimate_input_tokens("界" * 1000) > llm_context.estimate_input_tokens(
        "a" * 1000
    )


def test_judge_retries_with_larger_supported_budget_and_retains_both_attempts(monkeypatch):
    monkeypatch.setattr(
        funnel, "model_limits", lambda *args, **kwargs: llm_context.ModelLimits(100000, 24000)
    )
    completion = Mock(
        side_effect=[
            llms.IncompleteCompletionError(
                "", {"finish_reason": "length", "completion_tokens": 17000, "response_cost": 0.02}
            ),
            ('{"score":0.5}', {"finish_reason": "stop", "response_cost": 0.01}),
        ]
    )
    monkeypatch.setattr(funnel, "call_llm", completion)
    outcome = funnel.invoke_judge(
        "all evidence",
        judge=funnel.ResolvedJudge("gpt-5.6-luna", None, "openai"),
        response_format=Verdict,
        use_cache=False,
    )
    assert outcome.parsed.score == 0.5
    assert [call.kwargs["request_kwargs"]["max_tokens"] for call in completion.call_args_list] == [
        17000,
        24000,
    ]
    assert all(call.args[0] == "all evidence" for call in completion.call_args_list)
    assert outcome.stats["response_cost"] == pytest.approx(0.03)
    assert len(outcome.stats["attempts"]) == 2


def test_judge_does_not_repeat_known_context_rejection_or_leak_provider_body(monkeypatch):
    completion = Mock(side_effect=RuntimeError("maximum context length; private provider body"))
    monkeypatch.setattr(funnel, "call_llm", completion)
    outcome = funnel.invoke_judge(
        "evidence",
        judge=funnel.ResolvedJudge("gpt-4.1", None, "openai"),
        response_format=Verdict,
        use_cache=False,
    )
    assert completion.call_count == 1
    assert outcome.parsed is None
    assert outcome.stats["error_kind"] == "context_limit"
    assert "private" not in funnel.failure_reason(outcome)
    assert outcome.stats["response_cost"] is None


def test_generation_warning_does_not_skip_provider_call(monkeypatch):
    monkeypatch.setattr(
        llm_context, "model_limits", lambda *args, **kwargs: llm_context.ModelLimits(100, 100)
    )
    completion = Mock(return_value=("answer", {"finish_reason": "stop"}))
    monkeypatch.setattr(runner, "call_llm", completion)
    tool = Mock()
    tool.tool_definitions.return_value = []
    result = runner.generate_decision(
        input_messages=[{"role": "user", "content": "full input"}],
        tool_provider=tool,
        model="gpt-4.1",
    )
    completion.assert_called_once()
    assert result.context_checks[0]["status"] == "warning"
    assert result.output_messages[0]["content"] == "answer"


@pytest.mark.django_db
def test_preflight_profiles_generation_and_judge_from_selected_version(monkeypatch):
    project = Project.objects.create(name="Context")
    dataset = frozen_dataset(project, [{"input": "input" * 5000, "expected_output": "answer"}])
    evaluator = Evaluator.objects.create(
        project=project,
        name="Quality",
        kind="llm_judge",
        rubric_md="Assess evidence",
        checklist=[{"id": "correct", "q": "Correct?"}],
    )
    monkeypatch.setattr(
        context_check, "model_limits", lambda *args, **kwargs: llm_context.ModelLimits(1000, 5000)
    )
    checks = context_check.check_context(
        dataset=dataset,
        cell=dataset.active_cell,
        variants=[{"model_name": "gpt-4.1"}],
        evaluators=[evaluator],
    )
    assert [check["role"] for check in checks] == ["generation", "judge"]
    assert all(check["status"] == "warning" and check["checked_rows"] == 1 for check in checks)


def test_wire_budget_override_includes_reasoning_only_once(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
        usage=None,
    )
    completion = Mock(return_value=response)
    monkeypatch.setattr(llms, "_openrouter_client", Mock())
    monkeypatch.setattr(llms, "_do_openai_completion", completion)
    params = {"max_tokens": 24000}
    llms.call_llm("input", model="gpt-5.6-luna", request_kwargs=params)
    assert completion.call_args.args[1]["max_tokens"] == 24000
    assert "max_tokens" not in completion.call_args.args[2]
    assert params == {"max_tokens": 24000}


@pytest.mark.django_db
def test_deployed_limits_are_actual_and_project_scoped():
    project = Project.objects.create(name="Deployment", slug="deployment")
    other = Project.objects.create(name="Other", slug="other")
    DeployedModel.objects.create(project=project, model_id="ft-context-test", max_model_len=8192)
    assert llm_context.model_limits("ft-context-test", project_id=project.pk).context_window == 8192
    assert llm_context.model_limits("ft-context-test", project_id=other.pk).context_window is None
    assert llm_context.model_limits("gpt-4.1", custom=True).context_window is None


@pytest.mark.parametrize(
    "limits",
    [llm_context.ModelLimits(), llm_context.ModelLimits(18000, 17000)],
)
def test_exhausted_judge_does_not_retry_without_known_extra_capacity(monkeypatch, limits):
    monkeypatch.setattr(funnel, "model_limits", lambda *args, **kwargs: limits)
    completion = Mock(
        side_effect=llms.IncompleteCompletionError("partial", {"finish_reason": "length"})
    )
    monkeypatch.setattr(funnel, "call_llm", completion)
    outcome = funnel.invoke_judge(
        "evidence",
        judge=funnel.ResolvedJudge("gpt-5.6-luna", None, "openai"),
        response_format=Verdict,
        use_cache=False,
    )
    completion.assert_called_once()
    assert outcome.parsed is None
    assert outcome.stats["error_kind"] == "output_limit"
    assert "output token limit" in funnel.failure_reason(outcome)


@pytest.mark.django_db
def test_preflight_includes_custom_judge_bindings(monkeypatch):
    project = Project.objects.create(name="Bindings")
    dataset = frozen_dataset(
        project, [{"input": "question", "expected_output": "answer", "evidence": "x" * 30000}]
    )
    evaluator = Evaluator.objects.create(
        project=project,
        name="Grounding",
        kind="llm_judge",
        variable_mapping=[
            {"var": "evidence", "source": "metadata", "jsonpath": "$.row_extra.evidence"}
        ],
        checklist=[{"id": "grounded", "q": "Is the answer grounded?"}],
    )
    monkeypatch.setattr(
        context_check, "model_limits", lambda *args, **kwargs: llm_context.ModelLimits(8000, 5000)
    )
    checks = context_check.check_context(
        dataset=dataset, cell=dataset.active_cell, variants=[], evaluators=[evaluator]
    )
    assert checks[0]["estimated_input_tokens"] > 10000
    assert checks[0]["status"] == "warning"
