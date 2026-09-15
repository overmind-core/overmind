"""Eval-intent gate for optimizer datasets. Command templates are authored on the client."""

from __future__ import annotations

import uuid

import pytest
from conftest import EVAL_ROWS, TRAIN_ROWS, frozen_dataset

from overbae.models import Capability, Project
from overbae.models.optimizer import optimizer_dataset_error

pytestmark = pytest.mark.django_db


def test_optimizer_rejects_ft_and_missing_datasets():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    ft_dataset = frozen_dataset(project, TRAIN_ROWS, name="D")

    assert optimizer_dataset_error(capability, ft_dataset) is not None
    assert optimizer_dataset_error(capability, None) is not None

    eval_dataset = frozen_dataset(project, EVAL_ROWS, name="D2")
    assert optimizer_dataset_error(capability, eval_dataset) is None
