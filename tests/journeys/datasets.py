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
            "input": {
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"Refund order {i} please"},
                ]
            },
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


def training_rows(tmp_path, n=12):
    path = tmp_path / "train.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "messages": [
                        {"role": "system", "content": "You are a support agent."},
                        {"role": "user", "content": f"Refund order {i}"},
                        {"role": "assistant", "content": f"Order {i} was delivered."},
                    ]
                }
            )
            + "\n"
            for i in range(n)
        )
    )
    return path


def until(beat, condition, *tasks, limit=30):
    for _ in range(limit):
        beat(*tasks)
        if condition():
            return
    raise AssertionError(f"{condition.__name__} not met after {limit} ticks")
