import hashlib
import json
import os
import shlex
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from overbae.services.datasets import paths
from overbae.services.workshop_agent import lifecycle, transfer, workspace


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def baseline(sandbox, source, destination):
    started = time.monotonic()
    digest = checksum(source)
    sandbox.process.exec("mkdir -p " + shlex.quote(str(Path(destination).parent)), timeout=30)
    sandbox.fs.upload_file(str(source), destination)
    with tempfile.TemporaryDirectory() as directory:
        downloaded = Path(directory) / "verified"
        sandbox.fs.download_file(destination, str(downloaded))
        assert checksum(downloaded) == digest
    return {
        "seconds": time.monotonic() - started,
        "uploaded_bytes": source.stat().st_size,
        "downloaded_bytes": source.stat().st_size,
        "sha256": digest,
    }


for case in json.loads(os.environ["TRANSFER_CASES"]):
    task = lifecycle.get(case["task"])
    assert task.state == "completed"
    preparation = task.actions.get(sequence=0).result["input"]
    sandbox = workspace.attach(task, reconcile=True)
    root = "/home/daytona/overmind/.transfer-benchmark-" + uuid4().hex
    files = [("parquet", paths.cell_path(task.dataset_id, preparation["cell"]))]
    files += [
        ("original-" + str(i), paths.source_path(task.dataset_id, original["source"]))
        for i, original in enumerate(preparation["originals"])
    ]
    try:
        for index, (name, source) in enumerate(files):
            output = {"case": case["case"], "file": name, "bytes": source.stat().st_size}
            order = ["before", "after"] if (case["order"] + index) % 2 == 0 else ["after", "before"]
            for version in order:
                destination = root + "/" + version + "/" + name
                if version == "before":
                    output[version] = baseline(sandbox, source, destination)
                else:
                    receipt = transfer.copy_verified(sandbox, source, destination)
                    output[version] = {**receipt.pop("transfer"), **receipt, "downloaded_bytes": 0}
                    assert output[version]["sha256"] == checksum(source)
                    reused = transfer.copy_verified(sandbox, source, destination)
                    assert reused["transfer"]["reused"]
                    assert reused["transfer"]["uploaded_bytes"] == 0
                    output["retry"] = reused["transfer"]
                print(json.dumps({"event": "phase", **output, "finished": version}), flush=True)
            assert output["before"]["sha256"] == output["after"]["sha256"]
            print(json.dumps({"event": "comparison", **output}), flush=True)
    finally:
        response = sandbox.process.exec("rm -rf -- " + shlex.quote(root), timeout=30)
        assert response.exit_code == 0
