import json

import pytest

from overbae.core.model_registry import WORKSHOP_ENGINES

from .datasets import tickets, upload
from .stack import drain

ENGINES = [e for e in WORKSHOP_ENGINES if e.provider.name != "cursor"]


def _add_cell_turn(request):
    if any(m.get("role") == "tool" for m in request.messages[-2:]):
        return {"content": "Kept the first two rows."}
    call = {"title": "Keep two rows", "script": "df = df.head(2)\n"}
    return {
        "content": None,
        "tool_calls": [
            {
                "id": "call_keep",
                "type": "function",
                "function": {"name": "add_cell", "arguments": json.dumps(call)},
            }
        ],
    }


@pytest.mark.parametrize("engine", ENGINES, ids=[e.provider.name for e in ENGINES])
def test_every_workshop_engine_edits_a_dataset_through_its_own_api(
    engine, workshop, cli, sample_agent, worker, fake_llm, monkeypatch, tmp_path
):
    dataset = upload(cli, sample_agent, tickets(tmp_path), "--intent", "eval")
    drain(worker)
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv(engine.provider.key_env, "sk-fake")
    host = (engine.provider.base_url or "https://api.openai.com/v1").rstrip("/")
    fake_llm.on(
        lambda r: r.url.startswith(host) and "Keep the first two rows" in r.text, _add_cell_turn
    )

    workshop.call(
        "message_dataset_agent", {"dataset": dataset, "message": "Keep the first two rows."}
    )
    drain(worker)

    [proposal] = [
        c
        for c in workshop.call("inspect_dataset", {"dataset": dataset})["cells"]
        if c["state"] == "proposed"
    ]
    workshop.call("run_dataset", {"dataset": dataset, "proposal_cell": proposal["id"]})
    drain(worker)

    inspected = workshop.call("inspect_dataset", {"dataset": dataset})
    active = inspected["active"]
    assert active["title"] == "Keep two rows"
    assert active["rows"] == 2
    served = {r.model for r in fake_llm.requests if r.url.startswith(host)}
    assert any(model.endswith(expected) for model in served for expected in engine.models)
    turns = [r.body for r in fake_llm.requests if r.url.startswith(host) and r.body.get("tools")]
    provider = engine.provider
    if provider.output_cap_param != "max_tokens":
        assert all("max_tokens" not in body for body in turns)
    if provider.tools_reasoning_effort:
        assert all(
            body.get("reasoning_effort") == provider.tools_reasoning_effort for body in turns
        )
