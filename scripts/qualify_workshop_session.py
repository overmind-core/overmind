import argparse
import base64
import hashlib
import json
import os
import shlex
import time
from pathlib import Path
from uuid import uuid4

from daytona import Daytona, DaytonaConfig
from dotenv import load_dotenv

from workshop_runtime.environment import identity

parser = argparse.ArgumentParser()
parser.add_argument("--sandbox", required=True)
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()
load_dotenv(Path.home() / ".config/overmind/workshop.env")
c = Daytona(
    DaytonaConfig(
        api_key=os.environ["DAYTONA_API_KEY"],
        api_url="https://app.daytona.io/api",
        otel_enabled=False,
    )
)
s = c.get(args.sandbox)
run = str(uuid4())
root = "/home/daytona/overmind/" + run
prefix = "/opt/overmind-runtime/venv/bin/python -m workshop_runtime.session --root " + shlex.quote(
    root
)
report = {"sandbox": s.id, "root": root, "checks": []}
out = args.report
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, indent=2))


def record(name, value):
    report["checks"].append({"name": name, "result": value})
    out.write_text(json.dumps(report, indent=2))
    print(name, json.dumps(value), flush=True)


def command(text):
    r = s.process.exec(text, timeout=60)
    if r.exit_code:
        raise RuntimeError(r.result[-3000:])
    return r.result


def call(payload):
    return json.loads(
        command(
            prefix
            + " request --payload "
            + shlex.quote(base64.b64encode(json.dumps(payload).encode()).decode())
        )
    )


def settle(e):
    end = time.monotonic() + 50
    while time.monotonic() < end:
        r = call({"operation": "status", "execution": e})
        if r["state"] in ["completed", "unknown"]:
            return r
        time.sleep(0.2)
    raise RuntimeError("execution not settled")


def execute(code):
    e = str(uuid4())
    call({"operation": "submit", "execution": e, "code": code})
    r = settle(e)
    if r.get("exit_code") != 0:
        raise RuntimeError(json.dumps(r))
    return r


runtime = json.loads(
    command("/opt/overmind-runtime/venv/bin/python -m workshop_runtime.environment")
)
assert runtime["ready"] and runtime["identity"] == identity()
report["runtime"] = runtime["identity"]
command(
    "mkdir -p "
    + shlex.quote(root + "/inputs")
    + " "
    + shlex.quote(root + "/context")
    + " "
    + shlex.quote(root + "/work")
)
original = b"value\n10\n\n30\n"
s.fs.upload_file(original, root + "/inputs/probe.csv")
s.fs.upload_file(b'{"input":"data/probe.csv"}', root + "/context/input.json")
ready = json.loads(command(prefix + " ensure"))
record("kernel_ready", ready)
r = execute(
    "import os,pandas as pd\nfrom pathlib import Path\nassert os.geteuid()==10001\nassert not any(k in os.environ for k in ['OPENROUTER_API_KEY','DAYTONA_API_KEY','OPENAI_API_KEY'])\ndf=pd.read_csv('data/probe.csv',skip_blank_lines=False)\nassert len(df)==3\nprint({'rows':len(df),'uid':os.geteuid()})"
)
record("local_data_and_user", r)
r = execute(
    "assert df['value'].isna().sum()==1\nassert df['value'].mean()==20\nprint(df.to_dict())"
)
record("shared_state", r)
e = str(uuid4())
call({"operation": "submit", "execution": e, "code": "df['missing_column']"})
r = settle(e)
assert r["exit_code"] == 1 and "KeyError" in r["output"]
record("structured_error", r)
r = execute("assert df['value'].fillna(df['value'].median()).tolist()==[10,20,30]")
record("repair_after_error", r)
r = execute(
    "from pathlib import Path\nfor p in [Path('data/probe.csv'),Path('../.session/kernel.json')]:\n try:\n  p.write_text('corruption')\n except PermissionError:\n  pass\n else:\n  raise AssertionError('protected path was writable')"
)
assert s.fs.download_file(root + "/inputs/probe.csv") == original
record("protected_inputs_and_journal", r)
e = str(uuid4())
payload = {
    "operation": "submit",
    "execution": e,
    "code": "import time\nfrom pathlib import Path\np=Path('once.txt')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\ntime.sleep(2)",
}
call(payload)
c = Daytona(
    DaytonaConfig(
        api_key=os.environ["DAYTONA_API_KEY"],
        api_url="https://app.daytona.io/api",
        otel_enabled=False,
    )
)
s = c.get(s.id)
r = settle(e)
call(payload)
assert s.fs.download_file(root + "/work/once.txt") == b"x"
record("reconnect_without_reexecution", r)
e = str(uuid4())
call({"operation": "submit", "execution": e, "code": "import time\ntime.sleep(60)"})
h = call({"operation": "health"})
command("kill -KILL " + str(h["supervisor_pid"]))
restarted = json.loads(command(prefix + " ensure"))
assert restarted["fenced"]
r = call({"operation": "status", "execution": e})
assert r["state"] == "unknown"
record("supervisor_crash_fenced", r)
reset = call({"operation": "reset"})
assert reset["generation"] != h["generation"]
record("reset_after_crash", reset)
r = execute(
    "assert 'df' not in globals()\nimport pandas as pd\ndf=pd.read_csv('data/probe.csv',skip_blank_lines=False)\nassert len(df)==3"
)
recipe = "import pandas as pd\nfrom pathlib import Path\nimport sys\ndf=pd.read_csv(sys.argv[1],skip_blank_lines=False)\ndf['value']=df['value'].fillna(df['value'].median())\ndf.to_json(sys.argv[2],orient='records')\n"
r = execute(
    "from pathlib import Path\nPath('recipe.py').write_text("
    + repr(recipe)
    + ")\nimport subprocess,sys\nsubprocess.run([sys.executable,'recipe.py','data/probe.csv','out.json'],check=True)"
)
before = s.fs.download_file(root + "/work/out.json")
assert json.loads(before) == [{"value": 10.0}, {"value": 20.0}, {"value": 30.0}]
call({"operation": "reset"})
r = execute(
    "import subprocess,sys\nsubprocess.run([sys.executable,'recipe.py','data/probe.csv','replayed.json'],check=True)"
)
after = s.fs.download_file(root + "/work/replayed.json")
assert before == after
record("clean_replay", {"sha256": hashlib.sha256(after).hexdigest(), "rows": 3})
e = str(uuid4())
call({"operation": "submit", "execution": e, "code": "import time\ntime.sleep(60)"})
call({"operation": "interrupt", "execution": e})
r = settle(e)
assert r["error"] == "KeyboardInterrupt"
record("interrupt", r)
execute("assert 6*7==42")
call({"operation": "close"})
report["passed"] = True
out.write_text(json.dumps(report, indent=2))
print("PASS", flush=True)
