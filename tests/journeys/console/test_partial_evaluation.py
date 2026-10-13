import json
from datetime import timedelta
from unittest.mock import patch

import httpx
from django.utils import timezone
from factories import make_project

from overbae.models import Dataset, NativeEvaluationPlan
from overbae.services import native_evaluation
from overbae.services.datasets import land
from overbae.tasks.native_evaluation import advance_plan


def test_exhausted_evaluation_reports_partial_coverage_across_console_rest_mcp(
    console, account, rest_for, mcp_for, scripted, monkeypatch, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setenv("JEV_REQUESTS_PER_MINUTE", "10000000")
    monkeypatch.setenv("JEV_TOKENS_PER_SECOND", "10000000")
    user, key = account
    project = make_project("Partial evaluation", member=user)
    dataset = Dataset.objects.create(project=project, name="Frozen final", intent="eval")
    land.land_rows(
        dataset,
        [
            {
                "input": {
                    "decision": {
                        "state": f"Evidence {i}",
                        "question": "Choose",
                        "kind": "choice",
                        "options": ["a", "b"],
                    }
                },
                "expected_output": {"probabilities": [1, 0]},
            }
            for i in range(2)
        ],
    )
    dataset.refresh_from_db()

    def response(call):
        if call.json["state"] == "Evidence 0":
            return httpx.ReadTimeout("response lost")
        return (
            200,
            {"content-type": "application/json"},
            json.dumps(
                {
                    "id": "fixture",
                    "provider": "TypeSafe",
                    "model": "typesafe/jev-1.13-20260917",
                    "usage": {"cost": 0.002},
                    "answers": {
                        "decision": {
                            "type": "choice",
                            "choice": "0",
                            "probabilities": {"0": 0.9, "1": 0.1},
                        }
                    },
                }
            ).encode(),
        )

    provider = scripted("https://openrouter.ai/api/v1/systemone").then(response)
    mcp = mcp_for(key)
    created = mcp.call(
        "create_native_evaluation",
        {
            "project_id": str(project.pk),
            "name": "Available results",
            "request_key": "partial",
            "final_cell": str(dataset.active_cell.pk),
            "baseline": "external",
            "participants": [
                {
                    "key": "external",
                    "name": "External",
                    "kind": "external",
                    "model": "typesafe/jev-1.13",
                    "served_model": "typesafe/jev-1.13-20260917",
                }
            ],
            "bootstrap_samples": 2,
            "inference": {"concurrency": 1},
        },
    )
    identifier = created["workflow"]["id"]
    with patch.object(advance_plan, "delay"):
        mcp.call(
            "launch_native_evaluation", {"project_id": str(project.pk), "evaluation": identifier}
        )
    native_evaluation.advance(identifier, stage="verify_inputs")
    for _ in range(3):
        native_evaluation.advance(identifier, stage="final_external")
        plan = NativeEvaluationPlan.objects.get(pk=identifier)
        plan.calls["final_external"]["retry_at"] = (
            timezone.now() - timedelta(seconds=1)
        ).isoformat()
        plan.save()
    native_evaluation.advance(identifier, stage="score")
    job = mcp.call(
        "get_job", {"project_id": str(project.pk), "kind": "native_evaluation", "id": identifier}
    )
    assert job["status"] == "completed"
    completion = job["progress"]["results"]["completion"]
    assert completion["status"] == "completed_with_issues"
    assert completion["coverage"]["final"]["external"]["scored"] == 1
    assert completion["coverage"]["final"]["external"]["missing_predictions"] == 1
    rest = rest_for(key)
    assert (
        rest.request("GET", f"/api/native-evaluations/{identifier}/")["results"]["completion"]
        == completion
    )
    assert (
        rest.request("GET", f"/api/native-evaluations/{identifier}/report/?format=json")[
            "completion"
        ]
        == completion
    )
    assert len(provider.calls) == 4
    console.goto(f"/evaluations/runs/{identifier}?projectId={project.pk}&format=decision")
    console.sees("Completed with issues")
    console.sees("External: 1 / 2 scored · 1 missing · 0 invalid · 0 incompatible")
    console.save_evidence("partial-evaluation-desktop")
    console._browser.call(console._page.set_viewport_size, {"width": 390, "height": 844})
    console.wait_until("() => document.documentElement.scrollWidth <= innerWidth")
    console.save_evidence("partial-evaluation-mobile")
