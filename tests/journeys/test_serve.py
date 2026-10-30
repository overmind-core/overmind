import json

import pytest
from overmind.client import Client, OvermindInferenceError

from overbae.models import DedicatedDeployment, DeployedModel, Project
from overbae.services.provider_pricing import refresh_rates

from .datasets import until

DEPLOY = "overbae.tasks.inference_controller.reconcile_deployments"
MESSAGES = [{"role": "user", "content": "Where is order 42?"}]


def _dedicated_reply(**_kwargs):
    return {
        "status": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(
            {
                "model": "dedicated/internal",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Order 42 left the depot."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 5, "total_tokens": 6},
            }
        ).encode(),
    }


def test_serve(
    cli, mcp_for, sample_agent, live_api, fake_llm, fake_modal, scripted, worker, beat, console
):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    key = cli.project_key(sample_agent)
    mcp = mcp_for(key)
    project = Project.objects.get(pk=mcp.project()["id"])
    DeployedModel.objects.create(
        project=project,
        model_id="ft-sdk-journey",
        status=DeployedModel.Status.READY,
        base_model_id="Qwen/Qwen3-1.7B",
        max_model_len=16384,
        gpu_type="L4",
    )
    client = Client(api_key=key, base_url=live_api.url)
    fake_llm.on(lambda r: "inference.test" in r.url, "Order 42 shipped today.")
    fake_llm.on(lambda r: "openrouter.ai" in r.url, "Order 42 is in transit.")

    served = client.chat.completions.create(model="ft-sdk-journey", messages=MESSAGES)
    assert served.choices[0].message.content == "Order 42 shipped today."
    assert served.usage.total_tokens > 0
    streamed = client.chat.completions.create(
        model="ft-sdk-journey", messages=MESSAGES, stream=True
    )
    assert "".join(c.choices[0].delta.content or "" for c in streamed) == "Order 42 shipped today."
    frontier = client.chat.completions.create(model="openai/gpt-5-mini", messages=MESSAGES)
    assert frontier.choices[0].message.content == "Order 42 is in transit."
    assert "ft-sdk-journey" in {model.id for model in client.models.list().data}
    assert client.models.get("ft-sdk-journey").id == "ft-sdk-journey"
    with pytest.raises(OvermindInferenceError, match="HTTP 404"):
        client.models.get("ft-nobody")

    refresh_rates()
    fake_modal.deploy("overmind-dedicated", "H100_vllm.infer", _dedicated_reply)
    fake_modal.worker_stats = {"num_total_runners": 1, "backlog": 0, "num_running_inputs": 0}
    started = mcp.call(
        "start_dedicated_deployment",
        {"deployment": "ft-sdk-journey", "gpu_type": "H100", "min_replicas": 1, "max_replicas": 1},
    )

    def status() -> str:
        return DedicatedDeployment.objects.get(pk=started["id"]).status

    until(beat, lambda: status() == "online", DEPLOY)
    dedicated = client.chat.completions.create(model=started["model_id"], messages=MESSAGES)
    assert dedicated.choices[0].message.content == "Order 42 left the depot."
    assert started["model_id"] in {model.id for model in client.models.list().data}

    mcp.call("stop_dedicated_deployment", {"deployment": "ft-sdk-journey"})
    fake_modal.worker_stats = {"num_total_runners": 0, "backlog": 0, "num_running_inputs": 0}
    until(beat, lambda: status() == "offline", DEPLOY)
    deployed = DeployedModel.objects.get(model_id="ft-sdk-journey")
    console.goto(f"/inference/{deployed.pk}?projectId={project.pk}")
    console.sees("Offline")
    console.sees("0 running · 1–1 configured")

    scripted("http://inference.test").reply(json_body={"ok": True})
    assert client.models.delete("ft-sdk-journey").deleted is True
    assert DeployedModel.objects.get(model_id="ft-sdk-journey").status == "deleted"
