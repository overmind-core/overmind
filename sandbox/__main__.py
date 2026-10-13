import argparse
import os
import sys

import uvicorn

from sandbox import execute


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sandbox")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not os.environ.get("SANDBOX_TOKEN"):
        sys.exit("SANDBOX_TOKEN is required.")
    if os.geteuid() != 0 and os.environ.get("SANDBOX_ALLOW_SHARED_UID") != "1":
        sys.exit(
            "The sandbox runs as root to start each run as its own user. "
            "Set SANDBOX_ALLOW_SHARED_UID=1 only for tests and local workers."
        )
    if os.geteuid() == 0 and not execute.can_trace():
        sys.exit("The sandbox needs CAP_SYS_PTRACE to see the files a run holds open.")
    uvicorn.run("sandbox.server:app", host=args.host, port=args.port, workers=1)


main()
