from unittest.mock import patch

import pytest

from overbae.models import Dataset, Project
from overbae.services.datasets import operations
from overbae.services.datasets.lifecycle import DatasetError

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("matching_agent", [True, False])
def test_interrupted_local_run_recovery_uses_its_saved_store(tmp_path, settings, matching_agent):
    settings.MEDIA_ROOT = tmp_path
    dataset = Dataset.objects.create(
        project=Project.objects.create(name="Local recovery", slug="local-recovery"),
        state="diagnosing",
    )
    operations.started(dataset.pk, "interrupted")
    operations.provider_started(dataset.pk, "interrupted", "local-agent", "local-run")
    operations.change(dataset.pk, state="cancel_pending", local_stopped=True)
    with (
        patch("cursor_sdk.Client.launch_bridge") as bridge,
        patch("cursor_sdk.Agent.cancel_run", side_effect=ValueError("Expected cloud agent ID")),
    ):
        client = bridge.return_value.__enter__.return_value
        run = client.get_run.return_value
        run.agent_id = "local-agent" if matching_agent else "another-agent"
        state = operations.reconcile(dataset.pk)
    if matching_agent:
        assert state["state"] == "cancelled"
        run.cancel.assert_called_once_with()
        root = tmp_path / "datasets" / str(dataset.pk) / "workspace"
        bridge.assert_called_once_with(workspace=root, state_root=root / ".agent")
        client.get_run.assert_called_once_with("local-run", {"runtime": "local", "cwd": str(root)})
    else:
        assert state["state"] == "cancel_pending"
        run.cancel.assert_not_called()


def test_dead_worker_keeps_provider_cancellation_pending_until_acknowledged():
    dataset = Dataset.objects.create(
        project=Project.objects.create(name="Cancel", slug="cancel"), state="diagnosing"
    )
    operations.started(dataset.pk, "task-1")
    operations.provider_started(dataset.pk, "task-1", "agent-1", "run-1")
    with patch("overbae.services.datasets.operations.cancel_provider", side_effect=TimeoutError):
        state = operations.cancel(dataset.pk)
    assert state["state"] == "cancel_pending"
    dataset.refresh_from_db()
    assert dataset.state == "diagnosing"
    with pytest.raises(DatasetError):
        operations.started(dataset.pk, "task-2")
    with patch("overbae.services.datasets.operations.cancel_provider") as remote:
        state = operations.reconcile(dataset.pk, local_stopped=True)
    assert remote.call_count == 1 and state["state"] == "cancelled"
    dataset.refresh_from_db()
    assert dataset.state == "idle"
    with patch("overbae.services.datasets.operations.cancel_provider") as remote:
        assert operations.cancel(dataset.pk)["state"] == "cancelled"
    remote.assert_not_called()


def test_cancel_during_uncertain_provider_submission_does_not_claim_done():
    dataset = Dataset.objects.create(
        project=Project.objects.create(name="Uncertain", slug="uncertain"), state="diagnosing"
    )
    operations.started(dataset.pk, "task-1")
    operations.provider_submitting(dataset.pk, "task-1", "agent-1")
    with patch("overbae.services.datasets.operations.cancel_provider") as remote:
        state = operations.cancel(dataset.pk)
    remote.assert_not_called()
    assert state["state"] == "cancel_pending"
    assert operations.reconcile(dataset.pk, local_stopped=True)["state"] == "cancel_pending"
    operations.provider_started(dataset.pk, "task-1", "agent-1", "run-late")
    with patch("overbae.services.datasets.operations.cancel_provider"):
        assert operations.reconcile(dataset.pk, local_stopped=True)["state"] == "cancelled"


def test_local_exit_cannot_complete_an_unacknowledged_provider_run():
    dataset = Dataset.objects.create(
        project=Project.objects.create(name="Exit", slug="exit"), state="diagnosing"
    )
    operations.started(dataset.pk, "task")
    operations.provider_started(dataset.pk, "task", "agent", "run")
    with patch("overbae.services.datasets.operations.cancel_provider", side_effect=TimeoutError):
        state = operations.finished(dataset.pk)
    assert state["state"] == "cancel_pending"
    assert state["local_stopped"] is True


def test_cell_child_is_reaped_after_cancel(tmp_path):
    from overbae.services.datasets import store
    from overbae.services.datasets.notebook import runner

    source = tmp_path / "source.parquet"
    import pandas as pd

    store.write_frame(source, pd.DataFrame({"input": ["synthetic"]}))
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 2

    result = runner.run(
        "while True: pass", source, library_cache=tmp_path / "libraries", cancelled=cancelled
    )
    assert result.path is None and result.error == "Cancellation requested."


def test_delayed_provider_acknowledgement_cannot_take_over_a_later_turn():
    dataset = Dataset.objects.create(
        project=Project.objects.create(name="Ownership", slug="ownership")
    )
    operations.started(dataset.pk, "old")
    operations.finished(dataset.pk, task_id="old")
    operations.started(dataset.pk, "current")
    with pytest.raises(DatasetError, match="ownership"):
        operations.provider_started(dataset.pk, "old", "old-agent", "old-run")
    dataset.refresh_from_db()
    assert dataset.operation["task_id"] == "current"
    assert not dataset.operation.get("provider")


def test_delayed_tool_callback_cannot_mutate_a_later_turn():
    from overbae.services.datasets.notebook.agent import Tools

    dataset = Dataset.objects.create(
        project=Project.objects.create(name="Tools", slug="tools"),
        name="Unchanged",
        intent="explore",
    )
    operations.started(dataset.pk, "old")
    tools = Tools(dataset.id, None, lambda event: None)
    tools.operation_id = "old"
    operations.finished(dataset.pk, task_id="old")
    operations.started(dataset.pk, "current")
    with pytest.raises(DatasetError, match="ownership"):
        tools.handlers()["rename"]({"name": "Changed by old turn"})
    dataset.refresh_from_db()
    assert dataset.name == "Unchanged"
