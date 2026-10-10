import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient
from starlette.testclient import TestClient
from test_data_first_workflow import workspace
from test_mcp_research_journey import call

from modal_shared.training_data import materialize_files, row_key
from overbae.models import APIToken, FinetuningJob
from overbae.services import training_submission
from overbae.services.mcp.server import create_mcp_application
from overbae.services.training_transfer import stage_data
from overbae.tasks.finetuning_reconciler import _reconcile

pytestmark = pytest.mark.django_db(transaction=True)


def test_active_transfer_reconciles_real_rows_into_console_and_mcp_without_dispatch(tmp_path):
    project, _, dataset = workspace()
    user = project.memberships.select_related("user").first().user
    key, _ = APIToken.create_for_user(user, project=project)
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="fixture",
        status="preparing",
        provider="modal",
        celery_task_id="live-staging",
        requested_configuration={"runtime": {"environment": "test"}},
    )
    training_submission.claim(job)
    rows = [{"messages": [{"role": "assistant", "content": str(index)}]} for index in range(3000)]
    paths = {}
    for name, items in (("data", rows + rows[:2]), ("val", rows[-2:])):
        paths[name] = tmp_path / f"{name}.source"
        paths[name].write_text("".join(json.dumps(row) + "\n" for row in items))
    tokens = tmp_path / "tokens.jsonl"
    tokens.write_text(
        "".join(
            json.dumps({"key": row_key(row), "input_ids": [index]}) + "\n"
            for index, row in enumerate(rows)
        )
    )
    preparation = SimpleNamespace(
        id="fixture-preparation",
        report={"artifact_sha256": hashlib.sha256(tokens.read_bytes()).hexdigest()},
    )
    collected = []

    class Volume:
        frame = None

        @contextmanager
        def batch_upload(self):
            yield self

        def put_file(self, source, destination):
            (tmp_path / destination.rsplit("/", 1)[-1]).write_bytes(source.read_bytes())

        def read_file(self, path):
            yield json.dumps(self.frame).encode()

    volume = Volume()
    rest = APIClient()
    rest.force_authenticate(user)
    app = MagicMock()
    inspector = app.control.inspect.return_value
    inspector.active.return_value = {"worker": [{"id": "live-staging"}]}
    inspector.reserved.return_value = inspector.scheduled.return_value = {"worker": []}

    with TestClient(create_mcp_application()) as mcp:

        def report(frame):
            volume.frame = frame
            with (
                patch("overbae.celery.get_celery_app", return_value=app),
                patch("modal.Volume.from_name", return_value=volume),
            ):
                result = _reconcile()
                assert result["kicked"] == 0
            job.refresh_from_db()
            response = rest.get(f"/api/finetuning-jobs/{job.pk}/loss-curves/")
            assert response.status_code == 200
            receipt = call(
                mcp,
                key,
                "get_job",
                {"project_id": str(project.pk), "kind": "finetune_job", "id": str(job.pk)},
            )
            assert response.data["progress"]["diagnostics"] == receipt["progress"]["diagnostics"]
            detail = receipt["progress"]["diagnostics"]
            assert detail["stage"] == frame["stage"]
            assert detail["completed"] == frame["completed"]
            assert detail["files_completed"] == 2
            operation = receipt["details"]["operation"]
            assert operation["stage"] == frame["stage"]
            assert operation["completed"] == frame["completed"]
            collected.append(frame)

        class Provider:
            def remote(self, *, selections, **kwargs):
                materialize_files(
                    tokens,
                    preparation.report["artifact_sha256"],
                    [
                        (tmp_path / f"selected-{name}.keys", tmp_path / f"{name}.out", spec)
                        for name, spec in selections.items()
                    ],
                    progress=report,
                )

        with patch(
            "modal.Function.from_name",
            side_effect=AssertionError("Status must not invoke provider functions"),
        ):
            stage_data(
                job, paths, "fixture-run", preparation, volume, Provider(), num_examples=3002
            )
    app.send_task.assert_not_called()
    assert collected[-1]["completed"] == 3004
    assert [
        json.loads(line)["input_ids"][0]
        for line in (tmp_path / "data.out").read_text().splitlines()
    ] == list(range(3000)) + [0, 1]
    assert [
        json.loads(line)["input_ids"][0] for line in (tmp_path / "val.out").read_text().splitlines()
    ] == [2998, 2999]
