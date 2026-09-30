import json
import subprocess
import sys

from .conftest import GOOD_REPLY
from .stack import drain
from .surfaces import SDK

TICKETS = ("Refund order 42 please", "Where is order 42?")

RUN_ONE_DATAPOINT = """\
OPENAI_BASE_URL={llm} OPENAI_API_KEY=sk-fake OVERMIND_API_URL={api} OVERMIND_API_KEY={key} \
OVERMIND_ANALYTICS_ENABLED=false PYTHONPATH={sdk}:. {python} - <<'PY'
import json
import overmind
from openai import OpenAI
from support_desk.agent import answer

datapoint = __DATAPOINT_INPUT__
payload = json.loads(datapoint) if isinstance(datapoint, str) else datapoint
user = next(m["content"] for m in payload["messages"] if m["role"] == "user")
category, _, ticket = user.partition("] ")
overmind.init(service_name="support-desk", providers=["openai"])
print(answer(OpenAI(), ticket, category.lstrip("[")))
overmind.force_flush_traces(timeout_millis=10_000)
PY
"""


def _workshop_corrects_references(fake_llm) -> None:
    def offers(request, tool):
        return any(
            (t.get("function") or {}).get("name") == tool for t in request.body.get("tools") or []
        )

    def turn(request):
        if any(m.get("role") == "tool" for m in request.messages[-2:]):
            return {"content": "Set the corrected reply as the reference."}
        script = f"df['expected_output'] = {GOOD_REPLY!r}\n"
        call = {"title": "Correct the references", "script": script}
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_references",
                    "type": "function",
                    "function": {"name": "add_cell", "arguments": json.dumps(call)},
                }
            ],
        }

    fake_llm.on(lambda r: offers(r, "add_cell") and "failures, not references" in r.text, turn)


def _candidate_diff(repo) -> str:
    agent = repo / "support_desk" / "agent.py"
    original = agent.read_text()
    agent.write_text(
        original.replace(
            '"Use lookup_order when the ticket names an order."',
            '"Use lookup_order when the ticket names an order. Quote the order id and its status."',
        )
    )
    diff = subprocess.run(
        ["git", "diff"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    agent.write_text(original)
    return diff


def test_failing_traces_become_a_measured_winning_diff(
    cli,
    mcp_for,
    sample_agent,
    worker,
    live_api,
    support_desk_llm,
    rubric_judges,
    fake_llm,
    tmp_path,
):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    key = cli.project_key(sample_agent)
    mcp = mcp_for(key)
    answer = mcp.capability("answer")

    sample_agent.run(*TICKETS, api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    executions = mcp.call("query_task_executions", {"capability": "answer"})["task_executions"]
    assert len(executions) == len(TICKETS)
    assert all(e["success_score"] < 1 for e in executions)

    created = mcp.call(
        "create_dataset_from_traces",
        {
            "name": "answer misses",
            "trace_ids": [e["trace_id"] for e in executions],
            "intent": "eval",
            "capability": answer["id"],
        },
    )
    dataset = created["dataset"]["id"]
    drain(worker)
    _workshop_corrects_references(fake_llm)
    mcp.call(
        "message_dataset_agent",
        {"dataset": dataset, "message": "These rows are failures, not references. Correct them."},
    )
    drain(worker)
    references = mcp.call(
        "query_dataset", {"dataset": dataset, "sql": "select distinct expected_output from t"}
    )["rows"]
    assert references == [{"expected_output": GOOD_REPLY}]

    repo = sample_agent.repo
    cli.run("optimise", "start", "-c", "answer", "-d", dataset, "--iterations", "5", cwd=repo)
    template = tmp_path / "run_one_datapoint.sh"
    template.write_text(
        RUN_ONE_DATAPOINT.format(
            llm=support_desk_llm, api=live_api.url, key=key, sdk=SDK, python=sys.executable
        )
    )
    cli.run("optimise", "set-template", str(template), cwd=repo)
    cli.run("optimise", "run-smoke", cwd=repo)
    cli.run("optimise", "run-baseline", cwd=repo)
    patch = tmp_path / "candidate.diff"
    patch.write_text(_candidate_diff(repo))
    cli.run("optimise", "add-candidate", "--diff", str(patch), cwd=repo)
    cli.run("optimise", "run-iteration", cwd=repo)
    assert "Action: WRITE_CANDIDATES" in cli.run("optimise", "next", cwd=repo)
    for _ in range(3):
        cli.run("optimise", "add-candidate", "--diff", str(patch), cwd=repo)
        cli.run("optimise", "run-iteration", cwd=repo)
    assert "Action: COMPLETE" in cli.run("optimise", "next", cwd=repo)
    cli.run("optimise", "complete", cwd=repo)

    state = json.loads((repo / ".overmind" / "optimise_state.json").read_text())
    result = mcp.call("inspect_optimizer_result", {"experiment": state["experiment_id"]})
    scores = result["experiment"]["scores"]
    assert result["experiment"]["status"] == "completed"
    assert result["experiment"]["cell"]["version"] == "2.0"
    assert scores["best"] > scores["baseline"]

    [baseline] = result["iterations"][0]["candidates"]
    [candidate] = result["iterations"][1]["candidates"]
    runs = [mcp.read(c["eval_run"]["uri"]) for c in (baseline, candidate)]
    assert runs[0]["dataset"] == runs[1]["dataset"] == dataset
    assert candidate["score"] > baseline["score"]

    subprocess.run(["git", "apply", "--check", str(patch)], cwd=repo, check=True)
    assert candidate["patch"].strip() == patch.read_text().strip()
