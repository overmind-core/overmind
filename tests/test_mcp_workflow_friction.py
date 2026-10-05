import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient
from test_data_first_workflow import workspace
from test_mcp_research_journey import call
from test_workshop_semantic_checks import dataset as dataset
from test_workshop_semantic_checks import provider as provider
from test_workshop_semantic_checks import request

from overbae.models import APIToken, Cell, Dataset
from overbae.services import native_evaluation
from overbae.services.datasets import land, paths, review, semantic_checks, store, use
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.notebook.agent import Tools
from overbae.services.datasets.partition_plans import build, prepared_member_rows, request_plan
from overbae.services.eval.funnel import JudgeOutcome
from overbae.services.mcp.server import create_mcp_application
from overbae.services.training_forecast import forecast

pytestmark = pytest.mark.django_db(transaction=True)


def test_semantic_findings_survive_later_unknown_script_and_still_resume(dataset, provider):
    cell = dataset.active_cell
    semantic_checks.run_checks(dataset, cell, request(max_rows=1))
    measured = store.read_frame(paths.cell_path(dataset.id, cell.id))[[store.SOURCE_ROW]]
    measured["answer_support"] = None
    measured["schema"] = True
    saved = review.record_quality_results(
        dataset,
        cell,
        [
            {"name": "answer_support", "evidence": "Not measured by this script"},
            {"name": "schema", "evidence": "Required columns present"},
        ],
        measured,
        audit={"method": "row_results", "script": "fixture"},
        reviewer="workshop_agent",
        context=context_fingerprint(None),
    )
    check = next(c for c in saved["checks"] if c["name"] == "answer_support")
    assert (check["rows_checked"], check["rows_unknown"]) == (1, 1)
    assert saved["audits"]["answer_support"]["method"] == "semantic_decisions"
    finished = semantic_checks.run_checks(dataset, cell, request(max_rows=1))
    assert finished["remaining_rows"] == 0
    check = next(c for c in finished["quality_report"]["checks"] if c["name"] == "answer_support")
    assert check["rows_failed"] == 1
    assert provider.call_count == 2


@pytest.mark.parametrize(
    "evidence,answer",
    [("decision", "decision.target_probabilities"), ("decision.options", "decision")],
)
def test_nested_answer_cannot_be_in_its_ancestor_evidence(evidence, answer):
    with pytest.raises(ValidationError, match="independent evidence"):
        semantic_checks.SemanticCheck(
            name="answer_support",
            question="Supported?",
            evidence_columns=[evidence],
            answer_columns=[answer],
        )


def test_native_nested_evidence_projects_only_requested_fields(dataset, monkeypatch):
    native = Dataset.objects.create(project=dataset.project, name="Native", intent="train")
    original = {
        "state": "",
        "question": "Which?",
        "kind": "choice",
        "options": ["a", "b"],
        "target_probabilities": [0.25, 0.75],
        "private_metadata": "do not send",
    }
    land.land_rows(native, [{"decision": original}])
    native.refresh_from_db()
    seen = []

    def invoke(state, questions, **kwargs):
        seen.extend(state["rows"].values())
        return JudgeOutcome(
            parsed=SimpleNamespace(answers=dict.fromkeys(questions, "pass")),
            raw="{}",
            stats={"response_cost": 0},
            judge_trace_id="nested-fixture",
        )

    monkeypatch.setattr(semantic_checks.decisions, "invoke", invoke)
    result = semantic_checks.run_checks(
        native,
        native.active_cell,
        semantic_checks.SemanticReviewRequest(
            checks=[
                semantic_checks.SemanticCheck(
                    name="answer_support",
                    question="Supported?",
                    evidence_columns=["decision.state", "decision.question", "decision.options"],
                    answer_columns=["decision.target_probabilities"],
                )
            ]
        ),
    )
    assert result["processed_rows"] == 1
    assert seen[0]["decision.state"] == ""
    assert seen[0]["decision.target_probabilities"] == [0.25, 0.75]
    assert "decision" not in seen[0] and "private_metadata" not in json.dumps(seen)
    assert (
        next(store.iter_rows(paths.cell_path(native.id, native.active_cell.id)))["decision"]
        == original
    )


def test_mcp_inspection_is_bounded_paged_and_does_not_profile_on_read(monkeypatch):
    project, _, dataset = workspace()
    cell = dataset.active_cell
    dataset.active = cell
    dataset.save(update_fields=["active"])
    cell.note = "long note " * 1000
    cell.stats = {}
    cell.quality_report = {
        "checks": [],
        "audit": {"script": "x" * 100000},
        "audits": {str(i): {"script": "x" * 100000} for i in range(20)},
    }
    cell.save()
    for i in range(1, 13):
        Cell.objects.create(
            dataset=dataset,
            position=i,
            state="ok",
            title=f"Cell {i}",
            fingerprint="a" * 64,
            script="x" * 10000,
            note="note " * 1000,
            intent_report=cell.intent_report,
        )
    key, _ = APIToken.create_for_user(
        project.memberships.first().user, project=project, permission=["read", "write"]
    )
    monkeypatch.setattr(
        "overbae.services.datasets.context.profile_records",
        Mock(side_effect=AssertionError("Inspection scanned whole source")),
    )
    with TestClient(create_mcp_application()) as client:
        found = []
        offset = 0
        while True:
            result = call(
                client,
                key,
                "inspect_dataset",
                {"dataset": str(dataset.id), "cell_offset": offset, "cell_limit": 4},
            )
            assert len(json.dumps(result).encode()) < 64000
            assert result["active"]["id"] == str(cell.id)
            found.extend(c["id"] for c in result["cells"])
            if not result["cell_page"]["has_more"]:
                break
            offset = int(result["cell_page"]["next_cursor"])
        assert len(found) == len(set(found)) == 13


def test_native_partition_members_are_ready_for_comparison_without_workshop_repair():
    project, _, source = workspace()
    raw = Dataset.objects.create(project=project, name="Native only", intent="train")
    records = [
        {
            "decision": {
                "state": "" if i == 0 else str(i),
                "question": f"Question {i}",
                "kind": "choice",
                "options": ["a", "b"],
                "target_probabilities": [0.35, 0.65],
            },
            "group_id": str(i),
        }
        for i in range(40)
    ]
    land.land_rows(raw, records)
    raw.refresh_from_db()
    fingerprint = raw.active_cell.fingerprint
    plan = request_plan(
        project,
        name="Prepared roles",
        request_key="prepared-roles",
        source_cell=raw.active_cell,
        recipe={
            "seed": 37,
            "fractions": {"train": 0.5, "development": 0.2, "calibration": 0.15, "final": 0.15},
        },
    )
    build(plan.id)
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    members = {m.role: m.cell for m in plan.members.select_related("cell__dataset")}
    for role, cell in members.items():
        assert cell.dataset.intent == ("eval" if role in ("calibration", "final") else "train")
        if cell.dataset.intent == "eval":
            for row in store.iter_rows(paths.cell_path(cell.dataset_id, cell.id)):
                assert row["expected_output"] == {"probabilities": [0.35, 0.65]}
                assert set(row["input"]["decision"]) == {"state", "question", "kind", "options"}
                assert row["_overmind_provenance"]["partition"]["role"] == role
    with patch.object(
        native_evaluation, "runtime", return_value={"app": "offline", "environment": "test"}
    ):
        draft = native_evaluation.create_plan(
            project,
            name="Ready",
            request_key="ready",
            final_cell=members["final"],
            calibration_cell=members["calibration"],
            baseline="base",
            participants=[
                {"kind": "foundation", "key": "base", "name": "Base", "model": "Qwen/Qwen3.5-4B"}
            ],
        )
    assert draft.state == "draft"
    raw.active_cell.refresh_from_db()
    assert raw.active_cell.fingerprint == fingerprint
    assert sum(m.rows for m in members.values()) == 40
    assert plan.report["progress"]["stage"] == "completed"


def test_forecast_distinguishes_current_price_from_missing_duration():
    with (
        patch("overbae.services.training_forecast.candidates", return_value=[]),
        patch("overbae.services.training_forecast.hardware", return_value=("H200", 1)),
    ):
        quote = forecast("project", "model", {}, tokens=100, stats={})
    assert quote["price_status"] == "current"
    assert quote["gpu_hour_usd"] == 4.54
    assert quote["duration_status"] == "unmeasured"
    assert quote["training_seconds"] is None and quote["training_gpu_usd"] is None
    assert quote["estimate_blockers"] == ["duration_unmeasured"]


def test_workshop_observed_version_survives_consumer_freezing(dataset):
    cell = dataset.active_cell
    tools = Tools(dataset.id, None, lambda _: None)
    before = tools.status({})
    version = before["active"]
    use.freeze(cell)
    result = tools.query({"version": version, "sql": "SELECT COUNT(*) AS n FROM t"})
    assert result["rows"] == [{"n": 2}], result
    assert tools.exploration[-1]["cell"] == str(cell.id)


def test_inspection_remains_valid_json_for_one_wide_unicode_cell():
    project, _, dataset = workspace()
    cell = dataset.active_cell
    cell.script = "界" * 8000
    cell.note = "界" * 8000
    cell.columns = [{"name": "column" + str(i), "metadata": "界" * 8000} for i in range(100)]
    cell.quality_report = {
        "checks": [],
        "audit": {("界" * 1000 + str(i)): "界" * 8000 for i in range(100)},
    }
    cell.save()
    dataset.brief = "界" * 8000
    dataset.chat = [{"role": "agent", "text": "界" * 8000}]
    dataset.save()
    key, _ = APIToken.create_for_user(
        project.memberships.first().user, project=project, permission=["read", "write"]
    )
    with TestClient(create_mcp_application()) as client:
        result = call(client, key, "inspect_dataset", {"dataset": str(dataset.id)})
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 34000
    assert result["cells"][0]["id"] == str(cell.id)
    assert result["cells"][0]["rows"] == cell.rows
    assert result["truncated_fields"]


def test_script_cannot_replace_measured_semantic_failure_with_success(dataset, provider):
    cell = dataset.active_cell
    semantic_checks.run_checks(dataset, cell, request())
    frame = store.read_frame(paths.cell_path(dataset.id, cell.id))[[store.SOURCE_ROW]]
    frame["answer_support"] = True
    with pytest.raises(ValueError, match="semantic"):
        review.record_quality_results(
            dataset,
            cell,
            [{"name": "answer_support", "evidence": "claimed pass"}],
            frame,
            audit={"method": "row_results"},
            reviewer="workshop_agent",
            context=context_fingerprint(None),
        )
    cell.refresh_from_db()
    assert cell.quality_report["checks"][0]["rows_failed"] == 1


def test_partition_projection_preserves_mean_only_and_invalid_targets():
    decisions = [
        {
            "state": "",
            "question": "Rate",
            "kind": "ordinal",
            "options": ["low", "high"],
            "target_mean": 0.7,
            "option_values": [0, 1],
        },
        {
            "state": "",
            "question": "Pick",
            "kind": "choice",
            "options": ["a", "b"],
            "target_probabilities": [0.8, 0.8],
        },
    ]
    records = [{"decision": decision, "weight": 2, "group_id": "shared"} for decision in decisions]
    projected = list(prepared_member_rows([json.dumps(r) for r in records], "calibration"))
    assert [r["decision"] for r in projected] == decisions
    assert projected[0]["expected_output"] == {"mean": 0.7, "values": [0, 1]}
    assert projected[1]["expected_output"] == {"probabilities": [0.8, 0.8]}
    assert all(r["weight"] == 2 and r["group_id"] == "shared" for r in projected)
