from __future__ import annotations

import json
import math

import pytest
from factories import auth_client, make_project, make_user
from fakes.modal import DecisionBackend

from overbae.modal.decision_catalog import models
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

    assert report["decision"] == {
        "ok": True,
        "reason": "",
        "rows": 3,
        "shape": {"options": 2, "score_levels": 3, "questions": 3},
        "warnings": [
            "question route: 2 of 2 options have fewer than 5 rows",
            "question tone: 3 of 3 options have fewer than 5 rows",
        ],
    }
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
        (_row("x", tone=3), "answer '3' is not a level; score answers are level indexes 0–2"),
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
            'question q: choice criteria must be an object mapping 2–255 option keys to descriptions, e.g. {"refund": "...", "other": "..."}',
        ),
    ],
)
def test_an_invalid_decision_row_names_the_row_and_the_rule(owner, make_dataset, row, reason):
    _, project, _ = owner
    report = make_dataset(project, [*ROWS, row]).active_cell.intent_report["decision"]

    assert report["ok"] is False
    assert report["reason"] == f"1 rows are not valid decisions; row 4: {reason}"


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
    assert finished["evaluation"]["train_overlap"] == 2
    assert finished["evaluation"]["status"] == "complete"
    assert finished["evaluation_status"] == "complete"
    assert (trained["n"], trained["unanswered"]["total"]) == (6, 0)
    # The fake answers every question with its first option at 0.9; the weighted
    # route answer is scored by log loss and Brier, not accuracy.
    route = trained["questions"]["route"]
    assert (route["accuracy"], route["soft"]) == (1.0, 1)
    assert trained["questions"]["urgent"]["accuracy"] == 0.5
    assert trained["questions"]["tone"]["accuracy"] == 1.0

    decision_jobs.advance(job.id)
    charges = BillingTelemetry.objects.filter(user=user, service=BillingService.FINETUNING_JOB)
    assert charges.count() == 1
    assert charges.get().metadata["decision_job_id"] == str(job.id)


def _urgent_row(urgent, state: str) -> dict:
    return {
        "input": {"state": state, "questions": {"urgent": URGENT}},
        "expected_output": {"urgent": urgent},
    }


def _evaluated_urgent(owner, make_dataset, decision, fake_modal, held_out: list[dict]) -> dict:
    _, project, client = owner
    train = make_dataset(project, ROWS, name="tickets")
    evaluation = make_dataset(project, held_out, name="held out", target="eval")
    job_id = _job(client, project, train, evaluation).data["id"]
    decision_jobs.start(job_id)
    fake_modal.release("train_decision")
    decision_jobs.advance(job_id)
    fake_modal.release("evaluate_decision")
    decision_jobs.advance(job_id)
    finished = client.get(f"/api/decision-jobs/{job_id}/").data
    return finished["evaluation"]["trained"]["questions"]["urgent"]


def test_an_evaluation_reports_always_answering_the_majority_as_the_baseline(
    owner, make_dataset, decision, fake_modal
):
    held_out = [_urgent_row("false", f"quiet {i}") for i in range(86)] + [
        _urgent_row("true", f"urgent {i}") for i in range(14)
    ]

    urgent = _evaluated_urgent(owner, make_dataset, decision, fake_modal, held_out)

    # The fake always answers "true" at 0.7, so accuracy is the share of urgent rows.
    assert (urgent["majority_baseline"], urgent["accuracy"], urgent["soft"]) == (0.86, 0.14, 0)


def test_a_weighted_gold_answer_is_kept_out_of_accuracy_but_not_log_loss_or_brier(
    owner, make_dataset, decision, fake_modal
):
    held_out = [
        _urgent_row("true", "a"),
        _urgent_row("true", "b"),
        _urgent_row("false", "c"),
        _urgent_row({"true": 0.75, "false": 0.25}, "d"),
        _urgent_row({"true": 0.5, "false": 0.5}, "e"),
    ]

    urgent = _evaluated_urgent(owner, make_dataset, decision, fake_modal, held_out)

    assert (urgent["n"], urgent["soft"]) == (5, 2)
    assert urgent["accuracy"] == pytest.approx(2 / 3, abs=1e-6)
    assert urgent["majority_baseline"] == pytest.approx(2 / 3, abs=1e-6)
    # Predicted [0.7, 0.3] on every row: true rows, false row, 0.75/0.25 and 0.5/0.5.
    assert urgent["brier"] == pytest.approx((0.18 + 0.18 + 0.98 + 0.005 + 0.08) / 5, abs=1e-6)
    assert urgent["log_loss"] == pytest.approx(
        (
            2 * -math.log(0.7)
            + -math.log(0.3)
            + 0.75 * -math.log(0.7)
            + 0.25 * -math.log(0.3)
            + 0.5 * -math.log(0.7)
            + 0.5 * -math.log(0.3)
        )
        / 5,
        abs=1e-6,
    )
    # Weighted rows are out of the argmax set: only true, true and false remain.
    assert urgent["macro_f1"] == 0.4


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
    assert unknown.data["detail"][0].startswith("Unknown decision model missing/model. Models: ")
    assert not DecisionJob.objects.exists()


def test_a_failed_training_reports_a_plain_error(owner, make_dataset, decision, fake_modal):
    _, project, client = owner
    job = DecisionJob.objects.get(pk=_job(client, project, make_dataset(project, ROWS)).data["id"])
    decision_jobs.start(job.id)

    fake_modal.fail("train_decision", RuntimeError("CUDA out of memory"))
    decision_jobs.advance(job.id)

    job.refresh_from_db()
    assert (job.status, job.error) == ("failed", "Training stopped unexpectedly.")
    assert job.cost_usd is not None


def test_a_request_the_model_rejects_answers_400_with_its_message(owner, decision):
    _, project, client = owner
    decision.score_error = {
        "code": "request_rejected",
        "message": "question route: instructions are too long",
    }

    response = client.post(
        "/api/decision-models/vllm-sr/d3-edge/systemone",
        {"project": str(project.id), "state": "x", "questions": {"route": ROUTE}},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["detail"] == ["question route: instructions are too long"]


def test_a_model_that_fails_to_boot_answers_503_with_a_reason_and_no_worker_message(
    owner, decision
):
    _, project, client = owner
    decision.score_error = {"code": "boot_failed", "message": "CUDA error in /weights/private"}

    response = client.post(
        "/api/decision-models/vllm-sr/d3-edge/systemone",
        {"project": str(project.id), "state": "x", "questions": {"route": ROUTE}},
        format="json",
    )

    assert response.status_code == 503
    assert response.data == {
        "detail": "Decision inference is not available.",
        "code": "unavailable",
        "reason": "boot_failed",
    }


def test_an_evaluation_where_every_trained_row_errors_fails_with_the_code(
    owner, make_dataset, decision, fake_modal
):
    _, project, client = owner
    train = make_dataset(project, ROWS, name="tickets")
    evaluation = make_dataset(project, ROWS[:2], name="held out", target="eval")
    job_id = _job(client, project, train, evaluation).data["id"]
    decision.trained_error = {"code": "runtime_error", "message": "CUDA out of memory"}
    decision_jobs.start(job_id)
    fake_modal.release("train_decision")
    decision_jobs.advance(job_id)
    fake_modal.release("evaluate_decision")
    decision_jobs.advance(job_id)

    finished = client.get(f"/api/decision-jobs/{job_id}/").data

    assert finished["status"] == "succeeded"
    assert finished["evaluation_status"] == "failed"
    assert finished["evaluation"]["status"] == "failed"
    trained = finished["evaluation"]["trained"]
    assert trained["n"] == 0
    assert trained["unanswered"] == {
        "total": 6,
        "by_reason": {"runtime_error": 6},
        "sample": "CUDA out of memory",
    }
    assert finished["evaluation"]["base"]["unanswered"]["total"] == 0


def test_a_training_where_no_row_fits_fails_with_the_skipped_counts(
    owner, make_dataset, decision, fake_modal
):
    _, project, client = owner
    job_id = _job(client, project, make_dataset(project, ROWS)).data["id"]
    decision.train_error = {
        "code": "no_records_fit",
        "message": "every row exceeds the context",
        "skipped": {"too_long": 3, "rejected": 1},
    }
    decision_jobs.start(job_id)
    fake_modal.release("train_decision")
    decision_jobs.advance(job_id)

    job = DecisionJob.objects.get(pk=job_id)
    max_context = models()["vllm-sr/d3-edge"]["max_context_length"]
    assert (job.status, job.error) == (
        "failed",
        f"No rows fit vllm-sr/d3-edge: 3 too long for {max_context} tokens, "
        "1 rejected by the model.",
    )


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


def test_d1_models_train_on_their_own_job_and_answer_on_l4(
    owner, make_dataset, decision, fake_modal
):
    from overbae.modal.decision_catalog import models

    omni = models()["LiquidAI/d1-omni-600M"]
    larger = models()["LiquidAI/d1-3B"]
    assert (omni["architecture_context"], omni["max_context_length"], omni["inference_gpu"]) == (
        16384,
        16384,
        "L4",
    )
    assert (
        larger["architecture_context"],
        larger["max_context_length"],
        larger["inference_gpu"],
    ) == (32768, 32768, "L4")

    user, project, client = owner
    job_id = _job(client, project, make_dataset(project, ROWS), model=larger["id"]).data["id"]
    decision_jobs.start(job_id)
    assert "train_d1" in fake_modal.spawns()
    assert "train_decision" not in fake_modal.spawns()

    answered = client.post(
        f"/api/decision-models/{omni['id']}/systemone",
        {
            "project": str(project.id),
            "state": "I was charged twice.",
            "questions": {"route": ROUTE},
        },
        format="json",
    )
    assert answered.status_code == 200, answered.data
    assert "D1Server.score" in fake_modal.spawns()
    charge = BillingTelemetry.objects.get(user=user, service=BillingService.INFERENCE_FT_MODEL)
    assert charge.metadata["gpu_type"] == "L4"


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


def test_choice_options_keep_the_order_they_were_landed_in(owner, make_dataset, decision):
    _, project, client = owner
    topics = {
        "instructions": "Which topic is it?",
        "type": "choice",
        "criteria": {"world": "World", "sports": "Sports", "business": "Business", "tech": "Tech"},
    }
    rows = [
        {
            "input": {"state": "Markets rallied.", "questions": {"topic": topics}},
            "expected_output": {"topic": "business"},
        }
    ]
    train = make_dataset(project, rows)
    job = DecisionJob.objects.get(pk=_job(client, project, train).data["id"])
    decision_jobs.start(job.id)

    staged = [json.loads(line) for line in decision.staged[str(job.id)]["records"].splitlines()]
    assert list(staged[0]["questions"]["topic"]["criteria"]) == [
        "world",
        "sports",
        "business",
        "tech",
    ]


def _wide_question(options: int) -> dict:
    return {
        "type": "choice",
        "instructions": "Which team handles this ticket?",
        "criteria": {f"team{i}": f"Team {i}" for i in range(options)},
    }


def _wide_row(options: int) -> dict:
    return {
        "input": {"state": "Ticket: wide", "questions": {"route": _wide_question(options)}},
        "expected_output": {"route": "team0"},
    }


def test_a_catalog_request_over_the_option_limit_is_refused_before_any_gpu_call(
    owner, decision, fake_modal
):
    _, project, client = owner
    response = client.post(
        "/api/decision-models/Cloudflare/clef/systemone",
        {"project": str(project.id), "state": "x", "questions": {"route": _wide_question(27)}},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["detail"] == ["question route has 27 options; this model takes up to 26"]
    assert fake_modal.spawns() == []
    assert decision.scored == []


def test_a_request_over_the_score_level_or_question_limit_is_refused(owner, decision):
    _, project, client = owner
    tone = {
        "type": "score",
        "instructions": "How upset is the customer?",
        "criteria": [f"level {i}" for i in range(11)],
    }
    levels = client.post(
        "/api/decision-models/Cloudflare/clef/systemone",
        {"project": str(project.id), "state": "x", "questions": {"tone": tone}},
        format="json",
    )
    assert levels.status_code == 400
    assert levels.data["detail"] == ["question tone has 11 levels; this model takes up to 10"]

    many = client.post(
        "/api/decision-models/Cloudflare/clef/systemone",
        {
            "project": str(project.id),
            "state": "x",
            "questions": {f"q{i}": URGENT for i in range(65)},
        },
        format="json",
    )
    assert many.status_code == 400
    assert many.data["detail"] == ["65 questions in one request; this model takes up to 64"]
    assert decision.scored == []


def test_a_job_on_a_dataset_over_the_model_limit_is_refused_and_not_created(owner, make_dataset):
    _, project, client = owner
    wide = make_dataset(project, [ROWS[0], _wide_row(27)], name="wide")

    response = _job(client, project, wide, model="Cloudflare/clef")

    assert response.status_code == 400
    assert response.data["detail"] == [
        "1 rows exceed Cloudflare/clef's limits; row 2: question route has 27 options; "
        "this model takes up to 26"
    ]
    assert not DecisionJob.objects.exists()


def test_the_decision_model_list_states_each_models_limits(owner):
    _, _, client = owner
    by_id = {model["id"]: model for model in client.get("/api/decision-models/").data}

    assert by_id["Cloudflare/clef"]["limits"] == {
        "options": 26,
        "score_levels": 10,
        "questions": 64,
    }
    assert by_id["vllm-sr/d3-edge"]["limits"]["options"] == 255
