import argparse
import base64
import json
import os
import shlex
import time
from pathlib import Path
from uuid import uuid4

from daytona import Daytona, DaytonaConfig
from dotenv import load_dotenv

parser = argparse.ArgumentParser()
parser.add_argument("--sandbox", required=True)
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()
load_dotenv(Path.home() / ".config/overmind/workshop.env")
client = Daytona(
    DaytonaConfig(
        api_key=os.environ["DAYTONA_API_KEY"],
        api_url="https://app.daytona.io/api",
        otel_enabled=False,
    )
)
sandbox = client.get(args.sandbox)
task = str(uuid4())
root = "/home/daytona/overmind/" + task
python = "/opt/overmind-runtime/venv/bin/python"
report = {"sandbox": sandbox.id, "root": root, "checks": [], "model_calls": 0}


def command(code):
    encoded = base64.b64encode(code.encode()).decode()
    response = sandbox.process.exec(
        f"{python} -c {shlex.quote('import base64;exec(base64.b64decode(' + repr(encoded) + '))')}",
        timeout=60,
    )
    if response.exit_code:
        raise RuntimeError(response.result[-4000:])
    return response.result


def agent(operation, payload):
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    entry = "workshop_runtime.mailbox" if operation == "exchange" else "workshop_runtime.agent"
    action = "" if operation == "exchange" else f" {operation}"
    result = sandbox.process.exec(
        f"{python} -m {entry} --root {root}{action} --payload {encoded}",
        timeout=60,
    )
    if result.exit_code:
        raise RuntimeError(result.result[-4000:])
    return json.loads(result.result)


def reply(name, arguments):
    return {
        "status": 200,
        "body": {
            "id": "resp_" + uuid4().hex,
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "test",
            "output": [
                {
                    "id": "fc_" + uuid4().hex,
                    "call_id": "call_" + uuid4().hex,
                    "type": "function_call",
                    "name": name,
                    "arguments": json.dumps(arguments),
                    "status": "completed",
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        },
    }


command(
    f"from pathlib import Path\nimport pyarrow as pa\nimport pyarrow.parquet as pq\np=Path({root!r})\n(p/'inputs').mkdir(parents=True)\npq.write_table(pa.Table.from_pylist([{{'source_row':i,'value':i,'text':'alpha\\u0085beta\\u2028gamma\\u2029omega','identifier':2**53+1 if i%2==0 else None,'_overmind_provenance':{{'source_group_keys':[['family',str(min(i,11-i))]]}}}} for i in range(12)]),p/'inputs/input.parquet')"
)
configuration = {
    "task": task,
    "model": "test",
    "objective": "Deterministic runtime qualification",
}
command(
    f"from pathlib import Path\nfrom workshop_runtime.skills import files\nfor name,text in files({{k: {{'qualification_fixture': True}} for k in ['decision_training','decision_evaluation','sft','model_evaluation','shared']}}).items():\n p=Path({root!r})/'context'/name\n p.parent.mkdir(parents=True,exist_ok=True)\n p.write_text(text)"
)
setup = """import json, zipfile
from pathlib import Path
import pandas as pd
from workshop_runtime.environment import identity
from workshop_runtime.data import read_frame
frame=read_frame('data/input.parquet')
assert frame.value.sum()==66
assert frame.loc[0,'identifier']==2**53+1 and pd.isna(frame.loc[1,'identifier'])
scratch_only=123
Path('scratch-secret.txt').write_text('not a declared replay input')
"""
script = f"""import json,sys
from pathlib import Path
try:
    Path({(root + "/work/scratch-secret.txt")!r}).read_text()
except PermissionError:
    pass
else:
    raise AssertionError('Undeclared exploratory file was readable')
with open(sys.argv[1]) as source, open(sys.argv[2],'w') as output:
    for line in source:
        row=json.loads(line)
        assert row['identifier'] == (2**53+1 if row['source_row']%2 == 0 else None)
        if 'mixed_numeric' in row:
            assert type(row['mixed_numeric']) is float
            assert row['optional'] == ('present' if row['source_row']%2 == 0 else None)
        else:
            row['mixed_numeric']=row['source_row'] if row['source_row']%2 == 0 else row['source_row']+0.5
            if row['source_row']%2 == 0: row['optional']='present'
        row['value']*=2
        output.write(json.dumps(row,ensure_ascii=False)+'\\n')
"""
setup += (
    "manifest="
    + repr(
        {
            "version": 1,
            "parameters": {},
            "steps": [
                {
                    "id": "scale",
                    "name": "Scale",
                    "entrypoint": "scale.py",
                    "checks": {"preserve_rows": True},
                },
                {
                    "id": "rescale",
                    "name": "Rescale",
                    "input": "scale",
                    "entrypoint": "scale.py",
                    "checks": {"preserve_rows": True},
                },
            ],
        }
    )
    + "\nmanifest['runtime']=identity()\n"
)
setup += (
    "with zipfile.ZipFile('recipe.zip','w') as z:\n    z.writestr('manifest.json',json.dumps(manifest))\n    z.writestr('scale.py',"
    + repr(script)
    + ")\n"
)
choices = [
    (
        "python",
        {"code": setup + "\nprint('start-' + 'x'*20000 + '-end')", "skills": ["decision-data"]},
    ),
    ("python", {"code": "int(frame.value.sum())/0"}),
    (
        "python",
        {
            "code": "import time\np=Path('once.txt')\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')\ntime.sleep(15)\nassert frame.value.mean()==5.5"
        },
    ),
    (
        "replay",
        {
            "package": "recipe.zip",
            "partition": {
                "seed": 42,
                "fractions": {"train": 0.75, "final": 0.25},
                "group_by": [],
                "stratify_by": None,
                "holdouts": [],
            },
        },
    ),
    (
        "python",
        {
            "code": "paths=list(Path('replays').glob('*/output-1.jsonl'))\nassert len(paths)==1\nfrom workshop_runtime.replay import read_rows\nrows=read_rows(paths[0])\nassert all(r['text']=='alpha\\u0085beta\\u2028gamma\\u2029omega' for r in rows)\nassert [r['value'] for r in rows]==list(range(0,48,4))\nassert Path('once.txt').read_text()=='1'\nassert scratch_only==123\nprint({'rows':len(rows),'sum':sum(r['value'] for r in rows)})"
        },
    ),
    (
        "finish",
        {
            "outcome": "blocked",
            "reason": "Qualification fixture intentionally has no requested publication; all execution assertions completed.",
        },
    ),
]
try:
    snapshot = agent("launch", configuration)
    delivered = set()
    deadline = time.monotonic() + 240
    killed = False
    while time.monotonic() < deadline:
        snapshot = agent("exchange", {})
        for request in snapshot["pending"]:
            if request["id"] in delivered:
                continue
            name, arguments = choices[len(delivered)]
            snapshot = agent("exchange", {"responses": {request["id"]: reply(name, arguments)}})
            delivered.add(request["id"])
            if len(delivered) == 3:
                time.sleep(1)
                text = command(
                    f"import os,signal\nfrom pathlib import Path\nfrom workshop_runtime.session import request\nh=request({root!r},{{'operation':'health'}})\nassert h['active']\nfor p in Path('/proc').glob('[0-9]*/cmdline'):\n    try: command=p.read_bytes().split(b'\\0')\n    except (OSError,ProcessLookupError): continue\n    if b'workshop_runtime.agent' in command and {root.encode()!r} in command and b'run' in command: os.kill(int(p.parent.name),signal.SIGKILL)\nprint('active runner killed; kernel retained')"
                )
                agent(
                    "exchange",
                    {
                        "messages": [
                            {
                                "id": "1",
                                "text": "Keep all twelve records and explain the check.",
                                "cells": [],
                            }
                        ]
                    },
                )
                agent("launch", configuration)
                report["checks"].append(text.strip())
                killed = True
        if (
            snapshot["state"]["state"] in {"blocked", "completed", "cancelled"}
            and not snapshot["alive"]
        ):
            break
        time.sleep(0.3)
    else:
        raise RuntimeError("Qualification did not finish")
    events = snapshot["state"].get("events", [])
    report["events"] = events
    assert len(delivered) == 6 and len(events) == 6, snapshot["state"].get("reason")
    assert events[1]["error"] == "ZeroDivisionError", events
    assert not any(e["error"] for i, e in enumerate(events) if i != 1), events
    assert killed
    first = json.loads(snapshot["state"]["messages"][2]["parts"][0]["content"])
    assert first["compacted"] and first["output"]["tail"].endswith("-end\n")
    assert "qualification_fixture" in first["skills"]["decision-data"]
    original = json.loads(sandbox.fs.download_file(root + "/work/" + first["full_receipt"]))
    assert "x" * 20000 in original["result"]["output"]
    report["checks"].append(
        "local skill and complete receipt survive compact observation and runner recovery"
    )
    replay_receipt = json.loads(sandbox.fs.download_file(root + "/.agent/event-000003.json"))[
        "result"
    ]
    assert replay_receipt["state"] == "completed", replay_receipt
    assert sum(replay_receipt["partition"]["counts"].values()) == 12
    assignment_bytes = sandbox.fs.download_file(
        root + "/work/" + replay_receipt["assignments_path"]
    )
    assignments = [json.loads(line) for line in assignment_bytes.splitlines()]
    roles = {row["row"]: row["role"] for row in assignments}
    assert all(roles[i] == roles[11 - i] for i in range(6)), roles
    report["replay"] = replay_receipt
    report["checks"] += [
        "local data exploration",
        "error and repair in persistent kernel",
        "runner recovery without duplicate mutation",
        "clean replay denies undeclared scratch reads",
        "local partition assignment preserves inherited groups across two transformations",
        "sparse columns and mixed numeric types match materialized table semantics between steps",
        "nullable source integers retain exact precision through exploration and replay",
        "all transformed values checked",
        "Unicode line separators survive two-step replay without changing text",
        "terminal runner exit",
    ]
    assert snapshot["state"]["accepted_messages"] == ["1"]
    agent(
        "exchange",
        {
            "messages": [
                {
                    "id": "2",
                    "text": "Explain the retained result without publishing anything.",
                    "cells": [],
                }
            ]
        },
    )
    agent("launch", configuration)
    deadline = time.monotonic() + 90
    answered = False
    while time.monotonic() < deadline:
        snapshot = agent("exchange", {})
        for request in snapshot["pending"]:
            assert request["kind"] == "model"
            content = json.dumps(request["arguments"]["input"])
            assert content.count("Explain the retained result without publishing anything.") == 1
            agent(
                "exchange",
                {
                    "responses": {
                        request["id"]: reply(
                            "finish",
                            {
                                "outcome": "answered",
                                "reason": "Twelve retained records were checked; no new publication was requested.",
                            },
                        )
                    }
                },
            )
            answered = True
        if snapshot["state"]["state"] == "completed" and not snapshot["alive"]:
            break
        time.sleep(0.3)
    assert answered and snapshot["state"]["state"] == "completed", snapshot
    assert snapshot["state"]["accepted_messages"] == ["1", "2"]
    assert snapshot["state"]["answered"]
    report["checks"] += [
        "follow-up survives runner recovery exactly once",
        "late follow-up restarts a settled runner with retained history",
        "answer-only completion publishes no data",
    ]
    task = str(uuid4())
    root = "/home/daytona/overmind/" + task
    agent("launch", {"task": task, "model": "test", "objective": "Cancellation check"})
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        snapshot = agent("exchange", {})
        if snapshot["pending"]:
            break
        time.sleep(0.2)
    assert snapshot["pending"], snapshot
    stopped_at = time.monotonic()
    agent("exchange", {"control": "cancelled"})
    while time.monotonic() < deadline:
        snapshot = agent("exchange", {})
        if not snapshot["alive"]:
            break
        time.sleep(0.2)
    assert not snapshot["alive"] and snapshot["state"]["state"] == "cancelled", snapshot
    assert not snapshot["state"].get("reason"), snapshot
    report["cancellation_seconds"] = time.monotonic() - stopped_at
    report["checks"].append("model-wait cancellation exits without a spurious connection error")
    report["passed"] = True
finally:
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
