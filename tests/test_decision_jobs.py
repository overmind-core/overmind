from __future__ import annotations

import json

import pytest
from factories import auth_client, make_project, make_user
from fakes.modal import DecisionBackend

from overbae.models import BillingService, BillingTelemetry, DecisionJob
from overbae.services import decision_jobs

pytestmark = pytest.mark.django_db

ROUTE = {
    "type": "choice",
    "instructions": "Which team handles this ticket?",
    "criteria": {"billing": "Billing", "shipping": "Shipping"},
}
URGENT = {"type": "noul", "instructions": "Is it urgent?"}
TONE = {
    "type": "score",
    "instructions": "How upset is the customer?",
    "criteria": ["calm", "upset", "angry"],
}


def _row(state: str, route="billing", urgent="false", tone=0) -> dict:
    return {
        "input": {"state": state, "questions": {"route": ROUTE, "urgent": URGENT, "tone": TONE}},
        "expected_output": {"route": route, "urgent": urgent, "tone": tone},
    }


ROWS = [
    _row("Ticket: I was charged twice for order 1002."),
    _row("Ticket: my parcel never arrived.", route={"shipping": 3, "billing": 1}, urgent="true"),
    _row("Ticket: refund me now!", tone=2),
]


@pytest.fixture
def decision(fake_modal) -> DecisionBackend:
    return DecisionBackend(fake_modal).install()


@pytest.fixture
def owner():
    user = make_user()
    return user, make_project(member=user), auth_client(user)


def _job(client, project, train, evaluation=None, **body):
    payload = {"project": str(project.id), "model": "vllm-sr/d3-edge", "dataset": str(train.id)}
    if evaluation is not None:
        payload["eval_dataset"] = str(evaluation.id)
    return client.post("/api/decision-jobs/", {**payload, **body}, format="json")


def test_decision_rows_are_their_own_format_and_never_reach_chat_consumers(owner, make_dataset):
    _, project, _ = owner
    dataset = make_dataset(project, ROWS)
    report = dataset.active_cell.intent_report

    assert report["decision"] == {"ok": True, "reason": "", "rows": 3}
    assert report["train"]["ok"] is False
    assert report["eval"] == {
        "ok": False,
        "reason": "decision rows evaluate decision models, not chat models",
        "input_type": "decision",
        "has_reference": True,
    }
    assert dataset.intent == "train"


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (_row("x", route="refund"), "answer 'refund' is not one of billing, shipping"),
        (_row("x", tone=3), "answer '3' is not one of 0, 1, 2"),
        (_row("x", urgent="maybe"), "answer 'maybe' is not one of true, false"),
        (_row("x", route={"billing": -1}), "answer weights must be finite and not negative"),
        (
            {**_row("x"), "expected_output": {"route": "billing", "urgent": "true"}},
            "no answer for tone",
        ),
        (
            {
                "input": {"state": "x", "questions": {"q": {"type": "rank", "instructions": "?"}}},
                "expected_output": {"q": "a"},
            },
            "question q: type must be choice, noul or score",
        ),
        (
            {
                "input": {"state": "x", "questions": {"q": {**ROUTE, "criteria": {"only": "One"}}}},
                "expected_output": {"q": "only"},
            },
            "question q: choice criteria must map 2–255 options to descriptions",
        ),
    ],
)
def test_an_invalid_decision_row_names_the_row_and_the_rule(owner, make_dataset, row, reason):
    _, project, _ = owner
    report = make_dataset(project, [*ROWS, row]).active_cell.intent_report["decision"]

    assert report["ok"] is False
    assert report["reason"] == f"1 rows are not valid decisions; row 3: {reason}"


def test_a_job_trains_on_a_frozen_version_evaluates_it_and_charges_once(
    owner, make_dataset, decision, fake_modal
):
    user, project, client = owner
    train = make_dataset(project, ROWS, name="tickets")
    evaluation = make_dataset(project, ROWS[:2], name="held out", target="eval")

    created = _job(client, project, train, evaluation)
    assert created.status_code == 202, created.data
    assert created.data["status"] == "queued"
    assert created.data["training"]["version"] == "v0"
    assert created.data["evaluation_data"]["dataset_name"] == "held out"
    job = DecisionJob.objects.get(pk=created.data["id"])
    assert job.cell.frozen and job.eval_cell.frozen

    decision_jobs.start(job.id)
    staged = [json.loads(line) for line in decision.staged[str(job.id)]["records"].splitlines()]
    assert staged[1] == {
        "state": "Ticket: my parcel never arrived.",
        "questions": {"route": ROUTE, "urgent": URGENT, "tone": TONE},
        "gold": {
            "route": {"probabilities": {"billing": 0.25, "shipping": 0.75}},
            "urgent": {"probabilities": {"true": 1.0, "false": 0.0}},
            "tone": {"probabilities": {"0": 1.0, "1": 0.0, "2": 0.0}},
        },
    }
    assert decision.cloud.spawns() == ["train_decision"]

    decision_jobs.advance(job.id)
    assert client.get(f"/api/decision-jobs/{job.id}/").data["status"] == "running"

    fake_modal.release("train_decision")
    decision_jobs.advance(job.id)
    assert DecisionJob.objects.get(pk=job.pk).status == "evaluating"

    fake_modal.release("evaluate_decision")
    decision_jobs.advance(job.id)
    finished = client.get(f"/api/decision-jobs/{job.id}/").data
    assert finished["status"] == "succeeded"
    assert finished["checkpoint_ready"] is True
    trained = finished["evaluation"]["trained"]
    assert finished["evaluation"]["rows"] == 2
    assert (trained["n"], trained["unanswered"]) == (6, 0)
    # The fake answers every question with its first option at 0.9.
    assert trained["questions"]["route"]["accuracy"] == 0.5
    assert trained["questions"]["urgent"]["accuracy"] == 0.5
    assert trained["questions"]["tone"]["accuracy"] == 1.0
    assert trained["questions"]["route"]["class_metrics"]["confusion_matrix"] == {
        "labels": ["billing", "shipping"],
        "matrix": [[1, 0], [1, 0]],
    }

    decision_jobs.advance(job.id)
    charges = BillingTelemetry.objects.filter(user=user, service=BillingService.FINETUNING_JOB)
    assert charges.count() == 1
    assert charges.get().metadata["decision_job_id"] == str(job.id)


def test_a_chat_dataset_or_an_unknown_model_cannot_start_a_job(owner, make_dataset):
    _, project, client = owner
    chat = make_dataset(
        project,
        [
            {
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hey"},
                ]
            }
        ],
    )
    refused = _job(client, project, chat)
    assert refused.status_code == 400
    assert refused.data["dataset"] == [f"{chat.name} · v0: no decision rows"]

    unknown = _job(client, project, make_dataset(project, ROWS), model="missing/model")
    assert unknown.data["detail"] == ["Unknown decision model missing/model."]
    assert not DecisionJob.objects.exists()


def test_a_failed_training_reports_a_plain_error(owner, make_dataset, decision, fake_modal):
    _, project, client = owner
    job = DecisionJob.objects.get(pk=_job(client, project, make_dataset(project, ROWS)).data["id"])
    decision_jobs.start(job.id)

    fake_modal.fail("train_decision", RuntimeError("CUDA out of memory"))
    decision_jobs.advance(job.id)

    job.refresh_from_db()
    assert (job.status, job.error) == ("failed", "Decision training failed.")
    assert job.cost_usd is not None


def test_cancel_stops_the_remote_call(owner, make_dataset, decision, fake_modal):
    _, project, client = owner
    job_id = _job(client, project, make_dataset(project, ROWS)).data["id"]
    decision_jobs.start(job_id)

    cancelled = client.post(f"/api/decision-jobs/{job_id}/cancel/")

    assert cancelled.data["status"] == "cancelled"
    assert [call for call, _ in fake_modal.cancelled] == [
        DecisionJob.objects.get(pk=job_id).remote_id
    ]
    again = client.post(f"/api/decision-jobs/{job_id}/cancel/")
    assert again.data["detail"] == ["The job is already cancelled."]


def test_a_catalog_model_answers_a_system_one_request_and_bills_it(owner, decision):
    user, project, client = owner
    response = client.post(
        "/api/decision-models/vllm-sr/d3-edge/systemone",
        {
            "project": str(project.id),
            "state": "I was charged twice.",
            "questions": {"route": ROUTE},
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["answers"]["route"]["probabilities"] == {"billing": 0.9, "shipping": 0.1}
    assert decision.scored[0]["repo"] == "vllm-sr/d3-edge"
    assert BillingTelemetry.objects.filter(
        user=user, service=BillingService.INFERENCE_FT_MODEL
    ).exists()

    invalid = client.post(
        "/api/decision-models/vllm-sr/d3-edge/systemone",
        {"project": str(project.id), "state": "x", "questions": {"route": {"type": "choice"}}},
        format="json",
    )
    assert invalid.data["detail"] == ["question route: instructions must be text"]


def test_a_cold_server_answers_503_with_retry_after(owner, fake_modal):
    _, project, client = owner
    DecisionBackend(fake_modal).install(warm=False)

    response = client.post(
        "/api/decision-models/Cloudflare/clef/systemone",
        {"project": str(project.id), "state": "x", "questions": {"route": ROUTE}},
        format="json",
    )

    assert response.status_code == 503
    assert response["Retry-After"] == str(decision_jobs.RETRY_AFTER_SECONDS)


def test_a_trained_clef_model_answers_through_its_adapter(
    owner, make_dataset, decision, fake_modal
):
    _, project, client = owner
    job_id = _job(client, project, make_dataset(project, ROWS), model="Cloudflare/clef-flash").data[
        "id"
    ]
    url = f"/api/decision-jobs/{job_id}/systemone/"
    body = {"state": "x", "questions": {"urgent": URGENT}}
    assert client.post(url, body, format="json").data["detail"] == [
        "The trained model is not ready."
    ]

    decision_jobs.start(job_id)
    fake_modal.release("train_decision")
    decision_jobs.advance(job_id)
    answered = client.post(url, body, format="json")

    assert answered.data["answers"]["urgent"] == {"type": "noul", "noul": 0.7}
    assert decision.scored[-1]["adapter"] == f"/artifacts/{job_id}/adapter"
