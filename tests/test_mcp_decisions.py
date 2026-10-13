from __future__ import annotations

import asyncio
import json

import pytest
from conftest import frozen_dataset
from fakes.modal import DecisionBackend
from mcp_fixtures import mcp_context
from test_decision_jobs import ROWS

from overbae.services import decision_jobs
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
