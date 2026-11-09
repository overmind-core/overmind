import json
import logging
import os
from datetime import timedelta

from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig, ListSandboxesQuery
from daytona.common.errors import DaytonaBadRequestError
from django.db import transaction
from django.utils import timezone

from overbae.models import Project, WorkshopWorkspace
from overbae.services.datasets.lifecycle import DatasetError
from workshop_runtime.environment import identity

logger = logging.getLogger(__name__)


def available(project):
    binding = WorkshopWorkspace.objects.filter(project=project).first()
    return binding.enabled if binding else bool(os.environ.get("WORKSHOP_RUNTIME_SNAPSHOT"))


def reserve(project):
    binding = WorkshopWorkspace.objects.filter(project=project).first()
    if binding:
        if not binding.enabled:
            raise DatasetError("The project workspace is disabled.")
        return binding
    snapshot = os.environ.get("WORKSHOP_RUNTIME_SNAPSHOT")
    if not snapshot:
        raise DatasetError("The Data Workshop runtime is not configured.")
    return WorkshopWorkspace.objects.create(
        project=project,
        provisioning={
            "name": f"overmind-workshop-{project.pk}",
            "snapshot": snapshot,
            "runtime": identity(),
            "state": "pending",
        },
    )


class WorkspaceSetupError(Exception):
    pass


def ensure(project):
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        binding = WorkshopWorkspace.objects.select_for_update().get(project=project)
        if not binding.enabled:
            raise WorkspaceSetupError("The project workspace is disabled.")
        if binding.sandbox_id:
            return True
        receipt = dict(binding.provisioning)
        create = receipt["state"] in {"pending", "rejected"}

    client = Daytona(
        DaytonaConfig(
            api_key=os.environ["DAYTONA_API_KEY"],
            api_url="https://app.daytona.io/api",
            otel_enabled=False,
        )
    )
    matches = list(
        client.list(
            ListSandboxesQuery(labels={"overmind_workspace": receipt["name"]}), request_timeout=30
        )
    )
    if len(matches) > 1:
        raise WorkspaceSetupError(
            "Workspace setup found multiple allocations. Reconciliation is required."
        )
    if matches:
        sandbox = client.get(matches[0].id, request_timeout=30)
    elif create:
        with transaction.atomic():
            binding = WorkshopWorkspace.objects.select_for_update().get(project=project)
            if not binding.enabled:
                raise WorkspaceSetupError("The project workspace is disabled.")
            if binding.sandbox_id:
                return True
            if binding.provisioning != receipt:
                return False
            receipt.update(state="creating", requested_at=timezone.now().isoformat())
            binding.provisioning = receipt
            binding.save(update_fields=["provisioning"])
        try:
            sandbox = client.create(
                CreateSandboxFromSnapshotParams(
                    name=receipt["name"],
                    snapshot=receipt["snapshot"],
                    labels={
                        "project": str(project.pk),
                        "runtime": receipt["runtime"],
                        "overmind_workspace": receipt["name"],
                    },
                    public=False,
                    auto_stop_interval=0,
                    auto_pause_interval=0,
                    auto_delete_interval=-1,
                    ephemeral=False,
                    network_block_all=True,
                ),
                timeout=60,
            )
        except DaytonaBadRequestError:
            # A definite rejection permits a later explicit request to try again.
            WorkshopWorkspace.objects.filter(project=project, sandbox_id=None).update(
                provisioning={**receipt, "state": "rejected"}
            )
            logger.exception("Daytona rejected workspace creation for %s", project.pk)
            raise WorkspaceSetupError(
                "Workspace creation was rejected. Check Daytona capacity and runtime configuration, then retry."
            ) from None
        except Exception:
            # The durable intent survives an unknown acknowledgement; only lookup may recover it.
            logger.exception("Workspace creation acknowledgement uncertain for %s", project.pk)
            return False
    else:
        if timezone.now() - timezone.datetime.fromisoformat(receipt["requested_at"]) > timedelta(
            minutes=2
        ):
            raise WorkspaceSetupError(
                "Workspace creation is unconfirmed. The saved allocation must be reconciled before another can be created."
            )
        return False
    if (
        sandbox.labels.get("project") != str(project.pk)
        or sandbox.labels.get("runtime") != receipt["runtime"]
    ):
        raise WorkspaceSetupError("Workspace ownership or runtime identity does not match.")
    if sandbox.state in {"creating", "starting", "pending_build", "pulling_snapshot"}:
        return False
    if sandbox.state in {"stopped", "archived"}:
        sandbox.start(timeout=60)
    if (
        sandbox.state != "started"
        or sandbox.public
        or not sandbox.network_block_all
        or sandbox.auto_stop_interval != 0
        or sandbox.auto_pause_interval not in (None, 0)
        or sandbox.auto_delete_interval != -1
        or sandbox.auto_destroy_at
    ):
        raise WorkspaceSetupError("Workspace readiness or isolation checks failed.")
    probe = sandbox.process.exec(
        "/opt/overmind-runtime/venv/bin/python -m workshop_runtime.environment", timeout=60
    )
    report = json.loads(probe.result) if probe.exit_code == 0 else {}
    if (
        not report.get("ready")
        or report.get("identity") != receipt["runtime"]
        or receipt["runtime"] != identity()
    ):
        raise WorkspaceSetupError(
            "The workspace runtime does not match this release. Publish the matching runtime before retrying."
        )
    with transaction.atomic():
        binding = WorkshopWorkspace.objects.select_for_update().get(project=project)
        if binding.enabled and binding.sandbox_id == sandbox.id:
            return True
        if not binding.enabled or binding.provisioning != receipt:
            raise WorkspaceSetupError("Workspace configuration changed during setup.")
        binding.sandbox_id = sandbox.id
        binding.provisioning = {**receipt, "state": "ready"}
        binding.save(update_fields=["sandbox_id", "provisioning"])
    return True
