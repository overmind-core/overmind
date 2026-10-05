import json

import pytest
from rest_framework.test import APIClient
from starlette.testclient import TestClient
from test_data_first_workflow import workspace
from test_mcp_research_journey import call

from overbae.models import APIToken, NativeEvaluationPlan, Project
from overbae.services.mcp.server import create_mcp_application
from overbae.services.native_evaluation import directory

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("pending", [{}, {"receipt": None}])
def test_pending_evaluation_receipts_remain_readable_until_completion(pending):
    plan, key = completed_report()
    plan.state = "running"
    plan.results = {}
    plan.calls = {"calibration_candidate": {"id": "existing-call", "state": "running", **pending}}
    plan.save(update_fields=["state", "results", "calls"])
    with TestClient(create_mcp_application()) as client:
        for complete in (False, True):
            if complete:
                plan.calls["calibration_candidate"].update(
                    state="completed",
                    receipt={"decisions": 210, "predictions_sha256": "sealed", "tokens": [1, 2]},
                )
                plan.save(update_fields=["calls"])
            job = call(client, key, "get_job", {"kind": "native_evaluation", "id": str(plan.pk)})
            observed = job["progress"]["calls"]["calibration_candidate"]
            assert observed["id"] == "existing-call"
            assert observed["state"] == ("completed" if complete else "running")
            assert observed["receipt"] == (
                {"decisions": 210, "predictions_sha256": "sealed"} if complete else None
            )
            resource = client.post(
                "/api/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "resources/read",
                    "params": {"uri": job["resource"]["uri"]},
                },
                headers={"X-Api-Key": key, "Accept": "application/json"},
            ).json()["result"]
            assert (
                json.loads(resource["contents"][0]["text"])["calls"]["calibration_candidate"]
                == observed
            )


def completed_report():
    project, _, dataset = workspace()
    key, _ = APIToken.create_for_user(
        project.memberships.first().user, project=project, permission=["read"]
    )
    benchmarks = {
        f"source-{index}": {
            "expected": 2,
            "scored": 2,
            "missing_predictions": 0,
            "invalid_predictions": 0,
            "incompatible_inputs": 0,
            "metrics": {"accuracy": {"mean": 0.5, "decisions": 2}},
        }
        for index in range(105)
    }
    benchmarks["source-104"].update(scored=1, missing_predictions=1)
    summary = {
        "baseline": {"benchmarks": benchmarks, "macro_across_benchmarks": {}},
        "candidate": {"benchmarks": benchmarks, "macro_across_benchmarks": {}},
        "benchmarks": {
            name: {"expected": 2, "paired_decisions": value["scored"]}
            for name, value in benchmarks.items()
        },
        "macro_across_benchmarks": {},
    }
    results = {
        "baseline": "base",
        "comparisons": {"candidate": {"raw": summary}},
        "calibration": {"in_sample": True, "comparisons": {"candidate": {"raw": summary}}},
    }
    plan = NativeEvaluationPlan.objects.create(
        project=project,
        name="All source results",
        final_cell=dataset.active_cell,
        state="completed",
        results=results,
        calls={"score": {"state": "completed", "receipt": results}},
    )
    path = directory(plan, "report")
    path.mkdir(parents=True)
    (path / "results.json").write_text(json.dumps(results))
    (path / "results.md").write_text("# Complete report\n\nsource-104: 1 missing prediction\n")
    return plan, key


def test_mcp_summary_counts_every_benchmark_and_links_complete_project_scoped_reports():
    plan, key = completed_report()
    with TestClient(create_mcp_application()) as client:
        job = call(client, key, "get_job", {"kind": "native_evaluation", "id": str(plan.pk)})
        progress = job["progress"]
        assert len(json.dumps(job)) < 20_000
        for result in (progress["results"], progress["results"]["calibration"]):
            raw = result["comparisons"]["candidate"]["raw"]
            assert raw["benchmark_count"] == 105
            assert raw["candidate"]["coverage"]["expected"] == 210
            assert raw["candidate"]["coverage"]["scored"] == 209
            assert raw["candidate"]["coverage"]["missing_predictions"] == 1
        resource = client.post(
            "/api/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "resources/read",
                "params": {"uri": job["resource"]["uri"]},
            },
            headers={"X-Api-Key": key, "Accept": "application/json"},
        ).json()["result"]
        assert json.loads(resource["contents"][0]["text"])["report"] == progress["report"]
    api = APIClient()
    api.credentials(HTTP_X_API_KEY=key)
    response = api.get(progress["report"]["json_path"])
    assert response.status_code == 200
    report = json.loads(b"".join(response.streaming_content))
    assert len(report["comparisons"]["candidate"]["raw"]["benchmarks"]) == 105
    assert "source-104" in report["comparisons"]["candidate"]["raw"]["benchmarks"]
    foreign = Project.objects.create(name="Foreign", slug="foreign-reports")
    foreign_key, _ = APIToken.create_for_user(
        plan.project.memberships.first().user, project=foreign, permission=["read"]
    )
    api.credentials(HTTP_X_API_KEY=foreign_key)
    assert api.get(progress["report"]["json_path"]).status_code == 404


@pytest.mark.parametrize("format", ["json", "md"])
def test_report_format_downloads_without_renderer_negotiation_failure(format):
    plan, key = completed_report()
    api = APIClient()
    api.credentials(HTTP_X_API_KEY=key)
    path = f"/api/native-evaluations/{plan.pk}/report/?format={format}"
    response = api.get(path)
    assert response.status_code == 200
    assert b"source-104" in b"".join(response.streaming_content)
    plan.state = "running"
    plan.save(update_fields=["state"])
    assert api.get(path).status_code == 400
    assert api.get(f"/api/native-evaluations/{plan.pk}/report/?format=invalid").status_code == 400
