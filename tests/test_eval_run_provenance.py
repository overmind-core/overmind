"""A run pins a frozen version; the serializer names it so the console can link back."""

from __future__ import annotations

import uuid

import pytest
from conftest import EVAL_ROWS, frozen_dataset

from overbae.api.eval_serializers import EvalRunSerializer
from overbae.models import EvalRun, Project

pytestmark = pytest.mark.django_db


@pytest.fixture
def project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def test_version_info_names_the_pinned_version(project):
    dataset = frozen_dataset(project, EVAL_ROWS, name="graded set")
    version = dataset.active_cell
    run = EvalRun.objects.create(project=project, name="e", dataset=dataset, cell=version)
    data = EvalRunSerializer(run).data
    assert data["dataset_name"] == "graded set"
    assert data["cell_info"] == {
        "id": str(version.id),
        "version": "1.0",
        "title": "Source",
        "rows": 2,
        "fingerprint": version.fingerprint,
        "dataset_id": str(dataset.id),
    }


def test_version_info_null_without_a_pin(project):
    dataset = frozen_dataset(project, EVAL_ROWS)
    run = EvalRun.objects.create(project=project, name="e", dataset=dataset)
    assert EvalRunSerializer(run).data["cell_info"] is None
    bare = EvalRun.objects.create(project=project, name="e2", dataset=None)
    assert EvalRunSerializer(bare).data["cell_info"] is None
