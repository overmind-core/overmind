import json
from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient
from test_data_first_workflow import workspace
from test_mcp_research_journey import call

from overbae.models import APIToken, Cell, Dataset
from overbae.services import native_evaluation
from overbae.services.datasets import land, paths, store
from overbae.services.datasets.partition_plans import build, prepared_member_rows, request_plan
from overbae.services.mcp.server import create_mcp_application
from overbae.services.training_forecast import forecast

pytestmark = pytest.mark.django_db(transaction=True)


def test_mcp_inspection_is_bounded_paged_and_does_not_profile_on_read(monkeypatch):
    project, _, dataset = workspace()
    cell = dataset.active_cell
    dataset.active = cell
    dataset.save(update_fields=["active"])
    cell.note = "long note " * 51
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
            note="note " * 102,
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


def test_inspection_remains_valid_json_for_one_wide_unicode_cell():
    project, _, dataset = workspace()
    cell = dataset.active_cell
    cell.script = "界" * 8000
    cell.note = "界" * 512
    cell.columns = [{"name": "column" + str(i), "metadata": "界" * 8000} for i in range(100)]
    cell.quality_report = {
        "checks": [],
        "audit": {("界" * 1000 + str(i)): "界" * 8000 for i in range(100)},
    }
    cell.save()
    dataset.brief = "界" * 8000
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
