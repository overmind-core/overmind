import pytest
from fakes.daytona import DaytonaProvisioningAPI
from mcp_fixtures import mcp_context
from rest_framework.test import APIClient

from overbae.models import Dataset, WorkshopWorkspace
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.workshop_agent import conversation, lifecycle, provisioning, resident
from overbae.services.workshop_agent.contracts import SendMessage, StartTask
from workshop_runtime.environment import identity

pytestmark = [pytest.mark.django_db, pytest.mark.covers("work-once")]


@pytest.fixture
def setup(monkeypatch, scripted):
    monkeypatch.setenv("WORKSHOP_RUNTIME_SNAPSHOT", "qualified-runtime")
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    remote = DaytonaProvisioningAPI({"ready": True, "identity": identity()})
    remote.connect(monkeypatch, scripted)
    grant = mcp_context(["read", "write"])
    dataset = Dataset.objects.create(project=grant.project, name="New project source")
    payload = StartTask(dataset=dataset.pk, objective="Inspect data", request_key="first")
    return grant, payload, remote


def test_new_project_accepts_tasks_and_reuses_qualified_isolated_workspace(setup):
    grant, payload, remote = setup
    client = APIClient()
    client.force_authenticate(grant.user, grant.token)
    response = client.get(
        "/api/workshop-tasks/", {"project": grant.project.pk, "dataset": payload.dataset}
    )
    assert response.data["enabled"] is True
    assert not WorkshopWorkspace.objects.filter(project=grant.project).exists()
    task = lifecycle.start(grant, payload)
    assert lifecycle.start(grant, payload).pk == task.pk
    other = Dataset.objects.create(project=grant.project, name="Second source")
    lifecycle.start(
        grant, payload.model_copy(update={"request_key": "second", "dataset": other.pk})
    )
    assert provisioning.ensure(grant.project)
    assert provisioning.ensure(grant.project)
    assert len(remote.creates) == 1
    allocation = remote.creates[0]
    assert allocation["labels"]["project"] == str(grant.project.pk)
    assert allocation["networkBlockAll"] and not allocation["public"]
    assert allocation["autoStopInterval"] == allocation["autoPauseInterval"] == 0
    assert allocation["autoDeleteInterval"] == -1
    assert WorkshopWorkspace.objects.get(project=grant.project).sandbox_id == remote.sandbox_id
    lifecycle.authorise(task)


def test_unknown_creation_is_reconciled_without_resubmission(setup):
    grant, payload, remote = setup
    lifecycle.start(grant, payload)
    remote.lose_acknowledgement = True
    assert provisioning.ensure(grant.project) is False
    remote.visible = False
    assert provisioning.ensure(grant.project) is False
    assert len(remote.creates) == 1
    remote.visible = True
    assert provisioning.ensure(grant.project)
    assert len(remote.creates) == 1


@pytest.mark.parametrize("invalid", ["owner", "runtime"])
def test_mismatched_allocation_never_becomes_a_project_workspace(setup, invalid):
    grant, payload, remote = setup
    lifecycle.start(grant, payload)
    remote.lose_acknowledgement = True
    assert provisioning.ensure(grant.project) is False
    if invalid == "owner":
        remote.labels["project"] = "another-project"
    else:
        remote.runtime["identity"] = "different-runtime"
    with pytest.raises(provisioning.WorkspaceSetupError):
        provisioning.ensure(grant.project)
    assert WorkshopWorkspace.objects.get(project=grant.project).sandbox_id is None


def test_disabled_workspace_is_not_recreated_or_implicitly_enabled(setup):
    grant, payload, remote = setup
    WorkshopWorkspace.objects.create(project=grant.project, sandbox_id="retained", enabled=False)
    assert not provisioning.available(grant.project)
    with pytest.raises(DatasetError, match="disabled"):
        lifecycle.start(grant, payload)
    assert not remote.creates


def test_definite_rejection_can_retry_without_losing_the_project_identity(setup):
    grant, payload, remote = setup
    lifecycle.start(grant, payload)
    remote.reject = True
    with pytest.raises(provisioning.WorkspaceSetupError, match="rejected"):
        provisioning.ensure(grant.project)
    remote.reject = False
    assert provisioning.ensure(grant.project)
    assert remote.creates[0]["name"] == remote.creates[1]["name"]


def test_failed_setup_finishes_preparation_and_chat_can_retry(setup):
    grant, payload, remote = setup
    task = lifecycle.start(grant, payload)
    remote.reject = True
    assert resident.bootstrap(task) is None
    task.refresh_from_db()
    task.preparation.refresh_from_db()
    assert task.state == "failed"
    assert task.preparation.state == "cancelled"
    statuses = list(
        task.chat_events.filter(message__id="workspace-setup").values_list(
            "message__status", flat=True
        )
    )
    assert statuses == ["running", "failed"]
    retry = conversation.send(
        grant, SendMessage(dataset=payload.dataset, text="Try again", request_key="retry")
    )
    assert retry.pk != task.pk
    remote.reject = False
    assert provisioning.ensure(grant.project)


def test_another_supervisor_can_finish_setup_before_creator_receives_acknowledgement(setup):
    grant, payload, remote = setup
    lifecycle.start(grant, payload)
    recovered = []
    remote.on_create = lambda: recovered.append(provisioning.ensure(grant.project))
    assert provisioning.ensure(grant.project)
    assert recovered == [True]
    assert len(remote.creates) == 1
