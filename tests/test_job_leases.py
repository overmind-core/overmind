from datetime import timedelta

import pytest
from django.utils import timezone
from test_data_first_workflow import workspace

from overbae.models import DataExploration
from overbae.services.datasets import exploration, heartbeat
from overbae.tasks import data_exploration

pytestmark = [pytest.mark.django_db, pytest.mark.covers("recovery")]


def _profile(project, dataset):
    return exploration.request(
        project,
        source_cell=dataset.active_cell,
        name="Profile",
        request_key="profile",
        kind="profile",
        sampling_request=None,
    )


def test_a_lost_job_restarts_once_its_lease_lapses_and_fails_after_three_losses(monkeypatch):
    project, _, dataset = workspace()
    op = _profile(project, dataset)
    started = []
    monkeypatch.setattr(data_exploration.run, "delay", started.append)
    rows = DataExploration.objects.filter(pk=op.pk)

    rows.update(state="running", lease_until=timezone.now() + timedelta(minutes=1))
    data_exploration.reconcile()
    assert started == []

    rows.update(lease_until=timezone.now() - timedelta(seconds=1))
    data_exploration.reconcile()
    assert started == [str(op.pk)]
    exploration.advance(op.pk)
    assert rows.get().state == "completed"

    rows.update(
        state="running",
        lease_until=timezone.now() - timedelta(seconds=1),
        attempts=heartbeat.MAX_ATTEMPTS - 1,
    )
    exploration.advance(op.pk)
    assert rows.values_list("state", "error").get() == ("failed", heartbeat.STOPPED)
