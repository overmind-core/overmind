import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from django.core.cache import cache
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ValidationError

from overbae.api.eval_serializers import DecisionPolicySerializer
from overbae.core import decisions as transport
from overbae.core.model_registry import decision_model, inference_models, judge_picker_models
from overbae.models import Evaluator
from overbae.services.eval import cascade, decisions, dispatch, funnel, per_turn_judge
from overbae.services.eval.evaluators import gen_judge, judge
from overbae.services.eval.evaluators.base import EvalUnit
from overbae.services.eval.snapshots import build_snapshot
from tests.factories import evaluator_stub

_REQUEST = transport._request


def response(body, choices=None, confidence=0.99):
    choices = choices or {}
    answers = {}
    for key, question in body["questions"].items():
        chosen = choices.get(key, next(iter(question["criteria"])))
        answers[key] = {
            "type": "choice",
            "choice": chosen,
            "confidence": confidence,
            "probabilities": {option: float(option == chosen) for option in question["criteria"]},
        }
    return {
        "model": decision_model().slug + "-20260917",
        "answers": answers,
        "usage": {"input_tokens": 100, "output_tokens": 0, "cost": 0.0000042},
        "id": "request",
    }


@pytest.fixture
def provider(monkeypatch):
    cache.clear()
    call = Mock(side_effect=lambda body, **kwargs: response(body))
    monkeypatch.setattr(transport, "_request", call)
    return call


def question():
    return decisions.decision_question("Does the supplied answer match the reference?")


def call_decision(**kwargs):
    return transport.decide(
        {"output": "yes", "reference": "yes"},
        {"q": question()},
        project_id="project",
        workload="test",
        contract="test@1",
        **kwargs,
    )


def test_transport_validates_answers_and_preserves_usage(provider):
    result = call_decision()
    assert result.answers["q"].choice == "pass"
    assert result.stats["response_cost"] == 0.0000042
    assert result.stats["served_model"].endswith("20260917")
    assert provider.call_args.args[0]["model"] == decision_model().slug


@pytest.mark.parametrize("usage", [[1], {"cost": True, "input_tokens": "invalid"}])
def test_malformed_usage_does_not_hide_a_valid_decision_or_invent_a_cost(provider, usage):
    provider.side_effect = lambda body, **kwargs: {**response(body), "usage": usage}
    result = call_decision()
    assert result.answers["q"].choice == "pass"
    assert result.stats["response_cost"] is None
    assert result.stats["prompt_tokens"] == 0


def test_unexpected_served_model_is_rejected(provider):
    provider.side_effect = lambda body, **kwargs: {**response(body), "model": "some/chat-model"}
    with pytest.raises(transport.DecisionError, match="invalid_response"):
        call_decision()


def test_cache_is_project_question_and_contract_scoped(provider):
    call_decision()
    cached = call_decision()
    assert cached.stats["cached"]
    assert cached.stats["response_cost"] == 0
    assert cached.stats["prompt_tokens"] == 0
    for project, contract in [("other", "test@1"), ("project", "test@2")]:
        transport.decide(
            {"output": "yes", "reference": "yes"},
            {"q": question()},
            project_id=project,
            workload="test",
            contract=contract,
        )
    assert provider.call_count == 3


@pytest.mark.parametrize(
    "defect", ["missing", "extra", "unknown", "sum", "negative", "nan", "wrong_type", "not_argmax"]
)
def test_invalid_provider_answer_is_not_cached(provider, defect):
    def bad(body, **kwargs):
        payload = response(body)
        answer = payload["answers"]["q"]
        if defect == "missing":
            payload["answers"] = {}
        elif defect == "extra":
            payload["answers"]["other"] = answer
        elif defect == "unknown":
            answer["choice"] = "invented"
        elif defect == "sum":
            answer["probabilities"]["pass"] = 0.3
        elif defect == "negative":
            answer["probabilities"]["fail"] = -1
        elif defect == "nan":
            answer["confidence"] = float("nan")
        elif defect == "wrong_type":
            answer["type"] = "noul"
        else:
            answer["choice"] = "fail"
        return payload

    provider.side_effect = bad
    with pytest.raises(transport.DecisionError, match="invalid_response") as raised:
        call_decision()
    assert raised.value.stats["response_cost"] == 0.0000042
    provider.side_effect = lambda body, **kwargs: response(body)
    call_decision()
    assert provider.call_count == 2


def test_context_is_never_silently_truncated(provider):
    with pytest.raises(transport.DecisionError, match="context_budget"):
        transport.decide(
            "界" * 11_000, {"q": question()}, project_id="p", workload="t", contract="v"
        )
    provider.assert_not_called()


def test_question_batches_respect_both_budgets():
    questions = {str(index): decisions.decision_question("x" * 1000) for index in range(150)}
    batches = transport.question_batches("context", questions)
    assert len(batches) > 1
    assert sum(len(batch) for batch in batches) == 150
    assert all(len(json.dumps(batch).encode()) < 64_000 for batch in batches)


def test_missing_project_never_calls_provider(provider):
    with pytest.raises(transport.DecisionError, match="project_required"):
        transport.decide("state", {"q": question()}, project_id="", workload="t", contract="v")
    provider.assert_not_called()


def test_shared_capacity_reserves_request_and_token_budget(monkeypatch, settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "redis://test/1",
        }
    }
    client = Mock()
    client.eval.side_effect = [100, 0]
    monkeypatch.setattr(transport, "_redis", lambda url: client)
    monkeypatch.setattr(transport.time, "sleep", Mock())
    transport.reserve_capacity("account", 300, transport.time.monotonic() + 1)
    args = client.eval.call_args.args
    assert args[2:4] == (
        "overmind:decisions:{account}:requests",
        "overmind:decisions:{account}:tokens",
    )
    assert args[-1] == 300
    assert client.eval.call_count == 2


def test_capacity_outage_fails_closed(settings):
    with pytest.raises(transport.DecisionError, match="capacity_unavailable"):
        transport.reserve_capacity("account", 100, transport.time.monotonic() + 1)


def test_systemone_endpoint_and_overload_retry(monkeypatch):
    reserve = Mock()
    post = Mock(side_effect=[httpx.Response(529), httpx.Response(200, json={"answers": {}})])
    monkeypatch.setattr(transport, "reserve_capacity", reserve)
    monkeypatch.setattr(transport.httpx, "post", post)
    monkeypatch.setattr(transport.time, "sleep", Mock())
    _REQUEST(
        {"model": decision_model().slug},
        deadline=transport.time.monotonic() + 20,
        account="account",
    )
    assert post.call_count == reserve.call_count == 2
    assert post.call_args.args[0] == "https://openrouter.ai/api/v1/systemone"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer offline-test-key"


def test_timeout_cost_is_unknown_not_free(monkeypatch):
    monkeypatch.setattr(transport, "reserve_capacity", Mock())
    monkeypatch.setattr(transport.httpx, "post", Mock(side_effect=httpx.ReadTimeout("timeout")))
    with pytest.raises(transport.DecisionError) as raised:
        _REQUEST({}, deadline=transport.time.monotonic() + 20, account="account")
    assert raised.value.stats["response_cost"] is None


class Answer(BaseModel):
    label: str


def test_low_confidence_falls_back_and_adds_both_costs(provider):
    provider.side_effect = lambda body, **kwargs: response(body, confidence=0.2)
    fallback = Mock(
        return_value=funnel.JudgeOutcome(
            parsed=Answer(label="fail"),
            raw="{}",
            stats={"response_cost": 0.003},
            judge_trace_id="fallback",
        )
    )
    outcome = decisions.invoke(
        "state",
        {"q": question()},
        convert=lambda answers: Answer(label=answers["q"].choice),
        fallback=fallback,
        project_id="p",
        workload="w",
        contract="c",
    )
    assert outcome.parsed.label == "fail"
    assert outcome.stats["response_cost"] == pytest.approx(0.0030042)
    assert outcome.stats["decision"]["source"] == "generative_fallback"
    assert outcome.stats["decision"]["answers"]["q"]["confidence"] == 0.2
    fallback.assert_called_once()


def test_explicit_generative_backend_does_not_call_jev(provider):
    fallback = Mock(
        return_value=funnel.JudgeOutcome(parsed=None, raw="", stats={}, judge_trace_id="f")
    )
    decisions.invoke(
        "state",
        {"q": question()},
        convert=Mock(),
        fallback=fallback,
        project_id="p",
        workload="w",
        contract="c",
        policy=decisions.DecisionPolicy(backend="generative"),
    )
    provider.assert_not_called()
    fallback.assert_called_once()


@pytest.mark.parametrize(
    "value",
    [
        {"min_confidence": -1},
        {"min_confidence": float("nan")},
        {"model": "jev-latest"},
        {"backend": "magic"},
        {"version": 2},
        {"typo": True},
    ],
)
def test_policy_rejects_ambiguous_configuration(value):
    with pytest.raises(ValidationError):
        decisions.DecisionPolicy.model_validate(value)


@pytest.mark.parametrize(
    "value", [{"min_confidence": float("nan")}, {"model": "jev-latest"}, {"version": 2}]
)
def test_api_rejects_invalid_decision_policy(value):
    assert not DecisionPolicySerializer(data=value).is_valid()


def evaluator(**kwargs):
    config = kwargs.pop("config", {})
    config.setdefault("decision", {"backend": "jev"})
    return Evaluator(
        name="Correctness",
        kind="llm_judge",
        score_type="numeric",
        rubric_md="Compare output to reference.",
        config=config,
        checklist=[
            {"id": "a", "q": "Matches reference?", "weight": 3},
            {"id": "b", "q": "Concise?", "weight": 1},
        ],
        **kwargs,
    )


def test_rubric_judges_default_to_generative_and_freeze_that_policy():
    assert decisions.policy_for(Evaluator(config={})).backend == "generative"
    assert decisions.freeze_config({})["decision"]["backend"] == "generative"
    request = DecisionPolicySerializer(data={})
    assert request.is_valid(), request.errors
    assert request.validated_data["backend"] == "generative"
    assert decisions.freeze_config({"decision": {"backend": "jev"}})["decision"]["backend"] == "jev"


def test_checklist_keeps_weights_and_provenance(provider):
    provider.side_effect = lambda body, **kwargs: response(body, {"a": "pass", "b": "fail"})
    ev = evaluator()
    result = gen_judge.evaluate(
        EvalUnit(trajectory={"final_output": "yes"}, expected="yes"), ev, {"project_id": "p"}
    )[0]
    assert result.value == 0.75
    assert (
        next(item["_decision"] for item in result.sub_scores if "_decision" in item)["source"]
        == "jev"
    )


def test_insufficient_checklist_evidence_falls_back_not_na(provider, monkeypatch):
    provider.side_effect = lambda body, **kwargs: response(body, {"a": "insufficient", "b": "pass"})
    fallback = Mock(
        return_value=funnel.JudgeOutcome(
            parsed=decisions.ResolvedChoices(
                answers={"a": decisions.ResolvedAnswer(choice="fail", reasoning="Contradicted.")}
            ),
            raw="{}",
            stats={"response_cost": 0.01},
            judge_trace_id="f",
        )
    )
    monkeypatch.setattr(funnel, "invoke_judge", fallback)
    result = gen_judge.evaluate(
        EvalUnit(trajectory={"final_output": "yes"}, expected="yes"),
        evaluator(),
        {"project_id": "p"},
    )[0]
    assert result.value == 0.25
    assert set(json.loads(fallback.call_args.args[0])["questions"]) == {"a"}
    assert (
        next(item["_decision"] for item in result.sub_scores if "_decision" in item)[
            "fallback_reason"
        ]
        == "uncertain"
    )


def test_per_turn_dimensions_keep_tool_applicability(provider):
    provider.side_effect = lambda body, **kwargs: response(
        body, {"progress": "1", "turn_match": "1"}
    )
    ev = evaluator(config={"per_turn_judge": True})
    unit = EvalUnit(
        expected={"trajectory": []},
        structured={"_reference_final": "yes", "_candidate_final": "yes"},
    )
    drafts = per_turn_judge.evaluate(unit, ev, {"project_id": "p"})
    assert [draft.name for draft in drafts] == ["Correctness: progress", "Correctness: turn match"]
    assert [draft.value for draft in drafts] == [1, 1]
    assert sum(draft.cost for draft in drafts) == 0.0000042
    assert (
        sum(
            item["_decision"]["total_cost"]
            for draft in drafts
            for item in draft.sub_scores
            if "_decision" in item
        )
        == 0.0000042
    )


def test_behaviour_step_success_uses_anchored_decisions(provider, monkeypatch):
    provider.side_effect = lambda body, **kwargs: response(body, {"quality": "0.9"})
    fallback = Mock(side_effect=AssertionError("A confident step pass should not generate."))
    monkeypatch.setattr(funnel, "invoke_judge", fallback)
    ev = evaluator(config={"behaviour": {"role": "step"}}, score_min=0, score_max=1)
    draft = judge.evaluate(
        EvalUnit(trajectory={"final_output": "yes"}, expected="yes"), ev, {"project_id": "p"}
    )[0]
    assert draft.value == 0.9
    assert (
        next(item["_decision"] for item in draft.sub_scores if "_decision" in item)["source"]
        == "jev"
    )
    fallback.assert_not_called()


def test_behaviour_step_failure_retains_generative_causal_reasoning(provider, monkeypatch):
    provider.side_effect = lambda body, **kwargs: response(body, {"quality": "0.3"})
    fallback = Mock(
        return_value=funnel.JudgeOutcome(
            parsed=judge.JudgeResult(
                score=0.3, reasoning="The required evidence was ignored.", root_cause="a"
            ),
            raw="{}",
            stats={"response_cost": 0.01},
            judge_trace_id="f",
        )
    )
    monkeypatch.setattr(funnel, "invoke_judge", fallback)
    ev = evaluator(config={"behaviour": {"role": "step"}}, score_min=0, score_max=1)
    draft = judge.evaluate(
        EvalUnit(trajectory={"final_output": "no"}, expected="yes"), ev, {"project_id": "p"}
    )[0]
    assert draft.value == 0.3
    assert "ignored" in draft.reasoning
    fallback.assert_called_once()


def test_claim_extraction_and_support_costs_are_both_preserved(provider, monkeypatch):
    provider.side_effect = lambda body, **kwargs: response(
        body, {"0": "supported", "1": "contradicted"}
    )
    generate = Mock(
        return_value=funnel.JudgeOutcome(
            parsed=gen_judge.ClaimTexts(claims=["The box is blue.", "The box is red."]),
            raw="{}",
            stats={"response_cost": 0.001},
            judge_trace_id="extraction",
        )
    )
    monkeypatch.setattr(funnel, "invoke_judge", generate)
    outcome = gen_judge._invoke_judge(
        "judge",
        schema=gen_judge.ClaimsResult,
        evaluator=evaluator(config={"scoring_mode": "proportional"}),
        variables={"output": "Blue and red.", "reference": "The box is blue."},
        project_id="p",
    )
    assert [claim.supported for claim in outcome.parsed.claims] == [True, False]
    assert outcome.stats["response_cost"] == pytest.approx(0.0010042)
    assert outcome.stats["decision"]["total_cost"] == outcome.stats["response_cost"]
    assert "output" not in provider.call_args.args[0]["state"]
    generate.assert_called_once()


def test_grounding_keeps_insufficiency_out_of_denominator(provider, monkeypatch):
    provider.side_effect = lambda body, **kwargs: response(
        body, {"0": "supported", "1": "contradicted", "2": "insufficient"}
    )
    monkeypatch.setattr(
        funnel,
        "invoke_judge",
        lambda *args, **kwargs: funnel.JudgeOutcome(
            parsed=SimpleNamespace(claims=["a", "b", "c"]),
            raw="",
            stats={"response_cost": 0.001},
            judge_trace_id="d",
        ),
    )
    result = dispatch.grounding_verdict(
        trajectory={"final_output": "a b c"},
        unit_spans=[],
        project_id="p",
        target_id="s",
        trace_corpus=["environment evidence"],
    )
    assert result["score"] == 0.5
    assert result["metadata"]["coverage"] == pytest.approx(2 / 3, abs=0.0001)
    assert result["cost"] == pytest.approx(0.0010042)
    assert result["identifier"] == dispatch.grounding_identifier()
    provenance = next(
        item["_decision"] for item in result["metadata"]["sub_scores"] if "_decision" in item
    )
    assert provenance["total_cost"] == result["cost"]


def test_grounding_abstention_uses_the_same_policy_identity():
    result = dispatch.grounding_verdict(trajectory={}, unit_spans=[], project_id="p", target_id="s")
    assert result["identifier"] == dispatch.grounding_identifier()


def test_independent_questions_only_recheck_uncertainty(provider, monkeypatch):
    def answer(body, **kwargs):
        payload = response(body)
        if "q95" in payload["answers"]:
            payload["answers"]["q95"]["confidence"] = 0.2
        return payload

    provider.side_effect = answer
    generate = Mock(
        return_value=funnel.JudgeOutcome(
            parsed=decisions.ResolvedChoices(
                answers={
                    "q95": decisions.ResolvedAnswer(choice="fail", reasoning="Source disagrees.")
                }
            ),
            raw="{}",
            stats={"response_cost": 0.003},
            judge_trace_id="selective",
        )
    )
    monkeypatch.setattr(funnel, "invoke_judge", generate)
    fallback = Mock(side_effect=AssertionError("Confident questions must not be regraded."))
    chosen_judge = funnel.resolve_judge("")
    result = decisions.invoke(
        {"evidence": "kept intact"},
        {f"q{i}": question() for i in range(96)},
        convert=lambda answers: decisions.ResolvedChoices(
            answers={key: decisions.ResolvedAnswer(choice=a.choice) for key, a in answers.items()}
        ),
        fallback=fallback,
        project_id="p",
        workload="partial",
        contract="v",
        independent=True,
        judge=chosen_judge,
    )
    payload = json.loads(generate.call_args.args[0])
    assert set(payload["questions"]) == {"q95"}
    assert payload["state"] == {"evidence": "kept intact"}
    assert generate.call_args.kwargs["judge"] is chosen_judge
    assert [a.choice for a in result.parsed.answers.values()].count("pass") == 95
    assert result.parsed.answers["q95"].choice == "fail"
    assert result.stats["decision"]["source"] == "mixed"
    assert "confidence" not in result.stats["decision"]["resolved_answers"]["q95"]
    assert result.stats["response_cost"] == pytest.approx(0.0030042)


def test_later_transport_failure_keeps_completed_question_batches(provider, monkeypatch):
    successful = set()

    def answer(body, **kwargs):
        if successful:
            raise transport.DecisionError("provider_timeout", stats={"response_cost": None})
        successful.update(body["questions"])
        return response(body)

    provider.side_effect = answer

    def generate(prompt, response_format, **kwargs):
        pending = json.loads(prompt)["questions"]
        assert not successful.intersection(pending)
        return funnel.JudgeOutcome(
            parsed=response_format.model_validate(
                {"answers": {key: {"choice": "fail"} for key in pending}}
            ),
            raw="{}",
            stats={"response_cost": 0.003},
            judge_trace_id="remaining",
        )

    monkeypatch.setattr(funnel, "invoke_judge", generate)
    result = decisions.invoke(
        "state",
        {f"q{i}": question() for i in range(150)},
        convert=lambda answers: decisions.ResolvedChoices(
            answers={key: decisions.ResolvedAnswer(choice=a.choice) for key, a in answers.items()}
        ),
        fallback=Mock(side_effect=AssertionError("Do not discard completed batches.")),
        project_id="p",
        workload="partial_failure",
        contract="v",
        independent=True,
    )
    assert len(result.parsed.answers) == 150
    assert all(result.parsed.answers[key].choice == "pass" for key in successful)
    assert result.stats["response_cost"] is None
    assert result.stats["attempts"][0]["attempts"][0]["response_cost"] == 0.0000042


@pytest.mark.parametrize(
    "bad",
    [{}, {"q": {"choice": "invented"}}, {"q": {"choice": "pass"}, "extra": {"choice": "pass"}}],
)
def test_unusable_generative_resolution_remains_unknown(monkeypatch, bad):
    monkeypatch.setattr(
        funnel,
        "invoke_judge",
        lambda *args, **kwargs: funnel.JudgeOutcome(
            parsed=decisions.ResolvedChoices.model_validate({"answers": bad}),
            raw="{}",
            stats={"response_cost": 0.001},
            judge_trace_id="bad",
        ),
    )
    result = decisions.resolve_questions("state", {"q": question()}, project_id="p")
    assert result.parsed.answers["q"].choice is None
    assert result.stats["resolution_error"]


def test_question_resolution_uses_closed_provider_schema_and_preserves_ids(monkeypatch):
    questions = dict.fromkeys(["0", "answer support", "_private", "model_dump"], question())

    def generate(prompt, response_format, **kwargs):
        schema = to_strict_json_schema(response_format)

        def check(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    assert value["additionalProperties"] is False
                    assert set(value["required"]) == set(value["properties"])
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        check(schema)
        assert set(schema["$defs"]["QuestionAnswers"]["properties"]) == set(questions)
        assert list(schema["$defs"]["ResolvedAnswer"]["properties"]) == ["reasoning", "choice"]
        parsed = response_format.model_validate(
            {
                "answers": {
                    key: {"reasoning": "Source agrees.", "choice": "pass"} for key in questions
                }
            }
        )
        return funnel.JudgeOutcome(parsed=parsed, raw="{}", stats={}, judge_trace_id="wire")

    monkeypatch.setattr(funnel, "invoke_judge", generate)
    outcome = decisions.resolve_questions("state", questions, project_id="p")
    assert set(outcome.parsed.answers) == set(questions)
    assert all(answer.choice == "pass" for answer in outcome.parsed.answers.values())


def test_checklist_selective_fallback_keeps_explicit_judge(provider, monkeypatch):
    provider.side_effect = lambda body, **kwargs: response(body, {"a": "insufficient"})
    chosen = object()
    resolve = Mock(return_value=chosen)
    monkeypatch.setattr(funnel, "resolve_judge", resolve)
    fallback = Mock(
        return_value=funnel.JudgeOutcome(
            parsed=decisions.ResolvedChoices(
                answers={"a": decisions.ResolvedAnswer(choice="pass")}
            ),
            raw="{}",
            stats={},
            judge_trace_id="pinned",
        )
    )
    monkeypatch.setattr(funnel, "invoke_judge", fallback)
    gen_judge._invoke_judge(
        "grade",
        schema=gen_judge.ChecklistResult,
        evaluator=evaluator(),
        variables={"output": "blue", "reference": "blue"},
        project_id="p",
        judge_model="pinned-model",
    )
    resolve.assert_called_once_with("pinned-model", "p")
    assert fallback.call_args.kwargs["judge"] is chosen


def test_per_turn_fallback_keeps_explicit_judge(provider, monkeypatch):
    provider.side_effect = transport.DecisionError("provider_timeout")
    chosen = object()
    resolve = Mock(return_value=chosen)
    monkeypatch.setattr(funnel, "resolve_judge", resolve)
    generate = Mock(
        return_value=funnel.JudgeOutcome(
            parsed=per_turn_judge._TurnVerdict(progress=1, turn_match=1),
            raw="{}",
            stats={},
            judge_trace_id="pinned",
        )
    )
    monkeypatch.setattr(funnel, "invoke_judge", generate)
    per_turn_judge.evaluate(
        EvalUnit(
            expected={"trajectory": []},
            structured={"_reference_final": "blue", "_candidate_final": "blue"},
        ),
        evaluator(judge_model="pinned-model"),
        {"project_id": "p"},
    )
    resolve.assert_called_once_with("pinned-model", "p")
    assert generate.call_args.kwargs["judge"] is chosen


def test_error_latency_includes_provider_wait_and_fallback(provider, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(transport.time, "monotonic", lambda: clock[0])

    def timeout(*args, **kwargs):
        clock[0] += 0.025
        raise transport.DecisionError("provider_timeout", stats={"response_cost": None})

    provider.side_effect = timeout

    def fallback():
        clock[0] += 0.010
        return funnel.JudgeOutcome(
            parsed=Answer(label="pass"),
            raw="{}",
            stats={"response_cost": 0.003, "response_ms": 10},
            judge_trace_id="fallback",
        )

    result = decisions.invoke(
        "state",
        {"q": question()},
        convert=lambda answers: Answer(label="pass"),
        fallback=fallback,
        project_id="p",
        workload="latency",
        contract="v",
    )
    assert result.stats["response_ms"] == 35
    assert result.stats["decision"]["usage"]["response_ms"] == 25
    assert result.stats["response_cost"] is None


@pytest.mark.parametrize("quota", ["invalid", "0", "-1"])
def test_invalid_shared_capacity_configuration_falls_back(monkeypatch, settings, quota):
    settings.CACHES = {"default": {**settings.CACHES["default"], "LOCATION": "redis://test/1"}}
    monkeypatch.setenv("JEV_REQUESTS_PER_MINUTE", quota)
    with pytest.raises(transport.DecisionError, match="capacity_configuration"):
        transport.reserve_capacity("account", 10, transport.time.monotonic() + 20)


def test_success_after_server_error_keeps_unknown_retry_cost(provider, monkeypatch):
    body = {
        "model": decision_model().slug,
        "state": "state",
        "questions": {"q": question().model_dump()},
    }
    monkeypatch.setattr(transport, "reserve_capacity", Mock())
    monkeypatch.setattr(transport.time, "sleep", Mock())
    monkeypatch.setattr(
        transport.httpx,
        "post",
        Mock(side_effect=[httpx.Response(503), httpx.Response(200, json=response(body))]),
    )
    monkeypatch.setattr(transport, "_request", _REQUEST)
    result = call_decision(use_cache=False)
    assert result.stats["response_cost"] is None
    assert result.stats["attempts"][0]["attempts"][1]["response_cost"] == 0.0000042


@pytest.mark.parametrize(
    "resolution, supported", [("contradicted", False), ("insufficient", False), (None, None)]
)
def test_claim_fallback_preserves_extraction_and_support_semantics(
    provider, monkeypatch, resolution, supported
):
    provider.side_effect = lambda body, **kwargs: response(
        body, {"0": "supported", "1": "insufficient"}
    )
    generated = []

    def generate(prompt, response_format, **kwargs):
        generated.append(response_format)
        if response_format is gen_judge.ClaimTexts:
            parsed = gen_judge.ClaimTexts(claims=["Blue", "Red"])
        else:
            assert set(json.loads(prompt)["questions"]) == {"1"}
            parsed = decisions.ResolvedChoices(
                answers={"1": decisions.ResolvedAnswer(choice=resolution)}
            )
        return funnel.JudgeOutcome(
            parsed=parsed,
            raw="{}",
            stats={"response_cost": 0.001},
            judge_trace_id=str(len(generated)),
        )

    monkeypatch.setattr(funnel, "invoke_judge", generate)
    result = gen_judge._invoke_judge(
        "judge",
        schema=gen_judge.ClaimsResult,
        evaluator=evaluator(config={"scoring_mode": "proportional"}),
        variables={"output": "Blue and red", "reference": "Blue"},
        project_id="p",
    )
    assert generated[0] is gen_judge.ClaimTexts
    assert generated[1].__name__ == "QuestionResolution"
    assert [claim.claim for claim in result.parsed.claims] == ["Blue", "Red"]
    assert [claim.supported for claim in result.parsed.claims] == [True, supported]


def test_cascade_provenance_is_stored_once_and_cost_includes_holistic(provider, monkeypatch):
    provider.side_effect = lambda body, **kwargs: response(
        body, dict.fromkeys(body["questions"], "1")
    )
    sizes = []
    for count in (8, 32):
        unit = EvalUnit(
            trajectory={"messages": [{"role": "user", "content": "go"}]},
            structured={
                "tool_graph": {
                    "nodes": [
                        {"id": f"step_{i}", "tool": f"tool{i}", "result": "ok"}
                        for i in range(count)
                    ]
                }
            },
        )
        ev = evaluator_stub(
            name="Trajectory",
            kind="trajectory",
            rubric_md="Check quality",
            config={
                "step_window": 50,
                "judge_salient_only": False,
                "max_steps_judged": 0,
                "decision": {"backend": "jev"},
            },
        )
        result = cascade.run_cascade(
            unit, ev, {"project_id": "p"}, strategy="per_step", budget=60000, approx_tokens=5
        ).drafts[0]
        assert sum("_decision" in item for item in result.sub_scores) == 1
        assert result.sub_scores[0]["_decision"]["total_cost"] == result.cost
        sizes.append(len(json.dumps(result.sub_scores)))
    assert sizes[1] < sizes[0] * 5
    monkeypatch.setattr(
        funnel,
        "invoke_judge",
        lambda *args, **kwargs: funnel.JudgeOutcome(
            parsed=judge.JudgeResult(score=0.8),
            raw="{}",
            stats={"response_cost": None},
            judge_trace_id="holistic",
        ),
    )
    result = cascade.run_cascade(
        unit, ev, {"project_id": "p"}, strategy="per_step+mapreduce", budget=60000, approx_tokens=5
    ).drafts[0]
    assert result.sub_scores[0]["_decision"]["total_cost"] is None


def test_snapshot_pins_policy_without_mutating_library():
    ev = evaluator()
    ev.config = {}
    snap = build_snapshot(ev)
    assert snap["config"]["decision"]["version"] == 1
    assert snap["config"]["decision"]["model"] == decision_model().slug
    assert snap["config"]["decision"]["backend"] == "generative"
    assert "decision" not in ev.config


def test_jev_is_not_advertised_as_a_chat_model():
    assert decision_model() not in inference_models()
    assert decision_model().name not in judge_picker_models()
