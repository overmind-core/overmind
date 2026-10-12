from __future__ import annotations

from unittest.mock import patch

import pytest
from factories import make_project, make_user
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.modal.decision_catalog import models
from overbae.models import DecisionJob, ProjectMembership, User

pytestmark = pytest.mark.django_db


def _client(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def _record() -> dict:
    return {
        "state": "The invoice is unpaid.",
        "questions": {
            "pay": {
                "type": "choice",
                "keys": ["yes", "no"],
            }
        },
        "gold": {"pay": "yes"},
    }


def test_catalog_caps_every_checkpoint_at_h100_context():
    rows = models()
    assert len(rows) == 10
    assert "OvermindLab/lfm-2.5-2.6B-decision" not in rows
    assert {row["family"] for row in rows.values()} == {"clef", "d3"}
    assert {row["max_context_length"] for row in rows.values()} == {32768}


def test_decision_job_rejects_an_unknown_model_and_starts_a_catalog_model():
    user, project = make_user(), make_project()
    ProjectMembership.objects.create(user=user, project=project)
    client = _client(user)
    rejected = client.post(
        "/api/decision-jobs/",
        {"project": str(project.id), "model": "missing/model", "records": [_record()]},
        format="json",
    )
    assert rejected.status_code == 400

    class _Call:
        object_id = "fc-decision"

    with patch("modal.Function.from_name") as lookup:
        lookup.return_value.spawn.return_value = _Call()
        started = client.post(
            "/api/decision-jobs/",
            {"project": str(project.id), "model": "vllm-sr/d3-edge", "records": [_record()]},
            format="json",
        )
    assert started.status_code == 202
    assert started.data["status"] == "running"
    assert started.data["model_id"] == "vllm-sr/d3-edge"
    assert started.data["record_count"] == 1
    lookup.assert_called_once()
    assert lookup.call_args.args[1] == "train_decision"


def test_decision_job_retrieve_records_the_worker_result():
    user, project = make_user(), make_project()
    ProjectMembership.objects.create(user=user, project=project)
    job = DecisionJob.objects.create(
        project=project,
        model_id="vllm-sr/d3-edge",
        status=DecisionJob.Status.RUNNING,
        records=[_record()],
        remote_id="fc-decision",
    )
    result = {"model": "vllm-sr/d3-edge", "peak_allocated_gb": 4.2, "steps": 3}
    with patch("modal.FunctionCall.from_id") as lookup:
        lookup.return_value.get.return_value = result
        response = _client(user).get(f"/api/decision-jobs/{job.id}/")
    assert response.status_code == 200
    assert response.data["status"] == "succeeded"
    assert response.data["result"]["peak_allocated_gb"] == 4.2


def test_systemone_returns_answers_and_rejects_context_overflow():
    user, project = make_user(), make_project()
    ProjectMembership.objects.create(user=user, project=project)
    client = _client(user)
    body = {
        "project": str(project.id),
        "state": "The invoice is unpaid.",
        "questions": {"pay": {"type": "noul"}},
    }
    unknown = client.post("/api/decision-models/missing/model/systemone", body, format="json")
    assert unknown.status_code == 400

    payload = {
        "model": "Cloudflare/clef",
        "answers": {"pay": {"noul": 0.2}},
        "usage": {"input_tokens": 12, "output_tokens": 0},
    }
    with patch("modal.Cls.from_name") as lookup:
        lookup.return_value.return_value.score.remote.return_value = payload
        answered = client.post(
            "/api/decision-models/Cloudflare/clef/systemone", body, format="json"
        )
    assert answered.status_code == 200
    assert answered.data["answers"]["pay"]["noul"] == 0.2
    assert answered.data["usage"]["input_tokens"] == 12
    assert lookup.call_args.args[1] == "ClefServer"
    assert lookup.return_value.call_args.kwargs["source"] == "Cloudflare--clef"

    with patch("modal.Cls.from_name") as lookup:
        lookup.return_value.return_value.score.remote.return_value = {
            "error": "max_length_exceeded"
        }
        overflow = client.post(
            "/api/decision-models/Cloudflare/clef/systemone", body, format="json"
        )
    assert overflow.status_code == 400
    assert "max_length_exceeded" in str(overflow.data)


def test_trained_checkpoint_inference_uses_the_saved_artifact():
    user, project = make_user(), make_project()
    ProjectMembership.objects.create(user=user, project=project)
    job = DecisionJob.objects.create(
        project=project,
        model_id="Cloudflare/clef",
        status=DecisionJob.Status.SUCCEEDED,
        records=[_record()],
        result={"saved": "/artifacts/job/adapter"},
    )
    payload = {
        "model": "Cloudflare/clef",
        "answers": {"pay": {"choice": "yes"}},
        "usage": {"input_tokens": 4, "output_tokens": 0},
    }
    with patch("modal.Cls.from_name") as lookup:
        lookup.return_value.return_value.score.remote.return_value = payload
        answered = _client(user).post(
            f"/api/decision-jobs/{job.id}/systemone/",
            {"state": "The invoice is unpaid.", "questions": {"pay": {"type": "choice"}}},
            format="json",
        )
    assert answered.status_code == 200, answered.data
    sent = lookup.return_value.return_value.score.remote.call_args.args[0]
    assert sent["adapter"] == "/artifacts/job/adapter"
    assert lookup.return_value.call_args.kwargs["source"] == "Cloudflare--clef"
    assert answered.data["answers"]["pay"]["choice"] == "yes"

    d3 = DecisionJob.objects.create(
        project=project,
        model_id="vllm-sr/d3-edge",
        status=DecisionJob.Status.SUCCEEDED,
        records=[_record()],
        result={"saved": "/artifacts/d3-job"},
    )
    with patch("modal.Cls.from_name") as lookup:
        lookup.return_value.return_value.score.remote.return_value = payload
        d3_answered = _client(user).post(
            f"/api/decision-jobs/{d3.id}/systemone/",
            {"state": "The invoice is unpaid.", "questions": {"pay": {"type": "choice"}}},
            format="json",
        )
    assert d3_answered.status_code == 200, d3_answered.data
    assert lookup.call_args.args[1] == "D3Server"
    assert lookup.return_value.call_args.kwargs["source"] == "--artifacts--d3-job"
    assert "adapter" not in lookup.return_value.return_value.score.remote.call_args.args[0]

    waiting = DecisionJob.objects.create(
        project=project,
        model_id="Cloudflare/clef",
        status=DecisionJob.Status.RUNNING,
        records=[_record()],
    )
    early = _client(user).post(
        f"/api/decision-jobs/{waiting.id}/systemone/",
        {"state": "The invoice is unpaid.", "questions": {"pay": {"type": "choice"}}},
        format="json",
    )
    assert early.status_code == 400
