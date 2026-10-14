from __future__ import annotations

import asyncio
import json
import shutil

import pytest
from conftest import frozen_dataset
from fakes.modal import DecisionBackend
from mcp_fixtures import mcp_context
from test_decision_jobs import ROUTE, ROWS, URGENT

from overbae.models import Cell, DecisionJob
from overbae.services import decision_jobs
from overbae.services.datasets import paths, rows, versions
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import bind_context
from overbae.services.mcp.resources import read_resource

pytestmark = pytest.mark.django_db(transaction=True)


def _call(name: str, arguments: dict, context):
    return asyncio.run(CATALOG.call(name, arguments, context))


def _read(context, uri: str) -> dict:
    async def read():
        with bind_context(context):
            return json.loads(list(await read_resource(uri))[0].content)

    return asyncio.run(read())


def test_an_agent_learns_the_row_format_trains_and_follows_the_job(fake_modal):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    guide = _read(context, "overmind://decision-models")
    assert "vllm-sr/d3-edge" in {model["id"] for model in guide["models"]}
    assert "input = {state, questions}" in guide["rows"]
    for tool in ("check_decision_readiness", "start_decision_job", "get_job", "run_decision_model"):
        assert tool in guide["rows"]

    train = frozen_dataset(context.project, ROWS, name="Tickets")
    evaluation = frozen_dataset(context.project, ROWS[:2], name="Held out", contract="eval")
    started = _call(
        "start_decision_job",
        {"model": "vllm-sr/d3-edge", "dataset": "Tickets", "eval_dataset": str(evaluation.id)},
        context,
    )
    assert not started.isError, started.structuredContent
    job = started.structuredContent["job"]
    assert (job["status"], job["version"], job["eval_version"]) == ("queued", "v0", "v0")
    assert job["dataset"] == str(train.id)

    decision_jobs.start(job["id"])
    fake_modal.release("train_decision")
    decision_jobs.advance(job["id"])
    polled = _call("get_job", {"kind": "decision_job", "id": job["id"]}, context)
    assert polled.structuredContent["status"] == "evaluating"
    assert _read(context, f"overmind://jobs/decision_job/{job['id']}")["status"] == "evaluating"

    chat = frozen_dataset(context.project, name="Chat", contract="train")
    refused = _call("start_decision_job", {"model": "vllm-sr/d3-edge", "dataset": "Chat"}, context)
    assert refused.isError
    assert refused.structuredContent["error"]["message"] == "Chat · v0: no decision rows"
    assert chat.cells.get().used_at is None


def test_a_lower_cased_model_id_starts_the_canonical_model(fake_modal):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    frozen_dataset(context.project, ROWS, name="Tickets")

    started = _call(
        "start_decision_job", {"model": "cloudflare/clef", "dataset": "Tickets"}, context
    )

    assert not started.isError, started.structuredContent
    assert started.structuredContent["job"]["model"] == "Cloudflare/clef"


def test_the_decision_model_resource_states_each_models_limits(fake_modal):
    context = mcp_context(("read", "write"))
    models = {
        model["id"]: model for model in _read(context, "overmind://decision-models")["models"]
    }
    assert models["Cloudflare/clef"]["limits"] == {
        "options": 26,
        "score_levels": 10,
        "questions": 64,
    }


ANSWER = {"state": "Ticket: where is my parcel?", "questions": {"route": ROUTE, "urgent": URGENT}}


def _train_to_success(fake_modal, context, *, evaluation=None) -> str:
    arguments = {"model": "vllm-sr/d3-edge", "dataset": "Tickets"}
    if evaluation is not None:
        arguments["eval_dataset"] = str(evaluation.id)
    job_id = _call("start_decision_job", arguments, context).structuredContent["job"]["id"]
    decision_jobs.start(job_id)
    fake_modal.release("train_decision")
    decision_jobs.advance(job_id)
    if evaluation is not None:
        fake_modal.release("evaluate_decision")
        decision_jobs.advance(job_id)
    return job_id


def _second_version_over_the_same_rows(dataset) -> None:
    """A version without a script run: the sandbox that scripts run in is Linux-only."""
    parent = dataset.active_cell
    newer = Cell.objects.create(
        dataset=dataset,
        parent=parent,
        script_sha256=f"{1:064x}",
        position=1,
        title="Copy",
        rows=parent.rows,
        fingerprint=parent.fingerprint,
        columns=parent.columns,
        intent_report=parent.intent_report,
        capability_report=parent.capability_report,
    )
    shutil.copyfile(rows.frame_path(parent), paths.cell_path(dataset.id, newer.id))
    versions.activate(dataset, newer)


def test_start_pins_the_requested_version_and_defaults_to_the_active_one(fake_modal):
    backend = DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    train = frozen_dataset(context.project, ROWS, name="Tickets")
    _second_version_over_the_same_rows(train)

    pinned = _call(
        "start_decision_job",
        {"model": "vllm-sr/d3-edge", "dataset": "Tickets", "version": "v0"},
        context,
    )
    assert not pinned.isError, pinned.structuredContent
    assert pinned.structuredContent["job"]["version"] == "v0"
    decision_jobs.start(pinned.structuredContent["job"]["id"])
    staged = backend.staged[pinned.structuredContent["job"]["id"]]["records"]
    assert len(staged.splitlines()) == 3

    active = _call(
        "start_decision_job", {"model": "vllm-sr/d3-edge", "dataset": "Tickets"}, context
    )
    assert active.structuredContent["job"]["version"] == "v1"

    missing = _call(
        "start_decision_job",
        {"model": "vllm-sr/d3-edge", "dataset": "Tickets", "version": "v9"},
        context,
    )
    assert missing.structuredContent["error"]["code"] == "cell_not_found"


def test_start_names_an_unknown_model_as_model_not_found(fake_modal):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    frozen_dataset(context.project, ROWS, name="Tickets")

    unknown = _call("start_decision_job", {"model": "no/such", "dataset": "Tickets"}, context)
    assert unknown.structuredContent["error"]["code"] == "model_not_found"


def test_get_job_reports_checkpoint_loss_training_and_evaluation_once_succeeded(fake_modal):
    backend = DecisionBackend(fake_modal).install()
    backend.train_metrics = {
        "train_questions": 3,
        "skipped_questions": 1,
        "steps": 2,
        "loss_first_last": [1.5, 0.25],
    }
    context = mcp_context(("read", "write"))
    frozen_dataset(context.project, ROWS, name="Tickets")
    evaluation = frozen_dataset(context.project, ROWS[:2], name="Held out", contract="eval")
    started = _call(
        "start_decision_job",
        {"model": "vllm-sr/d3-edge", "dataset": "Tickets", "eval_dataset": str(evaluation.id)},
        context,
    )
    job_id = started.structuredContent["job"]["id"]
    decision_jobs.start(job_id)
    fake_modal.release("train_decision")
    decision_jobs.advance(job_id)

    evaluating = _call("get_job", {"kind": "decision_job", "id": job_id}, context)
    assert evaluating.structuredContent["details"]["checkpoint_ready"] is False
    assert evaluating.structuredContent["details"]["evaluation_status"] is None

    fake_modal.release("evaluate_decision")
    decision_jobs.advance(job_id)
    view = _call("get_job", {"kind": "decision_job", "id": job_id}, context).structuredContent
    job = view["details"]

    assert (view["status"], job["checkpoint_ready"], job["evaluation_status"]) == (
        "succeeded",
        True,
        "complete",
    )
    assert job["loss"] == [1.5, 0.25]
    assert job["training"] == {
        "examples": 3,
        "unit": "questions",
        "holdout": None,
        "skipped": 1,
        "steps": 2,
        "calibration": None,
    }
    assert view["created_at"] and job["started_at"] and view["completed_at"]
    assert "progress" not in job and "updated_at" not in job


def test_a_job_without_an_eval_set_reports_no_evaluation(fake_modal):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    frozen_dataset(context.project, ROWS, name="Tickets")
    job_id = _train_to_success(fake_modal, context)

    job = _call("get_job", {"kind": "decision_job", "id": job_id}, context).structuredContent[
        "details"
    ]

    assert (job["checkpoint_ready"], job["evaluation_status"], job["loss"]) == (True, "none", [])
    assert job["training"] is None


def test_run_decision_model_answers_from_a_trained_job_or_a_catalog_model(fake_modal):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    frozen_dataset(context.project, ROWS, name="Tickets")
    job_id = _train_to_success(fake_modal, context)

    from_job = _call("run_decision_model", {"job": job_id, **ANSWER}, context)
    assert not from_job.isError, from_job.structuredContent
    body = from_job.structuredContent
    assert body["model"] == "vllm-sr/d3-edge"
    assert body["answers"]["route"]["probabilities"]["billing"] == 0.9
    assert body["usage"] == {"input_tokens": 12, "output_tokens": 0}
    assert body["is_cold"] is False
    assert body["resource"]["uri"] == f"overmind://jobs/decision_job/{job_id}"

    from_model = _call("run_decision_model", {"model": "vllm-sr/d3-edge", **ANSWER}, context)
    assert not from_model.isError, from_model.structuredContent
    assert from_model.structuredContent["answers"]["urgent"]["type"] == "noul"
    assert from_model.structuredContent["resource"]["uri"] == "overmind://decision-models"


@pytest.mark.parametrize(
    "target",
    [{}, {"model": "vllm-sr/d3-edge", "job": "00000000-0000-0000-0000-000000000001"}],
)
def test_run_decision_model_needs_exactly_one_of_model_or_job(fake_modal, target):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))

    result = _call("run_decision_model", {**target, **ANSWER}, context)

    assert result.isError
    assert result.structuredContent["error"]["code"] == "invalid_request"


def test_run_decision_model_refuses_a_job_that_is_not_trained_yet(fake_modal):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    frozen_dataset(context.project, ROWS, name="Tickets")
    started = _call(
        "start_decision_job", {"model": "vllm-sr/d3-edge", "dataset": "Tickets"}, context
    )

    result = _call(
        "run_decision_model", {"job": started.structuredContent["job"]["id"], **ANSWER}, context
    )

    assert result.structuredContent["error"]["code"] == "invalid_request"
    assert result.structuredContent["error"]["message"] == "The trained model is not ready."


def test_a_cold_model_answers_model_warming_with_a_retry_hint(fake_modal):
    DecisionBackend(fake_modal).install(warm=False)
    context = mcp_context(("read", "write"))

    result = _call("run_decision_model", {"model": "vllm-sr/d3-edge", **ANSWER}, context)

    error = result.structuredContent["error"]
    assert (error["code"], error["retryable"], error["retry_after_seconds"]) == (
        "model_warming",
        True,
        decision_jobs.RETRY_AFTER_SECONDS,
    )


def test_a_failed_worker_answers_model_unavailable_with_its_reason(fake_modal):
    backend = DecisionBackend(fake_modal).install()
    backend.score_error = {"code": "boot_failed", "message": "The worker did not start."}
    context = mcp_context(("read", "write"))

    result = _call("run_decision_model", {"model": "vllm-sr/d3-edge", **ANSWER}, context)

    error = result.structuredContent["error"]
    assert error["code"] == "model_unavailable"
    assert error["fields"] == {"reason": "boot_failed"}


def test_readiness_recommends_a_fitting_model_for_decision_rows(fake_modal):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    frozen_dataset(context.project, ROWS, name="Tickets")
    evaluation = frozen_dataset(context.project, ROWS[:2], name="Held out", contract="eval")

    result = _call(
        "check_decision_readiness",
        {"dataset": "Tickets", "eval_dataset": str(evaluation.id)},
        context,
    )

    assert not result.isError, result.structuredContent
    body = result.structuredContent
    assert (body["ready"], body["missing"]) == (True, [])
    assert body["dataset"]["version"] == "v0"
    assert body["eval_dataset"]["version"] == "v0"
    assert body["picks"]
    assert {pick["model"] for pick in body["picks"]} <= {
        row["model"] for row in body["recommendations"]
    }
    assert body["credits"] == {"required": True, "available": True}
    assert all(row["fits"] for row in body["recommendations"])
    assert all(row["why_not"] for row in body["excluded"])


def test_readiness_refuses_chat_rows_with_the_reason(fake_modal):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    frozen_dataset(context.project, name="Chat", contract="train")

    body = _call("check_decision_readiness", {"dataset": "Chat"}, context).structuredContent

    assert body["ready"] is False
    assert body["picks"] == []
    assert any("no decision rows" in reason for reason in body["missing"])


def test_inspect_suggests_decision_readiness_for_decision_rows(fake_modal):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    train = frozen_dataset(context.project, ROWS, name="Tickets")

    result = _call("inspect_dataset", {"dataset": str(train.id)}, context)

    [action] = result.structuredContent["next_actions"]
    assert action["tool"] == "check_decision_readiness"
    assert action["arguments"] == {"dataset": str(train.id), "version": "v0"}


def test_decision_tools_follow_the_read_write_boundary():
    read_tools = {tool.name for tool in CATALOG.tools(frozenset({"read"}))}
    definitions = {definition.name: definition for definition in CATALOG.definitions()}

    assert "check_decision_readiness" in read_tools
    assert {"run_decision_model", "start_decision_job"}.isdisjoint(read_tools)
    run = definitions["run_decision_model"]
    assert (run.read_only, run.idempotent, run.open_world, run.cost_class, run.async_mode) == (
        False,
        False,
        True,
        "llm",
        "sync",
    )
    assert run.required_scopes == definitions["run_inference"].required_scopes
    readiness = definitions["check_decision_readiness"]
    assert (readiness.read_only, readiness.open_world, readiness.cost_class) == (
        True,
        False,
        "free",
    )


def _wide_rows(options: int) -> list[dict]:
    route = {"type": "choice", "instructions": "Which team?", "criteria": {}}
    route["criteria"] = {f"team{i}": f"Team {i}" for i in range(options)}
    return [
        {
            "input": {"state": f"Ticket {n}", "questions": {"route": route}},
            "expected_output": {"route": f"team{n % options}"},
        }
        for n in range(3)
    ]


def test_an_eval_set_over_a_models_limits_excludes_it_from_readiness():
    context = mcp_context(("read", "write"))
    frozen_dataset(context.project, ROWS, name="Tickets")
    wide = frozen_dataset(context.project, _wide_rows(27), name="Wide", contract="eval")

    report = _call(
        "check_decision_readiness", {"dataset": "Tickets", "eval_dataset": str(wide.id)}, context
    ).structuredContent

    excluded = {c["model"]: c["why_not"] for c in report["excluded"]}
    assert excluded["Cloudflare/clef"] == "Takes up to 26 options; this dataset has 27."
    assert all(pick["model"] != "Cloudflare/clef" for pick in report["picks"])


def test_an_eval_decision_dataset_suggests_a_call_it_can_make():
    context = mcp_context(("read", "write"))
    evaluation = frozen_dataset(context.project, ROWS, name="Held out", contract="eval")

    [action] = _call("inspect_dataset", {"dataset": str(evaluation.id)}, context).structuredContent[
        "next_actions"
    ]

    assert (action["tool"], action["arguments"]) == ("list_datasets", {})


def test_an_evaluation_stored_before_status_existed_reports_failed(fake_modal):
    DecisionBackend(fake_modal).install()
    context = mcp_context(("read", "write"))
    frozen_dataset(context.project, ROWS, name="Tickets")
    evaluation = frozen_dataset(context.project, ROWS[:2], name="Held out", contract="eval")
    job_id = _train_to_success(fake_modal, context, evaluation=evaluation)
    DecisionJob.objects.filter(pk=job_id).update(evaluation={"error": "The evaluation failed."})

    job = _call("get_job", {"kind": "decision_job", "id": job_id}, context).structuredContent

    assert job["details"]["evaluation_status"] == "failed"
