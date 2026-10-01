import pytest
from overmind.client import Client, OvermindInferenceError

from overbae.models import DeployedModel, Project


@pytest.fixture
def client(cli, mcp_for, sample_agent, live_api):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    key = cli.project_key(sample_agent)
    project = Project.objects.get(pk=mcp_for(key).project()["id"])
    DeployedModel.objects.create(
        project=project,
        model_id="ft-sdk-journey",
        status=DeployedModel.Status.READY,
        base_model_id="Qwen/Qwen3-1.7B",
        max_model_len=16384,
    )
    return Client(api_key=key, base_url=live_api.url)


MESSAGES = [{"role": "user", "content": "Where is order 42?"}]


def test_the_sdk_client_reaches_served_and_frontier_models_through_the_gateway(
    client, fake_llm, scripted
):
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

    listed = {model.id for model in client.models.list().data}
    assert "ft-sdk-journey" in listed
    assert client.models.get("ft-sdk-journey").id == "ft-sdk-journey"
    with pytest.raises(OvermindInferenceError, match="HTTP 404"):
        client.models.get("ft-nobody")

    scripted("http://inference.test").reply(json_body={"ok": True})
    assert client.models.delete("ft-sdk-journey").deleted is True
    assert DeployedModel.objects.get(model_id="ft-sdk-journey").status == "deleted"
