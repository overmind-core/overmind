import json

from django.utils import timezone

from overbae.models import (
    APIToken,
    DedicatedDeployment,
    DeployedModel,
    OperationalRun,
    Project,
    ProjectMembership,
)

CONTROLLER = "overbae.tasks.inference_controller.reconcile_deployments"


def test_an_agent_configures_inspects_and_stops_private_inference(
    workshop, fake_modal, beat, mcp_for, account, rest_for
):
    project = workshop.project()["id"]
    deployed = DeployedModel.objects.create(
        project_id=project,
        model_id="ft-dedicated-agent",
        base_model_id="meta-llama/Llama-3.2-3B-Instruct",
        status="ready",
        gpu_type="L4",
        weights_path="/weights/dedicated-agent",
    )
    fake_modal.deploy(
        "overmind-dedicated",
        "H100_vllm_dedicated.infer",
        lambda **_: {"status": 200, "headers": {}, "body": b"{}"},
    )
    fake_modal.worker_stats = {"num_total_runners": 1, "num_running_inputs": 0, "backlog": 0}
    assert "inspect_dedicated_inference" in workshop.discover()["tools"]
    inspected = workshop.call("inspect_dedicated_inference", {"deployment": str(deployed.pk)})
    assert inspected["models"][0]["available"]
    assert inspected["models"][0]["dedicated"] is None
    options = workshop.call("list_dedicated_gpus", {"deployment": str(deployed.pk)})
    assert "H100" in {gpu["gpu_type"] for gpu in options["gpus"]}
    recipe = {
        "deployment": str(deployed.pk),
        "request_key": "private-endpoint",
        "gpu_type": "H100",
        "min_replicas": 1,
        "max_replicas": 2,
    }
    started = workshop.call("start_dedicated_deployment", recipe)
    receipt = workshop.call("get_job", {"kind": started["kind"], "id": started["id"]})
    assert receipt["status"] == "deploying"
    assert receipt["details"]["deployment"] == str(deployed.pk)
    retried = workshop.call("start_dedicated_deployment", recipe)
    assert retried["id"] == started["id"]
    conflict = workshop.call_result("start_dedicated_deployment", {**recipe, "max_replicas": 3})
    assert conflict.isError
    assert json.loads(conflict.content[0].text)["error"]["code"] == "request_key_conflict"
    assert DedicatedDeployment.objects.filter(deployed_model=deployed).count() == 1
    uri = started["resource"]["uri"]
    before = list(fake_modal.log)
    assert workshop.read(uri)["status"] == "deploying"
    assert fake_modal.log == before
    beat(CONTROLLER)
    beat(CONTROLLER)
    assert workshop.read(uri)["status"] == "online"
    assert (
        workshop.call("get_job", {"kind": started["kind"], "id": started["id"]})["status"]
        == "online"
    )
    updated = workshop.call(
        "update_dedicated_deployment", {"dedicated_deployment": started["id"], "max_replicas": 3}
    )
    assert updated["max_replicas"] == 3
    malformed = workshop.call_result(
        "update_dedicated_deployment", {"dedicated_deployment": started["id"], "max_replicas": None}
    )
    assert malformed.isError
    foreign = Project.objects.create(name="Other", slug="dedicated-other")
    ProjectMembership.objects.create(user=account[0], project=foreign)
    foreign_key, _ = APIToken.create_for_user(account[0], project=foreign)
    outsider = mcp_for(foreign_key)
    assert outsider.call_result(
        "stop_dedicated_deployment", {"dedicated_deployment": started["id"]}
    ).isError
    try:
        outsider.read(uri)
    except LookupError:
        pass
    else:
        raise AssertionError("Dedicated resource crossed projects")
    stopped = workshop.call("stop_dedicated_deployment", {"dedicated_deployment": started["id"]})
    assert stopped["status"] == "shutting_down"
    fake_modal.worker_stats = {"num_total_runners": 0, "num_running_inputs": 0, "backlog": 0}
    beat(CONTROLLER)
    assert workshop.read(uri)["status"] == "offline"
    assert (
        workshop.call("stop_dedicated_deployment", {"dedicated_deployment": started["id"]})[
            "status"
        ]
        == "offline"
    )
    assert workshop.call("start_dedicated_deployment", recipe)["id"] == started["id"]
    assert workshop.read(uri)["status"] == "offline"
    restarted = workshop.call("start_dedicated_deployment", {**recipe, "request_key": "restart"})
    assert restarted["id"] != started["id"]
    receipt = rest_for(workshop.api_key).request(
        "POST",
        f"/api/deployed-models/{deployed.pk}/dedicated/",
        json={key: value for key, value in recipe.items() if key != "deployment"},
    )
    assert receipt["id"] == started["id"]
    scoped = mcp_for(account[1]).call(
        "inspect_dedicated_inference", {"project_id": project, "deployment": str(deployed.pk)}
    )
    assert f"project_id={project}" in scoped["models"][0]["dedicated"]["resource"]["uri"]
    read_key, _ = APIToken.create_for_user(
        account[0], project=deployed.project, permission=["read"]
    )
    reader = mcp_for(read_key)
    assert "inspect_dedicated_inference" in reader.discover()["tools"]
    assert "start_dedicated_deployment" not in reader.discover()["tools"]
    assert reader.read(uri)["id"] == started["id"]
    assert (
        workshop.call("stop_dedicated_deployment", {"dedicated_deployment": started["id"]})[
            "status"
        ]
        == "offline"
    )
    assert workshop.read(restarted["resource"]["uri"])["status"] == "deploying"
    workshop.call("stop_dedicated_deployment", {"dedicated_deployment": restarted["id"]})
    beat(CONTROLLER)


def test_failed_dedicated_state_remains_readable_without_provider_reads(workshop, fake_modal):
    deployed = DeployedModel.objects.create(
        project_id=workshop.project()["id"], model_id="ft-failed-dedicated", status="ready"
    )
    row = DedicatedDeployment.objects.create(
        project=deployed.project,
        deployed_model=deployed,
        status="failed",
        gpu_type="H100",
        rate_usd_per_gpu_hour=1,
        credits_per_gpu_hour=100,
        error_message="Dedicated worker is not deployed.",
        probe_call_id="failed-dedicated-probe",
    )
    OperationalRun.objects.create(
        project=deployed.project,
        kind="dedicated_deployment",
        reference=str(row.pk),
        attempt=row.probe_call_id,
        provider_state={
            "logs": [
                {
                    "stage": "loading_weights",
                    "message": f"Tensor {i}",
                    "at": timezone.now().isoformat(),
                    "worker": "fake-worker",
                    "completed": i,
                    "total": 150,
                    "unit": "tensors",
                }
                for i in range(150)
            ]
        },
    )
    before = list(fake_modal.log)
    inspected = workshop.call("inspect_dedicated_inference", {"deployment": str(deployed.pk)})
    model = inspected["models"][0]
    assert not model["available"]
    assert model["reason"]
    assert model["dedicated"]["id"] == str(row.pk)
    saved = workshop.read(model["dedicated"]["resource"]["uri"])
    assert saved["status"] == "failed"
    assert saved["error"] == row.error_message
    assert len(saved["startup"]["logs"]) == 100
    assert saved["startup"]["truncated"]
    assert model["dedicated"]["startup"]["truncated"]
    assert saved["startup"]["logs_total"] == 150
    assert saved["startup"]["next_log_offset"] == 100
    uri = model["dedicated"]["resource"]["uri"]
    page = workshop.read(uri + ("&" if "?" in uri else "?") + "log_offset=100")
    assert len(page["startup"]["logs"]) == 50
    assert page["startup"]["next_log_offset"] is None
    assert fake_modal.log == before
