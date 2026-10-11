"""Run through manage.py shell against the migrated local PostgreSQL deployment."""

import json
import uuid
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db import connection, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from overbae.models import Dataset, Project, ProjectMembership, User
from overbae.services.datasets import paths, store, workbench
from overbae.tasks.datasets import land

assert connection.vendor == "postgresql"
with TemporaryDirectory(prefix="workshop-replay-") as media:
    with override_settings(MEDIA_ROOT=media, ALLOWED_HOSTS=["testserver"]), transaction.atomic():
        suffix = uuid.uuid4().hex
        user = User.objects.create_user(email=f"workshop-replay-{suffix}@example.test")
        project = Project.objects.create(name="Workshop replay", slug=f"workshop-{suffix}")
        ProjectMembership.objects.create(user=user, project=project)
        client = APIClient()
        client.force_authenticate(user)
        source = {"rows": [{"question": "Opening time?", "answer": "Nine."}]}
        with patch("overbae.tasks.datasets.land.apply_async"):
            response = client.post(
                "/api/datasets/",
                {"project": str(project.pk), "name": "Replay", "intent": "train", "source": source},
                format="json",
            )
        assert response.status_code == 201, response.data
        dataset = Dataset.objects.get(pk=response.data["id"])
        land.run(
            dataset_id=str(dataset.pk), source=source, user_id=str(user.pk), infer_capability=False
        )
        dataset.refresh_from_db()
        original = dataset.active_cell
        source_bytes = paths.cell_path(dataset.pk, original.pk).read_bytes()
        base = f"/api/datasets/{dataset.pk}/"
        response = client.post(
            base + "pipelines/",
            {
                "name": "Conversation",
                "request_key": "recipe",
                "steps": [
                    {"operation": "conversation", "question": "question", "answer": "answer"}
                ],
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        body = {
            "pipeline": response.data["id"],
            "source_cell": str(original.pk),
            "source_fingerprint": original.fingerprint,
            "request_key": "run",
        }
        response = client.post(base + "pipeline-runs/", body, format="json")
        assert response.status_code == 202, response.data
        run_id = response.data["id"]
        workbench.execute(run_id)
        repeated = client.post(base + "pipeline-runs/", body, format="json")
        assert repeated.status_code == 202, repeated.data
        assert repeated.data["id"] == run_id
        assert repeated.data["state"] == "completed"
        dataset.refresh_from_db()
        transformed = dataset.active_cell
        rows = list(store.iter_rows(paths.cell_path(dataset.pk, transformed.pk)))
        assert rows[0]["messages"][1]["content"] == "Nine."
        response = client.post(
            base + "versions/import/",
            {
                "source_cell": str(transformed.pk),
                "source_fingerprint": transformed.fingerprint,
                "request_key": "import",
                "name": "Agent output",
                "provenance": "Native coding agent replay. " * 50,
                "imported_rows": [{"text": "Nine.", "source_row": 0}],
            },
            format="json",
        )
        assert response.status_code == 202, response.data
        workbench.execute(response.data["id"])
        detail = client.get(base + "workbench/")
        assert detail.status_code == 200, detail.data
        assert all(run["state"] == "completed" for run in detail.data["runs"])
        assert paths.cell_path(dataset.pk, original.pk).read_bytes() == source_bytes
        assert dataset.cells.count() == 3
        connection.check_constraints()
        transaction.set_rollback(True)
    assert not Project.objects.filter(pk=project.pk).exists()
print(json.dumps({"database": "postgresql", "runs": 2, "cells": 3, "rollback": "verified"}))
