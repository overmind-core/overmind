from types import SimpleNamespace

import pytest
from django.utils import timezone

from overbae.models import DedicatedDeployment, DeployedModel, Project
from overbae.services import dedicated_progress
from overbae.services.dedicated_progress import parse_log


def test_startup_counters_and_compilation_are_measured():
    assert parse_log(
        "Loading safetensors using Runai Model Streamer: 83% Completed | 643/775 [00:14]"
    ) == ("loading_weights", "Loading model weights", 643, 775, "tensors")
    assert parse_log("Capturing CUDA graphs (PIECEWISE): 50%|xx| 6/12 [00:12]") == (
        "capturing_graphs",
        "Capturing CUDA graphs (PIECEWISE)",
        6,
        12,
        "graphs",
    )
    assert parse_log("torch.compile took 100.30 s in total") == (
        "compiling_model",
        "Model compilation finished in 100.30s",
        None,
        None,
        None,
    )


def test_unknown_output_and_arbitrary_paths_are_not_published():
    assert parse_log("Authorization: Bearer secret") is None
    assert parse_log("user prompt: secret") is None
    assert parse_log("Starting vLLM: --api-key secret /private/path") == (
        "starting_engine",
        "Starting inference engine",
        None,
        None,
        None,
    )
    assert parse_log("Loading safetensors: 120% | 12/10") is None


@pytest.mark.django_db
def test_startup_logs_are_scoped_to_worker_and_terminal_state(fake_modal):
    project = Project.objects.create(name="Startup", slug="startup")
    model = DeployedModel.objects.create(
        project=project,
        model_id="startup-model",
        weights_path="model",
        base_model_id="meta-llama/Llama-3.2-3B-Instruct",
        gpu_type="H100",
    )
    row = DedicatedDeployment.objects.create(
        project=project,
        deployed_model=model,
        gpu_type="H100",
        probe_call_id="fc-startup",
        rate_usd_per_gpu_hour="3.95",
        credits_per_gpu_hour="395",
        status="warming",
    )
    now = timezone.now()

    def entry(message, worker):
        return SimpleNamespace(message=message, context_ids=[worker], timestamp=now)

    fake_modal.app_logs = [
        entry(f"Starting vLLM: --served-model-name {row.served_model_id}", "ta-owned"),
        entry("Loading safetensors: 50% | 5/10", "ta-owned"),
        entry("Loading safetensors: 100% | 10/10", "ta-other"),
        entry("Authorization: secret", "ta-owned"),
    ]
    dedicated_progress.collect(row)
    progress = dedicated_progress.summary(row)
    assert progress["completed"] == 5
    assert progress["total"] == 10
    assert {line["worker"] for line in progress["logs"]} == {"ta-owned"}
    assert "secret" not in str(progress)
    row.status = "online"
    progress = dedicated_progress.summary(row)
    assert progress["stage"] == "online"
    assert progress["label"] == "Endpoint ready"
    assert progress["completed"] is None
    row.status = "offline"
    progress = dedicated_progress.summary(row)
    assert progress["stage"] == "offline"
    assert progress["label"] == "Offline"
    row.probe_call_id = "fc-new-attempt"
    assert dedicated_progress.summary(row) is None
