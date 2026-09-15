from __future__ import annotations

import uuid

import pytest
from conftest import EVAL_ROWS, frozen_dataset

from overbae.models import Capability, EvalSet, Project
from overbae.services.optimizer_prereqs import optimizer_prerequisite_report

pytestmark = pytest.mark.django_db


def test_prerequisite_report_ready_when_dataset_and_eval_set_present():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(project=project, name="a", slug="a")
    dataset = frozen_dataset(project, EVAL_ROWS, capability=capability)
    eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
    capability.active_eval_set = eval_set
    capability.save(update_fields=["active_eval_set"])

    report = optimizer_prerequisite_report(project, capability)
    assert report["ready"] is True
    assert report["missing"] == []
    assert report["eval_datasets"][0]["name"] == (dataset.name or str(dataset.id)[:8])
    assert report["eval_datasets"][0]["usable"] is True
    assert "overmind optimise start" in report["drive_command"]
    assert "executioner_connected" not in report
    assert "executioner_start_command" not in report


def test_prerequisite_report_missing_dataset():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(project=project, name="a", slug="a")
    eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
    capability.active_eval_set = eval_set
    capability.save(update_fields=["active_eval_set"])

    report = optimizer_prerequisite_report(project, capability)
    assert report["ready"] is False
    assert any("dataset" in item for item in report["missing"])
