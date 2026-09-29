import json

from .conftest import GOOD_REPLY

SYSTEM = "You are a support agent. Answer the customer in two sentences."
JUDGE = "gpt-5.6-terra"


def upload(cli, sample_agent, path, *extra) -> str:
    out = cli.run("dataset", "upload", str(path), "--json", *extra, cwd=sample_agent.repo)
    return json.loads(out)["id"]


def tickets(tmp_path, n=3):
    path = tmp_path / "tickets.jsonl"
    rows = [
        {
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Refund order {i} please"},
            ],
            "expected_output": GOOD_REPLY,
        }
        for i in range(n)
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def evaluate(mcp, dataset):
    return mcp.call(
        "run_evaluation",
        {
            "name": "candidate",
            "dataset": dataset,
            "judge_model": JUDGE,
            "variants": [
                {"mode": "generate", "model_name": "fake/candidate", "label": "candidate"}
            ],
        },
    )
