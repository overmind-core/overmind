from datetime import datetime
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient
from starlette.testclient import TestClient
from test_data_first_workflow import workspace
from test_mcp_research_journey import call

from modal_shared.training_telemetry import read_telemetry, record_heartbeat, record_stage
from overbae.models import APIToken, FinetuningJob
from overbae.services.finetuning_runner import PollSnapshot
from overbae.services.mcp.server import create_mcp_application
from overbae.tasks.finetuning import _persist_snapshot_progress

pytestmark = pytest.mark.django_db(transaction=True)


def test_startup_stage_receipts_reach_console_and_mcp_without_provider_calls(tmp_path):
    project, _, dataset = workspace()
    user = project.memberships.select_related("user").first().user
    key, _ = APIToken.create_for_user(user, project=project)
    job = FinetuningJob.objects.create(
        project=project, dataset=dataset, base_model="fixture", status="running", provider="modal"
    )
    rest = APIClient()
    rest.force_authenticate(user)
    with (
        TestClient(create_mcp_application()) as mcp,
        patch("modal.Function.from_name", side_effect=AssertionError("Read invoked provider")),
    ):
        record_heartbeat(tmp_path, new_attempt=True)
        for stage, completed, total in (
            ("initializing_training_runtime", None, None),
            ("loading_model", None, None),
            ("configuring_adapters", None, None),
            ("verifying_training_tokenizer", None, None),
            ("loading_training_dataset", None, None),
            ("building_training_dataset", 250, 1000),
            ("building_training_dataset", 1000, 1000),
            ("initializing_trainer", None, None),
        ):
            record_stage(
                tmp_path, stage, completed=completed, total=total, unit="rows" if total else None
            )
            telemetry = read_telemetry(tmp_path)
            _persist_snapshot_progress(
                job,
                PollSnapshot(
                    state="running", stage=stage, diagnostics=telemetry, raw={"run_id": "fixture"}
                ),
                tick_evals=False,
            )
            response = rest.get(f"/api/finetuning-jobs/{job.pk}/loss-curves/")
            assert response.status_code == 200
            receipt = call(
                mcp,
                key,
                "get_job",
                {
                    "project_id": str(project.pk),
                    "kind": "finetune_job",
                    "id": str(job.pk),
                },
            )
            assert receipt["progress"]["diagnostics"] == response.data["progress"]["diagnostics"]
            assert receipt["progress"]["stage_label"]
            detail = receipt["progress"]["diagnostics"]
            assert detail["completed"] == completed
            assert detail["last_progress_at"] == telemetry["source_at"]
            operation = receipt["details"]["operation"]
            assert operation["stage"] == stage
            assert operation["completed"] == completed
            assert datetime.fromisoformat(
                operation["last_progress_at"]
            ).timestamp() == pytest.approx(telemetry["source_at"])
