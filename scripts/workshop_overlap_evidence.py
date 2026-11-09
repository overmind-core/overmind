import argparse
import hashlib
import json
import os
import shlex
from pathlib import Path

from daytona import Daytona, DaytonaConfig
from dotenv import load_dotenv

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
parser.add_argument("--sandbox", required=True)
args = parser.parse_args()
metrics = json.loads((args.directory / "metrics.json").read_text())
load_dotenv(Path.home() / ".config/overmind/workshop.env")
sandbox = Daytona(
    DaytonaConfig(
        api_key=os.environ["DAYTONA_API_KEY"],
        api_url="https://app.daytona.io/api",
        otel_enabled=False,
    )
).get(args.sandbox)
root = (
    "/home/daytona/overmind/"
    + metrics["task"]
    + "/.agent/replays/"
    + metrics["publication"]["replay"]
)
code = """import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
receipt = json.loads((root/'receipt.json').read_text())
assert receipt['state'] == 'completed' and len(receipt['steps']) == 1
count = 0
with (root/'output-0.jsonl').open() as handle:
    for count, line in enumerate(handle, 1):
        row = json.loads(line)
        assert row['source_row'] == count-1
        assert row.get('_overmind_parent_rows', [count-1]) == [count-1]
assert count == receipt['output_rows'] == 50000
print(json.dumps({'source_order_verified': True, 'rows': count}))
"""
response = sandbox.process.exec(
    "python -c " + shlex.quote(code) + " " + shlex.quote(root), timeout=30
)
assert response.exit_code == 0, response.result
order = json.loads(response.result)
destination = args.directory / "assignments.jsonl"
sandbox.fs.download_file(root + "/assignments.jsonl", str(destination))
with destination.open("rb") as handle:
    checksum = hashlib.file_digest(handle, "sha256").hexdigest()
assert checksum == metrics["publication"]["partition"]["report"]["assignments_sha256"]
evidence = {**order, "assignments_sha256": checksum}
(args.directory / "assignment-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
print(json.dumps(evidence))
