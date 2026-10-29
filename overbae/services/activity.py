import hashlib
import json
from urllib.parse import urlsplit
from uuid import UUID

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from overbae.models import (
    Activity,
    ActivityClient,
    ActivityRead,
    Capability,
    DatasetPipelineRun,
    DatasetPreparation,
    FinetuningJob,
)

AGENTS = {
    "codex": ("codex", "Codex"),
    "openai-codex": ("codex", "Codex"),
    "codex-mcp-client": ("codex", "Codex"),
    "claude-code": ("claude", "Claude Code"),
    "claude code": ("claude", "Claude Code"),
    "cursor": ("cursor", "Cursor"),
    "gemini-cli": ("gemini", "Gemini CLI"),
}


def actor(client_name):
    return AGENTS.get((client_name or "").lower(), ("coding_agent", "Coding agent"))


def credential_identity(context):
    return f"{type(context.token).__name__}:{context.token.pk}"


def connection_actor(context):
    if context.client_name:
        return actor(context.client_name)
    identities = list(
        ActivityClient.objects.filter(user=context.user, credential=credential_identity(context))
        .values_list("actor_kind", "actor_name")
        .distinct()[:2]
    )
    return identities[0] if len(identities) == 1 else actor("")


def connected(context):
    if context.client_name == "overmind-transfer":
        return
    actor_kind, actor_name = connection_actor(context)
    ActivityClient.objects.get_or_create(
        user=context.user,
        credential=credential_identity(context),
        actor_kind=actor_kind,
        defaults={"actor_name": actor_name},
    )
    project_id = (
        context.project.pk
        if context.project and context.token.scope.get("scope") == "project"
        else None
    )
    return record(
        key=f"mcp-connected:{context.user.pk}:{project_id or 'account'}:{actor_kind}",
        kind="agent_connected",
        action="Connected to Overmind",
        user_id=context.user.pk,
        project_id=project_id,
        actor_kind=actor_kind,
        actor_name=actor_name,
    )


def asset(kind, identity, label):
    return {"kind": kind, "id": str(identity), "label": str(label)[:160]}


ACTION_VERBS = {
    "create": "Created",
    "save": "Saved",
    "update": "Updated",
    "set": "Changed",
    "attach": "Attached",
    "connect": "Connected",
    "delete": "Deleted",
    "cancel": "Cancelled",
    "run": "Requested",
    "start": "Requested",
    "prepare": "Requested preparation of",
    "deploy": "Requested deployment of",
    "activate": "Activated",
    "import": "Imported",
    "pause": "Paused",
    "resume": "Resumed",
    "launch": "Requested launch of",
    "configure": "Configured",
    "schedule": "Scheduled",
    "retry": "Retried",
    "reuse": "Reused",
    "derive": "Derived",
    "measure": "Requested measurement of",
    "sync": "Requested synchronization of",
}


def action_label(name):
    verb, _, subject = name.partition("_")
    return f"{ACTION_VERBS[verb]} {subject.replace('_', ' ')}"


def output_assets(output):
    result = []
    links = [*output.get("resource_links", [])]
    if isinstance(output.get("resource"), dict):
        links.append(output["resource"])
    for link in links:
        uri = urlsplit(link.get("uri", ""))
        parts = [uri.netloc, *uri.path.strip("/").split("/")]
        if len(parts) >= 4 and parts[0] == "projects":
            parts = parts[2:]
        if len(parts) != 2 or parts[0] not in {"datasets", "capabilities"}:
            continue
        try:
            identity = UUID(parts[1])
        except ValueError:
            continue
        entry = asset(
            "dataset" if parts[0] == "datasets" else "capability",
            identity,
            link.get("title", parts[0]),
        )
        if entry not in result:
            result.append(entry)
    dataset = output.get("dataset")
    if dataset:
        identity = dataset.get("id") if isinstance(dataset, dict) else dataset
        label = dataset.get("name", "Dataset") if isinstance(dataset, dict) else "Dataset"
        try:
            entry = asset("dataset", UUID(str(identity)), label)
            if not any(item["kind"] == "dataset" and item["id"] == entry["id"] for item in result):
                result.append(entry)
        except ValueError:
            pass
    return result[:20]


def record(
    *,
    key,
    kind,
    action,
    project_id=None,
    user_id=None,
    actor_kind="overmind",
    actor_name="Overmind",
    assets=(),
    task_kind="",
    task_id="",
    status="completed",
):
    preparation = None
    if (
        task_kind == "dataset_pipeline"
        or kind in {"source_uploaded", "source_ready", "preparation_published"}
        or action in {"Saved a transformation for", "Saved dataset pipeline"}
    ):
        dataset_ids = [item["id"] for item in assets if item["kind"] == "dataset"]
        preparation = DatasetPreparation.objects.filter(
            project_id=project_id, dataset_id__in=dataset_ids, state__in=["preparing", "paused"]
        ).first()
    event, created = Activity.objects.get_or_create(
        key=key,
        defaults={
            "preparation": preparation,
            "project_id": project_id,
            "user_id": user_id,
            "kind": kind,
            "action": action,
            "actor_kind": actor_kind,
            "actor_name": actor_name,
            "assets": list(assets),
            "task_kind": task_kind,
            "task_id": str(task_id),
            "status": status,
        },
    )
    if created and preparation:
        Activity.objects.filter(key=f"preparation:{preparation.pk}").update(
            updated_at=timezone.now()
        )
    if event.actor_kind == "overmind" and actor_kind != "overmind":
        Activity.objects.filter(pk=event.pk, actor_kind="overmind").update(
            actor_kind=actor_kind, actor_name=actor_name
        )
    return event


def visible(user, project_ids, project_id=None, *, include_account=True):
    account = Q(project__isnull=True, user=user) if include_account else Q(pk__in=[])
    projects = Q(project_id__in=project_ids)
    if project_id:
        projects &= Q(project_id=project_id)
    else:
        projects = Q(pk__in=[])
    return Activity.objects.filter(account | projects).annotate(
        seen=Exists(
            ActivityRead.objects.filter(
                activity_id=OuterRef("pk"), user=user, seen_at__gte=OuterRef("updated_at")
            )
        )
    )


def mark_seen(query, user, ids, through):
    through = min(through, timezone.now())
    updated = 0
    for event in query.filter(pk__in=ids, updated_at__lte=through):
        ActivityRead.objects.update_or_create(
            activity=event, user=user, defaults={"seen_at": through}
        )
        updated += 1
    return updated


def update_task(project_id, kind, identity, status):
    normalized = {
        "succeeded": "completed",
        "complete": "completed",
        "ready": "completed",
        "error": "failed",
        "queued": "accepted",
        "pending": "accepted",
        "starting": "running",
        "preparing": "running",
        "preprocessing": "running",
        "deploying": "running",
    }.get(status, status)
    if normalized not in {"accepted", "running", "completed", "failed", "cancelled"}:
        return
    changed = Activity.objects.filter(
        project_id=project_id, task_kind=kind, task_id=str(identity)
    ).exclude(status=normalized)
    preparation_ids = list(
        changed.exclude(preparation=None).values_list("preparation_id", flat=True)
    )
    changed.update(status=normalized, updated_at=timezone.now())
    if preparation_ids:
        Activity.objects.filter(
            key__in=[f"preparation:{identity}" for identity in preparation_ids]
        ).update(updated_at=timezone.now())


def mcp_succeeded(name, definition, output, context):
    if definition.read_only and not context.client_name:
        return
    actor_kind, actor_name = connection_actor(context)
    attribution = {"user_id": context.user.pk, "actor_kind": actor_kind, "actor_name": actor_name}
    if (
        context.client_name
        or not ActivityClient.objects.filter(
            user=context.user, credential=credential_identity(context)
        ).exists()
    ):
        connected(context)
    if name in {"start_dataset_preparation", "update_dataset_preparation"}:
        return
    if not context.project or definition.read_only:
        return
    if name.partition("_")[0] not in ACTION_VERBS:
        return
    project_id = context.project.pk
    if name == "start_dataset":
        dataset = output["dataset"]
        record(
            key=f"dataset-created:{dataset['id']}",
            project_id=project_id,
            kind="asset_changed",
            action="Created",
            assets=[asset("dataset", dataset["id"], dataset["name"])],
            **attribution,
        )
        return
    if name == "cancel_finetune":
        event = Activity.objects.filter(
            key=f"cancel-training:{output['id']}", project_id=project_id
        ).first()
        if event:
            Activity.objects.filter(pk=event.pk, actor_kind="overmind").update(**attribution)
        return
    if name == "run_evaluation":
        for run in output.get("runs", []):
            record(
                key=f"mcp-task:{project_id}:eval_run:{run['id']}",
                project_id=project_id,
                kind="task_requested",
                action="Requested evaluation",
                task_kind="eval_run",
                task_id=run["id"],
                status=run["status"],
                assets=output_assets(output),
                **attribution,
            )
        return
    job = output.get("training_run") or output.get("finetune") or output.get("job")
    if isinstance(job, dict) and job.get("id") and job.get("kind"):
        assets = output_assets(output)
        training = (
            output.get("training_run") or output.get("finetune") or output.get("training_job")
        )
        if training and training.get("id"):
            training_job = (
                FinetuningJob.objects.select_related("dataset")
                .filter(pk=training["id"], project_id=project_id)
                .first()
            )
            if training_job and training_job.dataset_id:
                assets = (
                    [asset("training_job", training_job.pk, training_job.name)]
                    if name in {"start_finetune", "start_decision_job"}
                    else [asset("dataset", training_job.dataset_id, training_job.dataset.name)]
                )
        for field, kind in (("dataset_id", "dataset"), ("capability_id", "capability")):
            if output.get(field):
                assets.append(
                    asset(kind, output[field], "Dataset" if kind == "dataset" else "Capability")
                )
        cancelling = name.startswith("cancel_")
        action = action_label(name)
        if name in {"start_finetune", "start_decision_job"}:
            action = "Started training run"
        elif name == "prepare_training_data":
            action = "Requested training data preparation for"
        if name == "run_dataset_pipeline":
            action = (
                "Requested a transformation preview for"
                if (output.get("run") or {}).get("mode") == "preview"
                else "Requested a transformation of"
            )
        record(
            key=f"mcp-{'cancel' if cancelling else 'task'}:{project_id}:{job['kind']}:{job['id']}",
            project_id=project_id,
            kind="task_cancelled" if cancelling else "task_requested",
            action="Cancelled task" if cancelling and job.get("status") == "cancelled" else action,
            task_kind=job["kind"],
            task_id=job["id"],
            assets=assets,
            status=job.get("status", "accepted"),
            **attribution,
        )
    elif name in {"create_eval_matrix_row", "delete_eval_matrix_row"}:
        row = output["row"]
        assets = [asset("evaluation", row["id"], row["name"])]
        if row.get("capability"):
            capability = Capability.objects.filter(
                project=context.project, slug=row["capability"]
            ).first()
            if capability:
                assets.append(asset("capability", capability.pk, capability.name))
        creating = name == "create_eval_matrix_row"
        record(
            key=f"evaluation:{row['id']}" if creating else f"evaluation-deleted:{row['id']}",
            project_id=project_id,
            kind="evaluation_added" if creating else "evaluation_deleted",
            action="Added an evaluation" if creating else "Deleted an evaluation",
            assets=assets,
            **attribution,
        )
    else:
        digest = hashlib.sha256(
            json.dumps(output, sort_keys=True, default=str).encode()
        ).hexdigest()
        record(
            key=f"mcp-change:{project_id}:{name}:{digest}",
            project_id=project_id,
            kind="asset_changed",
            action="Saved a transformation for"
            if name == "save_dataset_pipeline"
            else action_label(name),
            assets=output_assets(output),
            **attribution,
        )


AGENT_ONBOARDING_STEPS = (
    ("overmind_installed", "Install Overmind"),
    ("agent_connected", "Connect project"),
    ("repository_inspected", "Inspect repository"),
    ("capabilities_discovered", "Discover capabilities"),
    ("capabilities_described", "Describe capabilities"),
    ("source_references_verified", "Verify source references"),
    ("snapshot_written", "Write snapshot"),
    ("configuration_validated", "Validate configuration"),
    ("repository_synced", "Sync capabilities"),
    ("capabilities_instrumented", "Instrument capabilities"),
)


def report_onboarding_progress(context, run_id, completed_steps):
    actor_kind, actor_name = connection_actor(context)
    record(
        key=f"onboarding:{context.project.pk}:{run_id}:{completed_steps}",
        kind=f"onboarding_progress_{completed_steps}",
        action=f"Completed {completed_steps} of 10 onboarding stages",
        project_id=context.project.pk,
        user_id=context.user.pk,
        actor_kind=actor_kind,
        actor_name=actor_name,
        task_kind="onboarding",
        task_id=run_id,
    )


def milestones(query, path):
    steps = (
        AGENT_ONBOARDING_STEPS
        if path == "agent"
        else (
            ("agent_connected", "Coding agent connected"),
            ("source_uploaded", "Source uploaded"),
            ("source_ready", "Source ready"),
            ("preparation_published", "Preparation published"),
        )
    )
    progress = (
        query.filter(kind__startswith="onboarding_progress_").order_by("-created_at").first()
        if path == "agent"
        else None
    )
    reports = (
        list(query.filter(kind__startswith="onboarding_progress_", task_id=progress.task_id))
        if progress
        else []
    )
    result = []
    for index, (kind, label) in enumerate(steps):
        reported = [event for event in reports if int(event.kind.rsplit("_", 1)[1]) > index]
        event = min(reported, key=lambda event: event.created_at) if reported else None
        if path != "agent" or kind in {"agent_connected", "repository_synced"}:
            events = query.filter(kind=kind, status="completed")
            if progress and kind == "repository_synced":
                events = events.filter(created_at__gte=min(item.created_at for item in reports))
            event = (
                events.order_by(
                    "-created_at" if kind == "repository_synced" else "created_at"
                ).first()
                or event
            )
        result.append(
            {
                "kind": kind,
                "label": label,
                "actor_kind": (progress or event).actor_kind if progress or event else None,
                "actor_name": (progress or event).actor_name if progress or event else None,
                "completed_at": event.created_at if event else None,
                "activity_id": event.pk if event else None,
                "assets": event.assets if event else [],
            }
        )
    return result


def repository_synced(project, snapshot, capabilities):
    fingerprint = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, default=str).encode()
    ).hexdigest()
    assets = [asset("capability", cap.pk, cap.name) for cap in capabilities[:20]]
    record(
        key=f"sync:{project.pk}:{fingerprint}",
        project_id=project.pk,
        kind="repository_synced",
        action=f"Synced repository · {len(capabilities)} capabilities",
        actor_kind="cli",
        actor_name="Overmind CLI",
        assets=assets,
    )
    if capabilities:
        record(
            key=f"capabilities:{project.pk}",
            project_id=project.pk,
            kind="capabilities_available",
            action="Capabilities available",
            actor_kind="cli",
            actor_name="Overmind CLI",
            assets=assets,
        )


def source_ready(dataset, cell_id, user=None):
    record(
        key=f"source-ready:{cell_id}",
        project_id=dataset.project_id,
        user_id=getattr(user, "pk", None),
        kind="source_ready",
        action="Source ready",
        assets=[asset("dataset", dataset.pk, dataset.name)],
    )


def feed(query, *, offset=0, limit=25):
    observed_at = timezone.now()
    query = query.filter(updated_at__lte=observed_at, preparation__isnull=True).exclude(
        kind__startswith="onboarding_progress_"
    )
    count = query.count()
    events = list(query[offset : offset + limit])
    runs = dict(
        DatasetPipelineRun.objects.filter(
            pk__in=[event.task_id for event in events if event.task_kind == "dataset_pipeline"],
        ).values_list("pk", "dataset_id")
    )
    sequences = {}
    for event in events:
        dataset_id = (
            runs.get(UUID(event.task_id)) if event.task_kind == "dataset_pipeline" else None
        )
        if (
            event.kind in {"source_uploaded", "source_ready"}
            or event.action
            in {
                "Saved a transformation for",
                "Saved dataset pipeline",
                "Published dataset preparation",
            }
            or event.key.startswith("dataset-created:")
        ):
            dataset_ids = [item["id"] for item in event.assets if item["kind"] == "dataset"]
            if len(dataset_ids) == 1:
                dataset_id = dataset_ids[0]
        if dataset_id:
            sequences[event.pk] = f"dataset-preparation:{event.project_id}:{dataset_id}"
    return {
        "results": [
            {
                "id": event.pk,
                "project": event.project_id,
                "kind": event.kind,
                "actor_kind": event.actor_kind,
                "actor_name": event.actor_name,
                "action": event.action,
                "status": event.status,
                "assets": event.assets,
                "task_kind": event.task_kind,
                "task_id": event.task_id,
                "sequence_key": sequences.get(event.pk, ""),
                "seen": event.seen,
                "created_at": event.created_at,
                "updated_at": event.updated_at,
            }
            for event in events
        ],
        "count": count,
        "unseen_count": query.filter(seen=False).count(),
        "observed_at": observed_at,
        "next_offset": offset + limit if offset + limit < count else None,
    }
