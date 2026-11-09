import argparse
import json
import os
import shlex
import time
from pathlib import Path

from daytona import Daytona, DaytonaConfig
from dotenv import load_dotenv

CODE = """
import json, pathlib, shutil
root=pathlib.Path('/sys/fs/cgroup')
memory={key:(root/key).read_text() for key in ('memory.current','memory.peak','memory.events','memory.stat') if (root/key).exists()}
disk=shutil.disk_usage('/home/daytona')
print(json.dumps({'memory':memory,'disk':dict(zip(('total','used','free'),disk))}))
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--output", type=Path, required=True)
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    while True:
        record = {"epoch": time.time(), "sandbox": args.sandbox}
        try:
            result = sandbox.process.exec("python -c " + shlex.quote(CODE), timeout=20)
            if result.exit_code:
                raise RuntimeError(result.result)
            record.update(json.loads(result.result))
        except Exception as exc:
            record["error"] = str(exc)
        with args.output.open("a") as handle:
            handle.write(json.dumps(record) + "\n")
        time.sleep(20)
