from __future__ import annotations

import asyncio
import uuid

import pytest
from django.utils import timezone
from mcp_fixtures import mcp_context

from overbae.models import Dataset, DatasetImport
from overbae.services.datasets import imports
from overbae.services.mcp.catalog import CATALOG
from overbae.tasks import datasets as dataset_tasks

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("recover_split", [False, True])
def test_inspection_leads_to_source_recovery_through_existing_tool(monkeypatch, recover_split):
    context = mcp_context(("read", "write"))
    dataset = Dataset.objects.create(
        project=context.project,
        name="Retained source",
        source_kind=Dataset.SourceKind.TRACES,
        state=Dataset.State.LANDING,
    )
    inputs = {
        "dataset_id": str(dataset.pk),
        "source": {"traces": {"trace_ids": [uuid.uuid4().hex]}},
        "user_id": str(context.user.pk),
        "infer_capability": False,
    }
    target = dataset
    if recover_split:
        target = Dataset.objects.create(
            project=context.project,
            name="Retained eval source",
            source_kind=Dataset.SourceKind.TRACES,
            state=Dataset.State.LANDING,
        )
        inputs["split"] = {
            "eval_dataset_id": str(target.pk),
            "eval_percent": 20,
            "position": "tail",
        }
    receipt = imports.queue_landing_receipt(dataset, uuid.uuid4(), inputs, published=True)
    claim = imports.claim(receipt.pk)
    assert claim is not None
    imports.fail(claim, "The import worker stopped.")
    queued = []
    monkeypatch.setattr(dataset_tasks.land, "apply_async", lambda **kwargs: queued.append(kwargs))

    target.refresh_from_db()
    assert target.state == Dataset.State.ERROR
    inspected = asyncio.run(CATALOG.call("inspect_dataset", {"dataset": str(target.pk)}, context))
    assert not inspected.isError, inspected.structuredContent
    actions = inspected.structuredContent["next_actions"]
    assert len(actions) == 1
    action = actions[0]
    assert action["tool"] == "resume_dataset_import"
    recovered = asyncio.run(CATALOG.call(action["tool"], action["arguments"], context))

    assert not recovered.isError, recovered.structuredContent
    assert recovered.structuredContent["dataset"]["id"] == str(target.pk)
    assert recovered.structuredContent["dataset"]["state"] == "landing"
    assert recovered.structuredContent["job"]["kind"] == "dataset_run"
    assert recovered.structuredContent["job"]["id"] == str(target.pk)
    assert len(queued) == 1
    assert queued[0]["task_id"] == str(receipt.pk)
    assert queued[0]["kwargs"] == inputs
    receipt.refresh_from_db()
    assert receipt.state == DatasetImport.State.QUEUED
    dataset.refresh_from_db()
    assert dataset.state == Dataset.State.LANDING


def test_inspection_does_not_suggest_a_refused_action_after_import_attempts_are_exhausted():
    context = mcp_context(("read", "write"))
    dataset = Dataset.objects.create(
        project=context.project,
        name="Stopped source",
        source_kind=Dataset.SourceKind.TRACES,
        state=Dataset.State.ERROR,
        error="The import attempt limit was reached. Its source is retained.",
    )
    DatasetImport.objects.create(
        dataset=dataset,
        state=DatasetImport.State.BLOCKED,
        attempts=imports.MAX_ATTEMPTS,
        queued_at=timezone.now(),
    )

    inspected = asyncio.run(CATALOG.call("inspect_dataset", {"dataset": str(dataset.pk)}, context))

    assert not inspected.isError, inspected.structuredContent
    assert inspected.structuredContent["next_actions"] == []
    assert inspected.structuredContent["error"]
