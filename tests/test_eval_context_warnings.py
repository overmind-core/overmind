import pytest
from conftest import frozen_dataset
from pydantic import BaseModel

from overbae.core import llms
from overbae.core.model_registry import OPENROUTER_MODEL_SLUGS
from overbae.models import DeployedModel, Evaluator, Project
from overbae.services import llm_context
from overbae.services.eval import context_check, funnel, runner


class Verdict(BaseModel):
    score: float


LUNA = "openai/gpt-5.6-luna"


def _judge(model="gpt-5.6-luna"):
    return funnel.invoke_judge(
        "all evidence",
        judge=funnel.ResolvedJudge(model, None, "openai"),
        response_format=Verdict,
        use_cache=False,
    )


def _sent(fake_llm, slug=LUNA):
    return [r for r in fake_llm.requests if r.model == slug]


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


def test_judge_retries_with_larger_supported_budget_and_retains_both_attempts(fake_llm):
    fake_llm.limits[LUNA] = 100000
    fake_llm.output_limits[LUNA] = 24000
    replies = iter(
        [
            {"content": "", "finish_reason": "length", "usage": {"cost": 0.02}},
            {"content": '{"score":0.5}', "usage": {"cost": 0.01}},
        ]
    )
    fake_llm.on(lambda r: r.model == LUNA, lambda r: next(replies))
    outcome = _judge()
    assert outcome.parsed.score == 0.5
    sent = _sent(fake_llm)
    assert [r.body["max_tokens"] for r in sent] == [17000, 24000]
    assert all("all evidence" in r.text for r in sent)
    assert outcome.stats["response_cost"] == pytest.approx(0.03)
    assert len(outcome.stats["attempts"]) == 2


def test_judge_does_not_repeat_known_context_rejection_or_leak_provider_body(fake_llm):
    fake_llm.fail(
        lambda r: r.model == "openai/gpt-4.1", 400, "maximum context length; private provider body"
    )
    outcome = _judge("gpt-4.1")
    assert len(_sent(fake_llm, "openai/gpt-4.1")) == 1
    assert outcome.parsed is None
    assert outcome.stats["error_kind"] == "context_limit"
    assert "private" not in funnel.failure_reason(outcome)
    assert outcome.stats["response_cost"] is None


def test_generation_warning_does_not_skip_provider_call(fake_llm):
    fake_llm.limits["openai/gpt-4.1"] = 100
    fake_llm.output_limits["openai/gpt-4.1"] = 100
    fake_llm.on(lambda r: r.model == "openai/gpt-4.1", "answer")
    result = runner.generate_decision(
        input_messages=[{"role": "user", "content": "full input"}],
        tool_provider=runner.ReplayToolProvider(),
        model="gpt-4.1",
    )
    assert len(_sent(fake_llm, "openai/gpt-4.1")) == 1
    assert result.context_checks[0]["status"] == "warning"
    assert result.output_messages[0]["content"] == "answer"


@pytest.mark.django_db
def test_preflight_profiles_generation_and_judge_from_selected_version(fake_llm):
    project = Project.objects.create(name="Context")
    dataset = frozen_dataset(
        project, [{"input": "input" * 5000, "expected_output": "answer"}], contract="eval"
    )
    evaluator = Evaluator.objects.create(
        project=project,
        name="Quality",
        kind="llm_judge",
        rubric_md="Assess evidence",
        checklist=[{"id": "correct", "q": "Correct?"}],
    )
    fake_llm.limits = dict.fromkeys(OPENROUTER_MODEL_SLUGS.values(), 1000)
    fake_llm.output_limits = dict.fromkeys(OPENROUTER_MODEL_SLUGS.values(), 5000)
    checks = context_check.check_context(
        dataset=dataset,
        cell=dataset.active_cell,
        variants=[{"model_name": "gpt-4.1"}],
        evaluators=[evaluator],
    )
    assert [check["role"] for check in checks] == ["generation", "judge"]
    assert all(check["status"] == "warning" and check["checked_rows"] == 1 for check in checks)


def test_wire_budget_override_is_sent_once_and_the_caller_keeps_its_params(fake_llm):
    params = {"max_tokens": 24000}
    llms.call_llm("input", model="gpt-5.6-luna", request_kwargs=params)
    assert _sent(fake_llm)[-1].body["max_tokens"] == 24000
    assert params == {"max_tokens": 24000}


@pytest.mark.django_db
def test_deployed_limits_are_actual_and_project_scoped():
    project = Project.objects.create(name="Deployment", slug="deployment")
    other = Project.objects.create(name="Other", slug="other")
    DeployedModel.objects.create(project=project, model_id="ft-context-test", max_model_len=8192)
    assert llm_context.model_limits("ft-context-test", project_id=project.pk).context_window == 8192
    assert llm_context.model_limits("ft-context-test", project_id=other.pk).context_window is None
    assert llm_context.model_limits("gpt-4.1", custom=True).context_window is None


@pytest.mark.parametrize("limits", [None, (18000, 17000)], ids=["unknown", "no headroom"])
def test_exhausted_judge_does_not_retry_without_known_extra_capacity(fake_llm, limits):
    if limits is None:
        fake_llm.catalog_models = [slug for slug in OPENROUTER_MODEL_SLUGS.values() if slug != LUNA]
    else:
        fake_llm.limits[LUNA], fake_llm.output_limits[LUNA] = limits
    fake_llm.on(lambda r: r.model == LUNA, {"content": "partial", "finish_reason": "length"})
    outcome = _judge()
    assert len(_sent(fake_llm)) == 1
    assert outcome.parsed is None
    assert outcome.stats["error_kind"] == "output_limit"
    assert "output token limit" in funnel.failure_reason(outcome)


@pytest.mark.django_db
def test_preflight_includes_custom_judge_bindings(fake_llm):
    project = Project.objects.create(name="Bindings")
    dataset = frozen_dataset(
        project,
        [{"input": "question", "expected_output": "answer", "evidence": "x" * 30000}],
        contract="eval",
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
    fake_llm.limits = dict.fromkeys(OPENROUTER_MODEL_SLUGS.values(), 8000)
    fake_llm.output_limits = dict.fromkeys(OPENROUTER_MODEL_SLUGS.values(), 5000)
    checks = context_check.check_context(
        dataset=dataset, cell=dataset.active_cell, variants=[], evaluators=[evaluator]
    )
    assert checks[0]["estimated_input_tokens"] > 10000
    assert checks[0]["status"] == "warning"
