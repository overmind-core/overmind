from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from django.utils import timezone
from mcp_fixtures import call_tool, mcp_context

from overbae.models import (
    APIToken,
    BillingTelemetry,
    Capability,
    ConnectorCredential,
    ConnectorSyncConfig,
    Dataset,
    DecisionJob,
    DeployedModel,
    EvalMatrix,
    EvalSet,
    FinetuningJob,
    MCPMutation,
    ProjectMembership,
    TrainingCheckpoint,
)
from overbae.services.mcp.contracts.evaluations import CreateEvalMatrixRowInput
from overbae.services.mcp.inventory import COLLECTIONS
from overbae.services.mcp.mutations import claim

pytestmark = [pytest.mark.django_db, pytest.mark.covers("mcp-surface")]


def call(context, tool="list_objects", **arguments):
    result = call_tool(tool, arguments, context)
    assert not result.isError, result
    return result.structuredContent


def test_inventory_discovers_undeployed_models_and_pages_without_cross_project_leakage():
    context, other = mcp_context(), mcp_context()
    for project in (context.project, other.project):
        dataset = Dataset.objects.create(project=project, name="data")
        for index in range(3):
            FinetuningJob.objects.create(
                project=project,
                dataset=dataset,
                name=f"support {index}",
                base_model="base",
                status="succeeded",
            )
        FinetuningJob.objects.create(
            project=project, dataset=dataset, name="failed", base_model="base", status="failed"
        )
    seen = []
    offset = 0
    while True:
        page = call(context, kinds=["trained_model"], search="support", limit=1, offset=offset)
        assert page["total"] == 3
        seen.extend(page["items"])
        offset = page["next_offset"]
        if offset is None:
            break
    assert len({item["id"] for item in seen}) == 3
    assert all(
        item["project_id"] == str(context.project.pk) and item["deployment_id"] is None
        for item in seen
    )
    job = call(context, kinds=["finetune"], status="failed")["items"][0]
    assert job["name"] == "failed"
    overview = call(context, "get_project_overview")
    assert next(row["count"] for row in overview["counts"] if row["kind"] == "trained_model") == 3


def test_inventory_search_preserves_ambiguous_names_and_request_identity():
    context = mcp_context()
    Capability.objects.create(project=context.project, name="support", slug="support")
    job = FinetuningJob.objects.create(
        dataset=Dataset.objects.create(project=context.project, name="data"),
        project=context.project,
        name="support",
        base_model="base",
        request_key="original",
    )
    assert call(context, search="support")["total"] == 2
    assert call(context, request_key="original")["items"][0]["id"] == str(job.pk)
    decision = DecisionJob.objects.create(finetuning_job=job)
    found = call(context, kinds=["decision_job"], request_key="original")["items"]
    assert [item["id"] for item in found] == [str(decision.pk)]
    assert call(context, status="missing")["total"] == 0


def test_batch_status_cursor_reports_changes_and_isolated_errors():
    context, other = mcp_context(), mcp_context()
    jobs = [
        FinetuningJob.objects.create(
            project=ctx.project,
            dataset=Dataset.objects.create(project=ctx.project, name="data"),
            base_model="base",
        )
        for ctx in (context, other)
    ]
    refs = [{"kind": "finetune", "id": str(job.pk)} for job in jobs]
    first = call(context, "inspect_jobs", jobs=refs)
    assert first["changed"]
    assert first["jobs"][1]["error"]["code"] == "resource_not_found"
    second = call(context, "inspect_jobs", jobs=refs, after=first["cursor"])
    assert not second["changed"]
    FinetuningJob.objects.filter(pk=jobs[0].pk).update(status="failed")
    third = call(context, "inspect_jobs", jobs=refs, after=first["cursor"])
    assert third["changed"]
    assert third["jobs"][0]["job"]["status"] == "failed"


def test_project_spend_preserves_unattributed_charges_and_account_isolation():
    job_id = str(uuid4())

    context, other = mcp_context(), mcp_context()
    for user, project, amount, metadata in [
        (context.user, context.project, "-2", {"job_id": job_id}),
        (context.user, context.project, "-3", {}),
        (other.user, context.project, "-100", {}),
        (context.user, other.project, "-100", {}),
    ]:
        BillingTelemetry.objects.create(
            user=user,
            project=project,
            amount=Decimal(amount),
            service="finetuning-job",
            metadata=metadata,
        )
    result = call(context, "get_credit_usage", limit=1)
    assert Decimal(result["spent_credits"]) == 500
    assert result["total_groups"] == 2
    assert result["next_offset"] == 1
    filtered = call(context, "get_credit_usage", training_job=job_id)
    assert Decimal(filtered["spent_credits"]) == 200


def test_lineage_keeps_missing_pins_unknown_and_returns_deployment_identity():
    context = mcp_context()
    dataset = Dataset.objects.create(project=context.project, name="data")
    job = FinetuningJob.objects.create(
        project=context.project, dataset=dataset, base_model="base", status="succeeded"
    )
    deployment = DeployedModel.objects.create(
        project=context.project, finetuning_job=job, model_id="test-lineage", status="ready"
    )
    result = call(context, "inspect_model_lineage", training_job=str(job.pk))
    assert result["cells"] == []
    assert result["deployment"]["id"] == str(deployment.pk)
    assert result["deployment"]["training_job_id"] == str(job.pk)
    assert result["active_capabilities"] == []


def test_mutation_replay_conflict_and_versioned_evaluator_edit():
    context = mcp_context(["read", "write"])
    args = {
        "name": "answer",
        "type": "llm",
        "source": "Answer the question.",
        "request_key": "create-answer",
    }
    original = call(context, "create_eval_matrix_row", **args)
    repeated = call(context, "create_eval_matrix_row", **args)
    assert original == repeated
    assert EvalMatrix.objects.filter(project=context.project).count() == 1
    conflict = call_tool("create_eval_matrix_row", {**args, "source": "Changed"}, context)
    assert (
        conflict.isError and conflict.structuredContent["error"]["code"] == "request_key_conflict"
    )
    receipt = call(context, "inspect_mutation", request_key="create-answer")
    assert receipt["state"] == "completed"
    row = EvalMatrix.objects.get(pk=original["row"]["id"])
    eval_set = EvalSet.objects.create(project=context.project, name="checks")
    eval_set.rows.add(row)
    old_snapshot = row.snapshot()
    revision = {
        "row": str(row.pk),
        "expected_updated_at": row.updated_at.isoformat(),
        "name": row.name,
        "type": row.type,
        "source": "Answer accurately.",
        "request_key": "revise-answer",
    }
    call(context, "revise_eval_matrix_row", **revision)
    row.refresh_from_db()
    assert row.source == "Answer accurately."
    assert eval_set.rows.get().pk == row.pk
    assert row.revisions.get().definition["source"] == old_snapshot["source"]
    stale = call_tool("revise_eval_matrix_row", {**revision, "request_key": "stale-edit"}, context)
    assert stale.isError and stale.structuredContent["error"]["code"] == "revision_conflict"


def test_cross_project_search_reports_partial_scope_and_dependencies_page():
    context, other = mcp_context(), mcp_context()
    dataset = Dataset.objects.create(project=context.project, name="data")
    for index in range(2):
        FinetuningJob.objects.create(
            project=context.project, dataset=dataset, base_model="base", name=f"job-{index}"
        )
    results = call(
        context,
        "search_objects",
        project_ids=[str(context.project.pk), str(other.project.pk)],
        kinds=["finetune"],
    )
    assert results["total"] == 2
    assert not results["scope_complete"]
    assert results["inaccessible_project_ids"] == [str(other.project.pk)]
    dependencies = call(
        context, "inspect_dependencies", kind="dataset", id=str(dataset.pk), limit=1
    )
    assert dependencies["total"] == 2
    assert dependencies["next_offset"] == 1
    more = call(
        context, "inspect_dependencies", kind="dataset", id=str(dataset.pk), limit=1, offset=1
    )
    assert more["items"][0]["id"] != dependencies["items"][0]["id"]


def test_unresolved_mutation_never_replays_and_failed_job_checkpoint_is_discoverable():
    context = mcp_context(["read", "write"])
    payload = CreateEvalMatrixRowInput(name="pending", type="llm", source="Answer accurately.")
    claim(context, "create_eval_matrix_row", "lost-ack", payload.model_dump(mode="json"))
    result = call_tool(
        "create_eval_matrix_row", {**payload.model_dump(), "request_key": "lost-ack"}, context
    )
    assert result.isError and result.structuredContent["error"]["code"] == "request_in_progress"
    assert not EvalMatrix.objects.filter(project=context.project).exists()
    job = FinetuningJob.objects.create(
        project=context.project,
        dataset=Dataset.objects.create(project=context.project, name="data"),
        base_model="base",
        status="failed",
    )
    checkpoint = TrainingCheckpoint.objects.create(
        job=job, key="step-10", attempt=1, step=10, state="verified", identity="a" * 64
    )
    records = call(context, kinds=["checkpoint"])
    assert records["items"][0]["id"] == str(checkpoint.pk)
    assert records["items"][0]["resource"]["uri"].startswith("overmind://checkpoints/")


@pytest.mark.covers("tenancy")
def test_account_search_pages_tied_names_across_projects_and_object_kinds():
    context, second = mcp_context(), mcp_context()
    ProjectMembership.objects.create(project=second.project, user=context.user)
    context = replace(
        context,
        project=None,
        token=APIToken(user=context.user, scope={"scope": "account", "permission": ["read"]}),
    )
    instant = timezone.now()
    expected = set()
    projects = list(ProjectMembership.objects.filter(user=context.user).select_related("project"))
    for membership in projects:
        project = membership.project
        dataset = Dataset.objects.create(project=project, name="Same")
        job = FinetuningJob.objects.create(
            project=project, dataset=dataset, name="Same", base_model="base"
        )
        Dataset.objects.filter(pk=dataset.pk).update(created_at=instant)
        FinetuningJob.objects.filter(pk=job.pk).update(created_at=instant)
        expected.update({("dataset", str(dataset.pk)), ("finetune", str(job.pk))})
    for ordering in ("name", "newest", "oldest"):
        seen, offset = [], 0
        while True:
            page = call(
                context,
                "search_objects",
                project_ids=[str(m.project_id) for m in projects],
                kinds=["dataset", "finetune"],
                ordering=ordering,
                limit=1,
                offset=offset,
            )
            assert page["scope_complete"]
            seen.extend((item["kind"], item["id"]) for item in page["items"])
            offset = page["next_offset"]
            if offset is None:
                break
        assert len(seen) == len(expected) and set(seen) == expected


def test_revision_changes_only_the_fields_it_names():
    context = mcp_context(["read", "write"])
    row = EvalMatrix.objects.create(
        project=context.project,
        name="scoped",
        type="llm",
        source="Answer well.",
        enabled=False,
        span_type="tool_call",
        span_name="search",
    )
    listed = call(context, kinds=["eval_row"])["items"][0]
    revised = call(
        context,
        "revise_eval_matrix_row",
        row=str(row.pk),
        expected_updated_at=listed["updated_at"],
        source="Answer accurately.",
    )["row"]
    assert (revised["source"], revised["enabled"]) == ("Answer accurately.", False)
    assert (revised["span_type"], revised["span_name"]) == ("tool_call", "search")
    assert revised["updated_at"] > listed["updated_at"]
    row.refresh_from_db()
    assert row.revisions.get().definition["source"] == "Answer well."
    moved = call_tool(
        "revise_eval_matrix_row",
        {
            "row": str(row.pk),
            "expected_updated_at": revised["updated_at"],
            "capability": "support",
        },
        context,
    )
    assert moved.isError and moved.structuredContent["error"]["code"] == "invalid_input"


def test_revision_refuses_no_changes_and_an_oversized_retained_source():
    context = mcp_context(["read", "write"])
    row = EvalMatrix.objects.create(
        project=context.project, name="long", type="deterministic", source="#" * 20_001
    )
    stamp = {"row": str(row.pk), "expected_updated_at": row.updated_at.isoformat()}
    empty = call_tool("revise_eval_matrix_row", stamp, context)
    assert empty.isError and empty.structuredContent["error"]["code"] == "invalid_input"
    renamed = call_tool("revise_eval_matrix_row", {**stamp, "name": "longer"}, context)
    assert renamed.structuredContent["error"]["fields"].keys() == {"source"}
    row.refresh_from_db()
    assert row.name == "long" and not row.revisions.exists()


def test_revision_rename_conflict_reports_the_name_field():
    context = mcp_context(["read", "write"])
    EvalMatrix.objects.create(project=context.project, name="taken", type="llm", source="One.")
    row = EvalMatrix.objects.create(project=context.project, name="free", type="llm", source="Two.")
    result = call_tool(
        "revise_eval_matrix_row",
        {"row": str(row.pk), "expected_updated_at": row.updated_at.isoformat(), "name": "taken"},
        context,
    )
    error = result.structuredContent["error"]
    assert result.isError and error["code"] == "eval_matrix_row_invalid"
    assert "name" in error["fields"]
    row.refresh_from_db()
    assert row.name == "free" and not row.revisions.exists()


def test_retryable_failure_releases_the_request_key_and_a_refusal_keeps_it(monkeypatch):
    context = mcp_context(["read", "write"])
    connector = ConnectorCredential.objects.create(
        project=context.project,
        name="Langfuse",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="provider-key",
        api_secret="provider-secret",
        verified_at=timezone.now(),
        capability_mapping_confirmed=True,
    )
    ConnectorSyncConfig.objects.create(
        credential=connector,
        version=1,
        source_project_id="source-project",
        lookback_days=7,
        effective_from=timezone.now(),
    )
    args = {"connector": str(connector.pk), "request_key": "sync-once"}

    def broker_down(*_, **__):
        raise ConnectionError

    target = "overbae.tasks.connector_sync.sync_connector_chunk.apply_async"
    monkeypatch.setattr(target, broker_down)
    failed = call_tool("sync_connector", args, context)
    assert failed.structuredContent["error"]["retryable"]
    assert not MCPMutation.objects.filter(request_key="sync-once").exists()

    queued = []
    monkeypatch.setattr(target, lambda args, **_: queued.append(args[0]))
    assert not call_tool("sync_connector", args, context).isError
    assert not call_tool("sync_connector", args, context).isError
    assert queued == [str(connector.pk)]

    refused = {"name": "bad", "type": "deterministic", "source": "x =", "request_key": "bad-check"}
    first = call_tool("create_eval_matrix_row", refused, context)
    assert first.isError and not first.structuredContent["error"]["retryable"]
    replayed = call_tool("create_eval_matrix_row", refused, context)
    assert replayed.structuredContent == first.structuredContent
    assert MCPMutation.objects.get(request_key="bad-check").state == "completed"


def test_missing_receipt_names_no_recovery_tool():
    result = call_tool("inspect_mutation", {"request_key": "unknown"}, mcp_context())
    error = result.structuredContent["error"]
    assert error["code"] == "resource_not_found" and error["next_actions"] == []


def test_inventory_console_links_name_console_routes():
    routes = Path(__file__).parents[1] / "frontend/src/routes/_auth"
    for kind, spec in COLLECTIONS.items():
        stem = spec.route.replace("/", ".")
        assert (routes / f"{stem}.tsx").exists() or (routes / f"{stem}.index.tsx").exists(), kind
