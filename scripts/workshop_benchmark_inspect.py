import argparse
import json
from pathlib import Path

from daytona import Daytona, DaytonaConfig
from dotenv import load_dotenv

parser = argparse.ArgumentParser()
parser.add_argument("case")
parser.add_argument("--sandbox", required=True)
parser.add_argument("--last", type=int, default=2)
args = parser.parse_args()
directory = Path("output/workshop-benchmark") / args.case
state = json.loads((directory / "state.json").read_text())
attempt = state["attempts"][-1]
load_dotenv(Path.home() / ".config/overmind/workshop.env")
client = Daytona(DaytonaConfig(api_url="https://app.daytona.io/api", otel_enabled=False))
sandbox = client.get(args.sandbox)
root = "/home/daytona/overmind/" + attempt["task"]
r = sandbox.process.exec(
    "/opt/overmind-runtime/venv/bin/python -m workshop_runtime.agent --root "
    + root
    + " exchange --payload e30=",
    timeout=60,
)
if r.exit_code:
    raise RuntimeError(r.result[-1000:])
snapshot = json.loads(r.result)
s = snapshot["state"]
print(
    json.dumps(
        {
            "case": args.case,
            "alive": snapshot["alive"],
            "state": s.get("state"),
            "turn": s.get("turn"),
            "reason": s.get("reason"),
            "events": s.get("events", []),
            "pending": [{"kind": r["kind"], "turn": r["turn"]} for r in snapshot["pending"]],
        }
    )
)
for event in s.get("events", [])[-args.last :]:
    full = json.loads(sandbox.fs.download_file(root + f"/.agent/event-{event['turn']:06}.json"))
    target = directory / f"attempt-{attempt['number']}-events"
    target.mkdir(exist_ok=True)
    (target / f"{event['turn']:06}.json").write_text(json.dumps(full, indent=2))
    print(
        json.dumps(
            {
                "turn": event["turn"],
                "tool": event["tool"],
                "arguments": str(full["arguments"])[:900],
                "result": str(full["result"])[-2200:],
            }
        )
    )
