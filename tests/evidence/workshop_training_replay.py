from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.db import transaction

from overbae.models import APIToken, Dataset, FinetuningJob, ProjectMembership
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

train = Dataset.objects.select_related("project", "capability").get(
    pk="02af0404-759a-46bb-8f8a-b514cd912da6"
)
member = ProjectMembership.objects.select_related("user").filter(project=train.project).first()
context = MCPContext(
    user=member.user,
    token=APIToken(scope={"scope": "project", "permission": ["read", "write"]}),
    project=train.project,
)
arguments = {
    "dataset": str(train.id),
    "capability": str(train.capability_id),
    "eval_dataset": "af7dc707-165a-41bb-ac18-f13d86007740",
    "eval_set": "7b0596f0-6057-4c64-b69f-1d81c18c65c5",
    "base_model": "Qwen/Qwen3.5-27B",
    "name": "Local workshop readiness verification",
    "hyperparameters": {"training_type": {"type": "Lora"}},
}
before = FinetuningJob.objects.filter(project=train.project).count()
with (
    transaction.atomic(),
    patch(
        "overbae.tasks.finetuning.run_finetuning.apply_async",
        return_value=SimpleNamespace(id="local-readiness-intercepted"),
    ) as dispatch,
):
    result = async_to_sync(CATALOG.call)("start_finetune", arguments, context)
    assert not result.isError, result.structuredContent
    receipt = result.structuredContent["job"]
    job = FinetuningJob.objects.get(pk=receipt["id"])
    assert job.cell_id == train.active_cell.id
    assert dispatch.call_count == 1
    state = async_to_sync(CATALOG.call)(
        "get_job", {"kind": "finetune_job", "id": str(job.id)}, context
    )
    assert not state.isError, state.structuredContent
    print("PASS: existing train/eval data -> MCP launch -> PostgreSQL -> get_job:", job.status)
    transaction.set_rollback(True)
assert FinetuningJob.objects.filter(project=train.project).count() == before
print("PASS: dispatch intercepted, no GPU launched, no test job retained")
