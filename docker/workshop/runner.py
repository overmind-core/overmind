import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

request = Path("/work/request.json")
while not request.exists():
    time.sleep(0.1)
spec = json.loads(request.read_text())
resource.setrlimit(resource.RLIMIT_FSIZE, (spec["max_file_bytes"], spec["max_file_bytes"]))
resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
os.chdir("/work/package")
with open("/work/stdout.log", "wb") as stdout, open("/work/stderr.log", "wb") as stderr:
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                spec["entrypoint"],
                "/work/input.jsonl",
                "/work/output.jsonl",
                "/work/parameters.json",
            ],
            stdout=stdout,
            stderr=stderr,
            timeout=spec["seconds"],
            check=False,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONHASHSEED": "0"},
        )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        exit_code = 124
sys.exit(exit_code if exit_code >= 0 else 128 - exit_code)
