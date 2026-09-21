from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest import mock

import pytest
from conftest import EVAL_ROWS, frozen_dataset

from overbae.models import (
    Capability,
    Dataset,
    EvalRun,
    EvalSample,
    Evaluator,
    EvalVariant,
    Project,
    RunEvaluator,
    Score,
)
from overbae.services.datasets.rows import row as _dataset_row
from overbae.services.eval import funnel as judging
from overbae.services.eval import snapshots
from overbae.services.eval.evaluators.gen_judge import ChecklistItem, ChecklistResult
from overbae.tasks import eval as eval_tasks

pytestmark = pytest.mark.django_db


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _attach(run, evaluator) -> RunEvaluator:
    return RunEvaluator.objects.create(
        run=run, evaluator=evaluator, snapshot=snapshots.build_snapshot(evaluator)
    )


def test_statistical_pipeline_accuracy():
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project,
        [
            {
                "input": [
                    {"role": "user", "content": "q1"},
                    {"role": "assistant", "content": "yes"},
                ],
                "expected_output": "yes",
            },
            {
                "input": [
                    {"role": "user", "content": "q2"},
                    {"role": "assistant", "content": "no"},
                ],
                "expected_output": "yes",
            },
        ],
        capability=capability,
    )

    evaluator = Evaluator.objects.create(
        project=project,
        name="Accuracy",
        kind="statistical",
        scope="dataset",
        config={"metric": "accuracy"},
        version=1,
    )
    run = EvalRun.objects.create(
        project=project,
        name="run",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
        max_items=10,
    )
    run_eval = _attach(run, evaluator)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)

    # Mirrors run_eval_run's sample creation.
    items = eval_tasks._resolve_items(run)
    assert len(items) == 2
    sample_ids = []
    for item in items:
        s = EvalSample.objects.create(
            run=run, variant=variant, row_index=item["row_index"], expected=item["expected"]
        )
        sample_ids.append(str(s.id))

    for sid in sample_ids:
        eval_tasks.prepare_sample.apply(kwargs={"sample_id": sid}).get()
        eval_tasks.execute_evaluator.apply(
            kwargs={"sample_id": sid, "run_evaluator_id": str(run_eval.id)}
        ).get()

    preds = Score.objects.filter(run=run, name="Accuracy__prediction")
    assert preds.count() == 2

    # Dataset-level accuracy = 1/2.
    eval_tasks.aggregate_run.apply(kwargs={"eval_run_id": str(run.id)}).get()
    run.refresh_from_db()
    assert run.status == EvalRun.Status.COMPLETED
    dataset_score = Score.objects.get(run=run, scope="dataset", name="Accuracy")
    assert abs(dataset_score.value - 0.5) < 1e-9
    assert "variants" in run.summary


def test_normalize_datapoint_propagates_row_extra():
    """Row-level extra columns must reach metadata.row_extra for variable mappings."""
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project,
        [
            {"input": "q1", "expected_output": "a1", "sourceTraceId": "t-1", "fields": ["x", "y"]},
            {"input": "q2"},
        ],
        capability=capability,
    )
    dp = _row(dataset, 0)
    normalized = eval_tasks.normalize_datapoint(dp)
    assert normalized["metadata"]["row_extra"]["sourceTraceId"] == "t-1"
    assert normalized["metadata"]["row_extra"]["fields"] == ["x", "y"]
    assert normalized["metadata"]["has_expected"] is True

    # Rows without extras still expose a row_extra dict carrying no user columns.
    bare = _row(dataset, 1)
    extra = eval_tasks.normalize_datapoint(bare)["metadata"]["row_extra"]
    assert not {
        k: v
        for k, v in extra.items()
        if v not in (None, "") and k not in {"source_row", "_overmind_provenance"}
    }


def test_bind_eval_reference_strips_gold_from_object_input():
    from overbae.services.eval.profiler import bind_eval_reference

    inp, expected = bind_eval_reference({"question": "Who?", "answer": "Teller"}, None)
    assert inp == {"question": "Who?"}
    assert expected == "Teller"

    inp, expected = bind_eval_reference({"question": "Who?", "gold": "Teller"}, "already")
    assert inp == {"question": "Who?"}
    assert expected == "already"


def test_normalize_datapoint_strips_gold_keys_from_object_input():
    dp = SimpleNamespace(
        input={"question": "Who discovered?", "answer": "Edward Teller"},
        expected_output="",
        extra={},
    )
    normalized = eval_tasks.normalize_datapoint(dp)
    content = normalized["messages"][0]["content"]
    assert "Edward Teller" not in content
    assert "Who discovered?" in content
    assert normalized["expected"] == "Edward Teller"


def test_resolve_items_reads_reference_from_expected_output():
    """Read verbatim from expected_output — never scavenged from the input."""
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project,
        [
            {
                "input": {"input": "summarize the rows", "status": "ACTIVE"},
                "expected_output": "GT-ANSWER",
            }
        ],
        capability=capability,
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )

    items = eval_tasks._resolve_items(run)
    assert len(items) == 1
    assert items[0]["expected"] == "GT-ANSWER"


def test_generate_prompt_excludes_reference(monkeypatch):
    """The reference stays out of the prompt but remains available for scoring."""
    from overbae.services.eval.runner import RunResult

    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    ground_truth = '{"clusters":8,"silhouette":0.41}'
    dataset = frozen_dataset(
        capability.project,
        [
            {
                "input": {"input": "cluster dolly.jsonl", "status": "ACTIVE"},
                "expected_output": ground_truth,
            }
        ],
        capability=capability,
    )
    dp = _row(dataset, 0)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    variant = EvalVariant.objects.create(
        run=run, label="v", mode="generate", model_name="gpt-5-mini"
    )
    sample = EvalSample.objects.create(
        run=run, variant=variant, row_index=dp.index, expected=ground_truth
    )

    captured: dict = {}

    def fake_run_capability(**kwargs):
        captured.update(kwargs)
        return RunResult([{"role": "assistant", "content": "a fresh answer"}], 1, 0.0, 0, 0)

    monkeypatch.setattr("overbae.services.eval.runner.run_capability", fake_run_capability)
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()

    prompt_text = " ".join(m.get("content") or "" for m in captured["input_messages"])
    assert "cluster dolly.jsonl" in prompt_text
    assert "silhouette" not in prompt_text

    sample.refresh_from_db()
    assert sample.expected == ground_truth
    assert sample.trajectory["final_output"] == "a fresh answer"


def test_generate_multi_turn_keeps_history_strips_final_assistant(monkeypatch):
    from overbae.services.eval.runner import RunResult

    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project,
        [
            {
                "input": [
                    {"role": "system", "content": "be terse"},
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "history answer"},
                    {"role": "user", "content": "final question"},
                    {"role": "assistant", "content": "TARGET-ANSWER"},
                ],
                "expected_output": "TARGET-ANSWER",
            }
        ],
        capability=capability,
    )
    dp = _row(dataset, 0)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    variant = EvalVariant.objects.create(
        run=run, label="v", mode="generate", model_name="gpt-5-mini"
    )
    sample = EvalSample.objects.create(
        run=run, variant=variant, row_index=dp.index, expected="TARGET-ANSWER"
    )

    captured: dict = {}

    def fake_run_capability(**kwargs):
        captured.update(kwargs)
        return RunResult([{"role": "assistant", "content": "generated"}], 1, 0.0, 0, 0)

    monkeypatch.setattr("overbae.services.eval.runner.run_capability", fake_run_capability)
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()

    seed = captured["input_messages"]
    contents = [m.get("content") or "" for m in seed]
    assert "history answer" in contents
    assert "final question" in contents
    assert all("TARGET-ANSWER" not in c for c in contents)


def test_existing_mode_reference_synthesized_for_scoring():
    """With no assistant turn in the input, the reference becomes final_output for
    judges while the user message stays clean."""
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project,
        [{"input": {"input": "summarize", "status": "ACTIVE"}, "expected_output": "GT-ANSWER"}],
        capability=capability,
    )
    dp = _row(dataset, 0)

    normalized = eval_tasks.normalize_datapoint(dp)
    assert normalized["expected"] == "GT-ANSWER"
    assert normalized["metadata"]["has_expected"] is True
    assert normalized["final_output"] == "GT-ANSWER"
    assert normalized["metadata"]["output_synthesized_from_reference"] is True
    user_msgs = [m for m in normalized["messages"] if m["role"] == "user"]
    assert user_msgs and "GT-ANSWER" not in user_msgs[0]["content"]


def test_generate_mode_total_failure_sets_sample_error(monkeypatch):
    """No output at all must set sample.error rather than score an empty output."""
    from overbae.services.eval.runner import RunResult

    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project, [{"input": "q1", "expected_output": "a1"}], capability=capability
    )
    dp = _row(dataset, 0)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    variant = EvalVariant.objects.create(
        run=run, label="v", mode="generate", model_name="gpt-5-mini"
    )
    sample = EvalSample.objects.create(run=run, variant=variant, row_index=dp.index, expected="a1")

    failed = RunResult([], 0, 0.0, 0, 0, error="Error calling LLM: missing credentials")
    monkeypatch.setattr("overbae.services.eval.runner.run_capability", lambda **_: failed)

    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    sample.refresh_from_db()
    assert "generation failed" in sample.error
    assert "missing credentials" in sample.error


def _tool_calling_input(n_calls: int) -> list[dict]:
    msgs: list[dict] = [{"role": "user", "content": "start"}]
    for i in range(n_calls):
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "read", "arguments": json.dumps({"path": f"/f{i}"})},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"r{i}"})
    msgs.append({"role": "assistant", "content": "TARGET"})
    return msgs


def test_generate_max_steps_scales_with_recorded_tool_calls(monkeypatch):
    """The step budget derives from recorded tool-call depth, not the flat default of 12."""
    from overbae.services.eval.runner import RunResult, resolve_max_steps

    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project,
        [{"input": _tool_calling_input(8), "expected_output": "TARGET"}],
        capability=capability,
    )
    dp = _row(dataset, 0)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    variant = EvalVariant.objects.create(
        run=run, label="v", mode="generate", model_name="gpt-5-mini"
    )
    sample = EvalSample.objects.create(
        run=run, variant=variant, row_index=dp.index, expected="TARGET"
    )

    captured: dict = {}

    def fake_run_capability(**kwargs):
        captured.update(kwargs)
        return RunResult([{"role": "assistant", "content": "done"}], 1, 0.0, 0, 0)

    monkeypatch.setattr("overbae.services.eval.runner.run_capability", fake_run_capability)
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()

    assert captured["max_steps"] == resolve_max_steps(recorded_tool_calls=8)
    assert captured["max_steps"] > 12
    sample.refresh_from_db()
    assert sample.trajectory["metadata"]["max_steps"] == captured["max_steps"]


def test_generate_max_steps_override_from_variant_params(monkeypatch):
    from overbae.services.eval.runner import RunResult

    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project,
        [{"input": _tool_calling_input(8), "expected_output": "TARGET"}],
        capability=capability,
    )
    dp = _row(dataset, 0)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    variant = EvalVariant.objects.create(
        run=run, label="v", mode="generate", model_name="gpt-5-mini", params={"max_steps": 40}
    )
    sample = EvalSample.objects.create(
        run=run, variant=variant, row_index=dp.index, expected="TARGET"
    )

    captured: dict = {}

    def fake_run_capability(**kwargs):
        captured.update(kwargs)
        return RunResult([{"role": "assistant", "content": "done"}], 1, 0.0, 0, 0)

    monkeypatch.setattr("overbae.services.eval.runner.run_capability", fake_run_capability)
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()

    assert captured["max_steps"] == 40


def test_generate_tool_loop_without_final_answer_degrades(monkeypatch):
    """A tool envelope is not an answer: final_output stays empty and the sample degrades."""
    from overbae.services.eval.runner import RunResult

    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project, [{"input": "q1", "expected_output": "a1"}], capability=capability
    )
    dp = _row(dataset, 0)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    variant = EvalVariant.objects.create(
        run=run, label="v", mode="generate", model_name="gpt-5-mini"
    )
    sample = EvalSample.objects.create(run=run, variant=variant, row_index=dp.index, expected="a1")

    tail = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": '{"rows": 100}'},
    ]
    monkeypatch.setattr(
        "overbae.services.eval.runner.run_capability", lambda **_: RunResult(tail, 12, 0.0, 0, 0)
    )
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()

    sample.refresh_from_db()
    assert sample.trajectory["final_output"] == ""
    assert sample.degraded is True
    assert sample.degraded_reason.startswith("no_final_output")


def test_generate_mode_partial_failure_keeps_output(monkeypatch):
    """An error after some output was produced is metadata, not a hard failure."""
    from overbae.services.eval.runner import RunResult

    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project, [{"input": "q1", "expected_output": "a1"}], capability=capability
    )
    dp = _row(dataset, 0)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    variant = EvalVariant.objects.create(
        run=run, label="v", mode="generate", model_name="gpt-5-mini"
    )
    sample = EvalSample.objects.create(run=run, variant=variant, row_index=dp.index, expected="a1")

    partial = RunResult(
        [{"role": "assistant", "content": "partial answer"}], 1, 0.0, 0, 0, error="step 2 failed"
    )
    monkeypatch.setattr("overbae.services.eval.runner.run_capability", lambda **_: partial)

    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    sample.refresh_from_db()
    assert sample.error == ""
    assert sample.trajectory["metadata"]["generation_error"] == "step 2 failed"


def test_execute_evaluator_idempotent():
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(
        capability.project,
        [{"input": [{"role": "user", "content": "hi"}], "expected_output": "x"}],
        capability=capability,
    )
    evaluator = Evaluator.objects.create(
        project=project,
        name="EM",
        kind="deterministic",
        scope="final_output",
        config={"check": "exact_match"},
        requires_reference=True,
        version=1,
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    run_eval = _attach(run, evaluator)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing")
    sample = EvalSample.objects.create(
        run=run, variant=variant, expected="x", trajectory={"final_output": "x"}
    )

    eval_tasks.execute_evaluator.apply(
        kwargs={"sample_id": str(sample.id), "run_evaluator_id": str(run_eval.id)}
    ).get()
    first = Score.objects.filter(sample=sample, run_evaluator=run_eval).count()
    res = eval_tasks.execute_evaluator.apply(
        kwargs={"sample_id": str(sample.id), "run_evaluator_id": str(run_eval.id)}
    ).get()
    assert res["status"] == "skipped"
    assert Score.objects.filter(sample=sample, run_evaluator=run_eval).count() == first


def test_run_evaluator_snapshot_is_independent():
    """A run's bound rubric snapshot survives edits/deletes to the library."""
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(capability.project, EVAL_ROWS, capability=capability)
    evaluator = Evaluator.objects.create(
        project=project,
        name="Corr",
        kind="llm_judge",
        rubric_md="original rubric",
        checklist=[{"id": "q1", "q": "Does the output satisfy the rubric?", "weight": 1.0}],
        version=1,
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    run_eval = _attach(run, evaluator)
    assert run_eval.snapshot["rubric_md"] == "original rubric"

    evaluator.delete()
    run_eval.refresh_from_db()
    assert run_eval.snapshot["rubric_md"] == "original rubric"
    assert run_eval.evaluator_id is None


def _harness_artifact_evaluator(project):
    return Evaluator.objects.create(
        project=project,
        name="structured-recommendations",
        kind="llm_judge",
        scope="final_output",
        rubric_md="Judge the recommendations field.",
        checklist=[
            {"id": "recommendations_present", "q": "Are recommendations present?", "weight": 1.0}
        ],
        variable_mapping=[
            {"var": "recommendations", "source": "output", "jsonpath": "$.recommendations"}
        ],
        evidence_requirement="harness_artifact",
        version=1,
    )


def test_harness_artifact_evaluator_not_applicable_in_generate_mode():
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(capability.project, EVAL_ROWS, capability=capability)
    evaluator = _harness_artifact_evaluator(project)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    run_eval = _attach(run, evaluator)
    variant = EvalVariant.objects.create(run=run, label="gen", mode="generate")
    sample = EvalSample.objects.create(
        run=run, variant=variant, trajectory={"final_output": "I will start by reading the data."}
    )

    res = eval_tasks.execute_evaluator.apply(
        kwargs={"sample_id": str(sample.id), "run_evaluator_id": str(run_eval.id)}
    ).get()

    assert res["status"] == "not_applicable"
    score = Score.objects.get(sample=sample, run_evaluator=run_eval)
    assert score.value is None
    assert score.sub_scores[0]["_not_applicable"] is True
    assert "existing" in score.reasoning


def test_summary_carries_per_item_pass_rates():
    """Two variants can share a score while failing different items, so the
    breakdown is frozen with the run — revealed on request, not in the headline."""
    project = _project()
    agent = Capability.objects.create(project=project, name="A", slug="a")
    dataset = Dataset.objects.create(project=project, capability=agent, name="d")
    run = EvalRun.objects.create(project=project, name="r", data_source="dataset", dataset=dataset)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)

    # facts fails 2 of 3; numbers passes all 3.
    for facts_ok in (False, False, True):
        sample = EvalSample.objects.create(
            run=run, variant=variant, trajectory={"final_output": "x"}
        )
        Score.objects.create(
            project=project,
            run=run,
            variant=variant,
            sample=sample,
            name="Correctness",
            data_type="numeric",
            value=0.5,
            outcome="scored",
            sub_scores=[
                {"id": "facts", "verdict": facts_ok, "reasoning": ""},
                {"id": "numbers", "verdict": True, "reasoning": ""},
                # Meta and per-sample claims must not be counted as items.
                {"_threshold": {"pass_threshold": None}},
                {"claim": "a one-off claim", "verdict": False},
                {"id": "unanswered", "verdict": None},
            ],
        )

    items = eval_tasks._build_summary(run)["items"][str(variant.id)]["Correctness"]
    assert items == [
        {"id": "facts", "passed": 1, "total": 3, "pass_rate": pytest.approx(1 / 3)},
        {"id": "numbers", "passed": 3, "total": 3, "pass_rate": 1.0},
    ]


def test_summary_omits_invented_item_ids():
    project = _project()
    agent = Capability.objects.create(project=project, name="A", slug="a")
    dataset = Dataset.objects.create(project=project, capability=agent, name="d")
    run = EvalRun.objects.create(project=project, name="r", data_source="dataset", dataset=dataset)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)
    evaluator = Evaluator.objects.create(
        project=project,
        name="Correctness",
        kind="llm_judge",
        checklist=[{"id": "facts", "q": "?", "weight": 1.0}],
    )
    RunEvaluator.objects.create(
        run=run,
        evaluator=evaluator,
        snapshot=snapshots.build_snapshot(evaluator),
    )
    sample = EvalSample.objects.create(run=run, variant=variant, trajectory={"final_output": "x"})
    Score.objects.create(
        project=project,
        run=run,
        variant=variant,
        sample=sample,
        name="Correctness",
        data_type="numeric",
        value=1.0,
        outcome="scored",
        sub_scores=[
            {"id": "facts", "verdict": True, "reasoning": ""},
            {"id": "hallucinated", "verdict": True, "reasoning": ""},
        ],
    )
    items = eval_tasks._build_summary(run)["items"][str(variant.id)]["Correctness"]
    assert [row["id"] for row in items] == ["facts"]


def test_summary_pools_proportional_evaluators():
    """Averaging per-row fractions over rows with different denominators favours
    the model that writes least; the ratio of sums does not."""
    project = _project()
    agent = Capability.objects.create(project=project, name="A", slug="a")
    dataset = Dataset.objects.create(project=project, capability=agent, name="d")
    run = EvalRun.objects.create(project=project, name="r", data_source="dataset", dataset=dataset)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)

    # One clean 1-claim row and one 5-claim row with a single miss. Mean of the
    # per-row fractions is 0.9; the true grounding rate is 5/6.
    for supported, judged in ((1, 1), (4, 5)):
        sample = EvalSample.objects.create(
            run=run, variant=variant, trajectory={"final_output": "x"}
        )
        Score.objects.create(
            project=project,
            run=run,
            variant=variant,
            sample=sample,
            name="Faithfulness",
            data_type="numeric",
            value=supported / judged,
            outcome="scored",
            sub_scores=[{"_proportion": {"supported": supported, "judged": judged}}],
        )

    summary = eval_tasks._build_summary(run)
    pooled = summary["pooled"][str(variant.id)]["Faithfulness"]
    assert pooled == {"supported": 5, "judged": 6, "rate": pytest.approx(5 / 6)}

    # The run view reads its headline from the metric row, so the pooled rate
    # has to land there or the mean is still what a person sees. The mean stays
    # alongside it: the paired comparison needs a per-row statistic.
    cell = summary["variants"][str(variant.id)]["metrics"]["Faithfulness"]
    assert cell["pooled"] == pytest.approx(5 / 6)
    assert cell["mean"] == pytest.approx(0.9)


def test_a_non_proportional_evaluator_keeps_the_mean_as_its_headline():
    """Only proportional evaluators pool; everything else has one denominator."""
    project = _project()
    agent = Capability.objects.create(project=project, name="A", slug="a")
    dataset = Dataset.objects.create(project=project, capability=agent, name="d")
    run = EvalRun.objects.create(project=project, name="r", data_source="dataset", dataset=dataset)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)

    for value in (1.0, 0.5):
        sample = EvalSample.objects.create(
            run=run, variant=variant, trajectory={"final_output": "x"}
        )
        Score.objects.create(
            project=project,
            run=run,
            variant=variant,
            sample=sample,
            name="Checklist Judge",
            data_type="numeric",
            value=value,
            outcome="scored",
            sub_scores=[{"id": "a", "verdict": True}],
        )

    cell = eval_tasks._build_summary(run)["variants"][str(variant.id)]["metrics"]["Checklist Judge"]
    assert "pooled" not in cell
    assert cell["mean"] == pytest.approx(0.75)


def test_summary_names_evaluators_that_never_scored():
    """A member that abstains on every row measured nothing, and reads as healthy
    unless the summary names it."""
    project = _project()
    agent = Capability.objects.create(project=project, name="A", slug="a")
    dataset = Dataset.objects.create(project=project, capability=agent, name="d")
    run = EvalRun.objects.create(project=project, name="r", data_source="dataset", dataset=dataset)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)

    for name, value in (("Works", 1.0), ("Inert", None)):
        for _ in range(3):
            sample = EvalSample.objects.create(
                run=run, variant=variant, trajectory={"final_output": "x"}
            )
            Score.objects.create(
                project=project,
                run=run,
                variant=variant,
                sample=sample,
                name=name,
                data_type="numeric",
                value=value,
                outcome="scored" if value is not None else "abstained",
            )

    assert eval_tasks._build_summary(run)["measurement"]["never_scored"] == ["Inert"]
    assert eval_tasks._build_summary(run)["measurement"]["uncovered_card_claims"] == []


def test_summary_names_uncovered_generate_card_claims():
    project = _project()
    capability = Capability.objects.create(
        project=project,
        name="A",
        slug="a-uncovered",
        improvement_metadata={
            "capability_card": {
                "success_criteria": [
                    "The report answers the user question with specific facts.",
                ]
            }
        },
    )
    dataset = Dataset.objects.create(project=project, capability=capability, name="d")
    run = EvalRun.objects.create(project=project, name="r", data_source="dataset", dataset=dataset)
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Task Success",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        rubric_md="Grade citations.",
        checklist=[
            {
                "id": "cites",
                "q": "Do citations in the report come from retrieved sources?",
                "weight": 1.0,
            }
        ],
        variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}],
    )
    _attach(run, evaluator)
    uncovered = eval_tasks._build_summary(run)["measurement"]["uncovered_card_claims"]
    assert any("specific facts" in c for c in uncovered)


def test_summary_gold_label_claim_covered_when_agreement_item_present():
    from conftest import frozen_dataset

    project = _project()
    capability = Capability.objects.create(
        project=project,
        name="Intent",
        slug=f"intent-{uuid.uuid4().hex[:8]}",
        improvement_metadata={
            "capability_card": {
                "success_criteria": ["Returned label matches the gold intent"],
            }
        },
    )
    dataset = frozen_dataset(
        project,
        [{"input": "cancel my order", "expected_output": "cancel_order"}],
        capability=capability,
        contract="eval",
    )
    run = EvalRun.objects.create(project=project, name="r", data_source="dataset", dataset=dataset)
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Task Success",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope="final_output",
        rubric_md="Grade labels.",
        checklist=[
            {
                "id": "gold",
                "q": "Does the output agree with {reference}?",
                "weight": 1.0,
            }
        ],
        variable_mapping=[
            {"var": "output", "source": "output", "jsonpath": ""},
            {"var": "reference", "source": "reference"},
        ],
        requires_reference=True,
    )
    _attach(run, evaluator)
    uncovered = eval_tasks._build_summary(run)["measurement"]["uncovered_card_claims"]
    assert not any("gold intent" in c.lower() for c in uncovered)


def test_summary_names_attached_evaluator_that_only_wrote_predictions():
    """Dataset-scope metrics write ``{name}__prediction`` abstains and no scored
    row; those must still surface as never_scored under the snapshot name."""
    project = _project()
    agent = Capability.objects.create(project=project, name="A", slug="a")
    dataset = Dataset.objects.create(project=project, capability=agent, name="d")
    run = EvalRun.objects.create(project=project, name="r", data_source="dataset", dataset=dataset)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)
    evaluator = Evaluator.objects.create(
        project=project,
        name="confidence-calibration",
        kind=Evaluator.Kind.STATISTICAL,
        scope=Evaluator.Scope.DATASET,
        config={"metric": "calibration"},
        version=1,
    )
    _attach(run, evaluator)
    for _ in range(3):
        sample = EvalSample.objects.create(
            run=run, variant=variant, trajectory={"final_output": "x"}
        )
        Score.objects.create(
            project=project,
            run=run,
            variant=variant,
            sample=sample,
            name="confidence-calibration__prediction",
            data_type="categorical",
            value=None,
            outcome="abstained",
        )

    assert eval_tasks._build_summary(run)["measurement"]["never_scored"] == [
        "confidence-calibration"
    ]


def test_count_sample_errors_surfaces_evaluator_errors_not_abstains():
    """Both carry value=None; the reasoning prefix is what separates an error from
    a legitimate abstain."""
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(capability.project, EVAL_ROWS, capability=capability)
    evaluator = Evaluator.objects.create(
        project=project,
        name="J",
        kind="llm_judge",
        scope="final_output",
        checklist=[{"id": "q1", "q": "Does the output satisfy the rubric?", "weight": 1.0}],
        version=1,
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    run_eval = _attach(run, evaluator)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)

    # All generated fine — no EvalSample.error.
    samples = [
        EvalSample.objects.create(run=run, variant=variant, trajectory={"final_output": "x"})
        for _ in range(3)
    ]

    def _score(sample, **kwargs):
        return Score.objects.create(
            project=project,
            run=run,
            variant=variant,
            sample=sample,
            evaluator=evaluator,
            run_evaluator=run_eval,
            name="J",
            scope="final_output",
            **kwargs,
        )

    # Counted: evaluator-call failures.
    _score(samples[0], data_type="numeric", value=None, reasoning="evaluator error: boom")
    _score(samples[1], data_type="numeric", value=None, reasoning="evaluator error: timeout")
    # Not counted: a legitimate abstain.
    _score(
        samples[2],
        data_type="numeric",
        value=None,
        reasoning="Insufficient evidence: nothing to judge.",
    )

    counts = eval_tasks._count_sample_errors(run)

    assert counts["errored"] == 0  # no sample generation failures
    assert counts["evaluator_errors"] == 2  # abstain excluded
    assert counts["by_variant"][str(variant.id)]["evaluator_errors"] == 2
    assert counts["by_variant"][str(variant.id)]["errored"] == 0


def test_execute_evaluator_error_uses_countable_prefix(monkeypatch):
    """A raising evaluator persists a None score whose reasoning is counted."""
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(capability.project, EVAL_ROWS, capability=capability)
    evaluator = Evaluator.objects.create(
        project=project,
        name="J",
        kind="llm_judge",
        scope="final_output",
        checklist=[{"id": "q1", "q": "Does the output satisfy the rubric?", "weight": 1.0}],
        version=1,
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    run_eval = _attach(run, evaluator)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing")
    sample = EvalSample.objects.create(run=run, variant=variant, trajectory={"final_output": "x"})

    def _boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(eval_tasks.eval_base, "evaluate", _boom)

    res = eval_tasks.execute_evaluator.apply(
        kwargs={"sample_id": str(sample.id), "run_evaluator_id": str(run_eval.id)}
    ).get()
    assert res["status"] == "error"

    score = Score.objects.get(sample=sample, run_evaluator=run_eval)
    assert score.value is None
    assert score.reasoning.startswith(eval_tasks._EVALUATOR_ERROR_PREFIX)

    counts = eval_tasks._count_sample_errors(run)
    assert counts["evaluator_errors"] == 1


def test_harness_artifact_evaluator_scores_in_existing_mode():
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(capability.project, EVAL_ROWS, capability=capability)
    evaluator = _harness_artifact_evaluator(project)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    run_eval = _attach(run, evaluator)
    variant = EvalVariant.objects.create(run=run, label="prod", mode="existing")
    structured_output = json.dumps({"recommendations": [{"body": "dedupe rows"}]})
    sample = EvalSample.objects.create(
        run=run, variant=variant, trajectory={"final_output": structured_output}
    )

    captured = {}

    def _fake_invoke_judge(prompt, **kwargs):
        captured["prompt"] = prompt
        return judging.JudgeOutcome(
            parsed=ChecklistResult(
                items=[ChecklistItem(id="recommendations_present", verdict=True)],
                reasoning="ok",
            ),
            raw="",
            stats={"response_cost": 0.0, "response_ms": 0},
            judge_trace_id="t",
        )

    with mock.patch.object(judging, "invoke_judge", _fake_invoke_judge):
        res = eval_tasks.execute_evaluator.apply(
            kwargs={"sample_id": str(sample.id), "run_evaluator_id": str(run_eval.id)}
        ).get()

    assert res["status"] != "not_applicable"
    assert "dedupe rows" in captured["prompt"]
    assert Score.objects.get(sample=sample, run_evaluator=run_eval).value == 1.0


def test_sample_units_for_dataset_and_dry_run():
    """The dry-run must normalize dataset rows exactly as the run path does."""
    from overbae.services.eval import binding_check

    project = _project()
    capability = Capability.objects.create(
        project=project,
        name="A",
        slug=f"a-{uuid.uuid4().hex[:8]}",
        output_fields={"recommendations": ""},
    )
    dataset = frozen_dataset(
        project,
        [
            {
                "input": [{"role": "user", "content": f"analyze run {i}"}],
                "expected_output": json.dumps({"recommendations": ["dedupe rows", "fix schema"]}),
            }
            for i in range(3)
        ],
        capability=capability,
    )

    units = binding_check.sample_units_for_dataset(dataset)
    assert len(units) == 3
    assert units[0].output_fields == {"recommendations": ""}

    healthy = SimpleNamespace(
        name="ok", variable_mapping=[{"var": "output", "source": "output", "jsonpath": ""}]
    )
    broken = SimpleNamespace(
        name="bad",
        variable_mapping=[
            {"var": "total", "source": "input", "jsonpath": "$.dataset_facts.total_rows_exact"}
        ],
    )
    health = binding_check.dry_run_bindings([healthy, broken], units)
    assert health["ok"].status == binding_check.GREEN
    assert health["bad"].status == binding_check.RED
    assert health["bad"].failing[0].var == "total"


def test_generate_mode_prefers_recent_run_samples():
    from overbae.services.eval import binding_check, evidence

    project = _project()
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    # Empty row output is what over-flags output bindings on a dry-run.
    dataset = frozen_dataset(
        project,
        [
            {"input": [{"role": "user", "content": f"analyze {i}"}], "expected_output": ""}
            for i in range(2)
        ],
        capability=capability,
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source=EvalRun.DataSource.DATASET,
        dataset=dataset,
        cell=dataset.active_cell,
    )
    variant = EvalVariant.objects.create(run=run, label="v", mode=EvalVariant.Mode.GENERATE)
    for i in range(2):
        EvalSample.objects.create(
            run=run,
            variant=variant,
            trajectory={
                "final_output": json.dumps({"recommendations": ["dedupe", "fix"]}),
                "messages": [{"role": "user", "content": f"analyze {i}"}],
                "metadata": {},
            },
        )

    units = binding_check.sample_units_for_dataset(dataset, mode=evidence.GENERATE)
    assert units, "expected units sourced from the generate run"
    assert all(u.trajectory.get("final_output") for u in units)

    spec = SimpleNamespace(
        name="recs", variable_mapping=[{"var": "recs", "source": "output", "jsonpath": ""}]
    )
    health = binding_check.dry_run_bindings([spec], units, mode=evidence.GENERATE)
    assert health["recs"].status == binding_check.GREEN


def test_generate_mode_skips_output_binding_without_run_samples():
    """Generate populates the output at grade time, so an unresolvable output
    binding is skipped rather than red."""
    from overbae.services.eval import binding_check, evidence

    project = _project()
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    dataset = frozen_dataset(
        project,
        [
            {"input": [{"role": "user", "content": f"analyze {i}"}], "expected_output": ""}
            for i in range(2)
        ],
        capability=capability,
    )

    units = binding_check.sample_units_for_dataset(dataset, mode=evidence.GENERATE)
    assert units  # datapoint-derived fallback
    spec = SimpleNamespace(
        name="recs", variable_mapping=[{"var": "recs", "source": "output", "jsonpath": ""}]
    )
    health = binding_check.dry_run_bindings([spec], units, mode=evidence.GENERATE)
    assert health["recs"].status == binding_check.SKIPPED
    # Existing mode keeps the strict behavior: empty output is a genuine failure.
    health_existing = binding_check.dry_run_bindings([spec], units, mode=evidence.EXISTING)
    assert health_existing["recs"].status == binding_check.RED


def test_prepare_sample_generation_timeout_marks_sample_errored(monkeypatch):
    """A soft time limit marks the sample errored — no hang, no retry, worker freed."""
    from celery.exceptions import SoftTimeLimitExceeded

    project = _project()
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    dataset = frozen_dataset(
        capability.project, [{"input": "q1", "expected_output": "a1"}], capability=capability
    )
    dp = _row(dataset, 0)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    variant = EvalVariant.objects.create(
        run=run, label="v", mode="generate", model_name="gpt-5-mini"
    )
    sample = EvalSample.objects.create(run=run, variant=variant, row_index=dp.index, expected="a1")

    def _hang(_sample):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(eval_tasks, "_generate_sample", _hang)

    # Returns normally — nothing propagates, so Celery does not retry.
    result = eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    assert result == str(sample.id)

    sample.refresh_from_db()
    assert "timed out" in sample.error
    assert sample.trajectory == {}


def test_cancel_run_revokes_inflight_tasks():
    """Revokes the parent and every sample task with terminate=True so hung
    generation calls stop occupying worker threads."""
    project = _project()
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    dataset = frozen_dataset(capability.project, EVAL_ROWS, capability=capability)
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        cell=dataset.active_cell,
        celery_task_id="run-task-id",
    )
    variant = EvalVariant.objects.create(run=run, label="v", mode="generate")
    s1 = EvalSample.objects.create(run=run, variant=variant, celery_task_id="sample-task-1")
    s2 = EvalSample.objects.create(run=run, variant=variant, celery_task_id="sample-task-2")
    # A sample with no task id (never dispatched) must not contribute an empty id.
    EvalSample.objects.create(run=run, variant=variant, celery_task_id="")

    with mock.patch("celery.current_app.control.revoke") as revoke:
        revoked = eval_tasks.revoke_run_tasks(run)

    assert revoked == 3
    revoke.assert_called_once()
    args, kwargs = revoke.call_args
    revoked_ids = set(args[0])
    assert revoked_ids == {"run-task-id", s1.celery_task_id, s2.celery_task_id}
    assert kwargs["terminate"] is True
    assert kwargs["signal"] == "SIGTERM"


def _row(dataset, index):
    return _dataset_row(dataset.active_cell, index)
