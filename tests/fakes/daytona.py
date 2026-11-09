import base64
import gzip
import hashlib
import io
import json
import shlex
import subprocess
import sys
import tempfile
import zipfile
from contextlib import nullcontext
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlparse

import urllib3


class DaytonaAPI:
    def __init__(self, sandbox_id, runtime):
        self.sandbox_id = sandbox_id
        self.runtime = runtime
        self.labels = {}
        self.files = {
            "/opt/overmind-runtime/lib/workshop_runtime/GUIDE.md": b"Use local Python and data files."
        }
        self.corrupt_download = False
        self.corrupt_file = None
        self.corrupt_upload = False
        self.uploads = []
        self.executions = {}
        self.session_requests = []
        self.agent_requests = []
        self.cache_directory = None
        self.capacity_bytes = None

    def handle(self, call):
        path = call.path
        if path == f"/api/sandbox/{self.sandbox_id}":
            body = {
                "id": self.sandbox_id,
                "organizationId": "test",
                "name": "workshop",
                "user": "daytona",
                "env": {},
                "labels": self.labels,
                "public": False,
                "networkBlockAll": True,
                "kvm": False,
                "target": "us",
                "cpu": 4,
                "gpu": 0,
                "memory": 8,
                "disk": 10,
                "state": "started",
                "autoStopInterval": 0,
                "autoPauseInterval": 0,
                "autoDeleteInterval": -1,
                "toolboxProxyUrl": "https://app.daytona.io/toolbox",
            }
        elif path.endswith("/process/execute"):
            command = call.json["command"]
            if "inspect_agent=mailbox.inspect, request_kernel=session.request" in command:
                arguments = shlex.split(command)
                request = json.loads(arguments[-1])
                if self.cache_directory is None:
                    self.cache_directory = tempfile.TemporaryDirectory()
                with nullcontext(self.cache_directory.name) as directory:
                    root = Path(directory)
                    for name, content in self.files.items():
                        local = root / name.lstrip("/")
                        local.parent.mkdir(parents=True, exist_ok=True)
                        local.write_bytes(content)
                    for name in ("root", "temporary", "destination"):
                        if name in request:
                            request[name] = str(root / request[name].lstrip("/"))
                    (root / "tmp").mkdir(exist_ok=True)
                    executed = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            arguments[-2],
                            json.dumps(request),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.files = {
                        "/" + str(local.relative_to(root)): local.read_bytes()
                        for local in root.rglob("*")
                        if local.is_file()
                    }
                    body = {
                        "exitCode": executed.returncode,
                        "result": executed.stdout + executed.stderr,
                    }
                    if request["operation"] == "catalogue" and self.capacity_bytes is not None:
                        inventory = json.loads(body["result"])
                        inventory["free"] = self.capacity_bytes - sum(map(len, self.files.values()))
                        inventory["reserve"] = self.capacity_bytes // 10
                        body["result"] = json.dumps(inventory)
            elif ".overmind-install-" in command:
                arguments = shlex.split(command)
                request = json.loads(arguments[-1])
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    for name in (request["destination"], request["archive"]):
                        if name in self.files:
                            local = root / name.lstrip("/")
                            local.parent.mkdir(parents=True, exist_ok=True)
                            local.write_bytes(self.files[name])
                    for name in ("destination", "archive"):
                        request[name] = str(root / request[name].lstrip("/"))
                    executed = subprocess.run(
                        [sys.executable, "-c", arguments[-2], json.dumps(request)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    for local in root.rglob("*"):
                        if local.is_file():
                            self.files["/" + str(local.relative_to(root))] = local.read_bytes()
                    body = {
                        "exitCode": executed.returncode,
                        "result": executed.stdout + executed.stderr,
                    }
            elif "workshop-transfer-" in command:
                names = json.loads(base64.b64decode(shlex.split(command)[-1]))
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                    for index, name in enumerate(names):
                        archive.writestr(
                            str(index),
                            b"corrupted" if name == self.corrupt_file else self.files[name],
                        )
                destination = "/tmp/workshop-transfer-fixture.zip"
                payload = output.getvalue()
                self.files[destination] = payload
                body = {
                    "exitCode": 0,
                    "result": json.dumps(
                        {"path": destination, "sha256": hashlib.sha256(payload).hexdigest()}
                    ),
                }
            elif command.startswith("chmod 444 "):
                assert shlex.split(command)[2] in self.files
                body = {"exitCode": 0, "result": ""}
            elif command.startswith("mkdir -p "):
                body = {"exitCode": 0, "result": ""}
            elif command == "/opt/overmind-runtime/venv/bin/python -m workshop_runtime.environment":
                body = {"exitCode": 0, "result": json.dumps(self.runtime)}
            elif "-m workshop_runtime.session" in command and command.endswith(" ensure"):
                body = {
                    "exitCode": 0,
                    "result": json.dumps(
                        {
                            "alive": True,
                            "fenced": False,
                            "active": None,
                            "generation": "test-kernel",
                        }
                    ),
                }
            elif "-m workshop_runtime.session" in command and " request --payload " in command:
                payload = json.loads(base64.b64decode(shlex.split(command)[-1]))
                self.session_requests.append(payload)
                if payload["operation"] != "status":
                    raise AssertionError(
                        "Recovery must inspect the retained execution, not resubmit code"
                    )
                body = {"exitCode": 0, "result": json.dumps(self.executions[payload["execution"]])}
            elif "-m workshop_runtime.mailbox" in command:
                payload = json.loads(base64.b64decode(shlex.split(command)[-1]))
                self.agent_requests.append(payload)
                body = {"exitCode": 0, "result": json.dumps({"alive": True, "pending": []})}
            else:
                raise AssertionError(f"Unexpected remote execution: {command}")
        elif path.endswith("/files") and call.method == "DELETE":
            self.files.pop(call.params["path"], None)
            body = {}
        elif path.endswith("/files/bulk-upload"):
            envelope = (
                b"Content-Type: "
                + call.headers["content-type"].encode()
                + b"\r\nMIME-Version: 1.0\r\n\r\n"
                + call.body
            )
            message = BytesParser(policy=policy.default).parsebytes(envelope)
            fields = {
                part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                for part in message.iter_parts()
            }
            for index in range(len(fields) // 2):
                destination = fields[f"files[{index}].path"].decode()
                content = fields[f"files[{index}].file"]
                if self.corrupt_upload and ".upload-" in destination:
                    raw = gzip.decompress(content)
                    content = {
                        "checksum": gzip.compress(b"x" * len(raw)),
                        "oversize": gzip.compress(raw + b"extra"),
                        "truncated": content[:-10],
                    }[self.corrupt_upload]
                self.files[destination] = content
                self.uploads.append(destination)
            body = {}
        elif path.endswith("/files/bulk-download"):
            parts = []
            for filename in call.json["paths"]:
                content = self.files[filename]
                if (
                    self.corrupt_download and filename.endswith(".parquet")
                ) or filename == self.corrupt_file:
                    content = b"corrupted"
                parts.append(
                    (
                        '--daytona\r\nContent-Disposition: form-data; name="file"; filename="'
                        + filename
                        + '"\r\nContent-Type: application/octet-stream\r\n\r\n'
                    ).encode()
                    + content
                    + b"\r\n"
                )
            return (
                200,
                {"content-type": "multipart/form-data; boundary=daytona"},
                b"".join(parts) + b"--daytona--\r\n",
            )
        else:
            raise AssertionError(f"Unexpected Daytona request: {call.method} {path}")
        return 200, {"content-type": "application/json"}, json.dumps(body).encode()

    def connect(self, monkeypatch, scripted):
        from fakes.http import Call

        scripted("https://app.daytona.io").then(self.handle)
        original = urllib3.PoolManager.request

        def request(manager, method, url, **kwargs):
            if urlparse(url).netloc != "app.daytona.io":
                return original(manager, method, url, **kwargs)
            call = Call(
                method,
                url,
                {k.lower(): v for k, v in kwargs.get("headers", {}).items()},
                kwargs.get("body"),
            )
            status, headers, content = self.handle(call)
            return urllib3.HTTPResponse(
                body=io.BytesIO(content), status=status, headers=headers, preload_content=False
            )

        monkeypatch.setattr(urllib3.PoolManager, "request", request)


class DaytonaProvisioningAPI(DaytonaAPI):
    def __init__(self, runtime):
        super().__init__("new-workspace", runtime)
        self.created = False
        self.creates = []
        self.lose_acknowledgement = False
        self.reject = False
        self.visible = True
        self.on_create = None

    def handle(self, call):
        if call.path == "/api/sandbox":
            if call.method == "POST":
                self.creates.append(call.json)
                if self.reject:
                    return (
                        400,
                        {"content-type": "application/json"},
                        b'{"message":"Quota exceeded"}',
                    )
                self.created = True
                self.labels = call.json["labels"]
                if self.on_create:
                    self.on_create()
                if self.lose_acknowledgement:
                    raise TimeoutError("Acknowledgement lost")
            from fakes.http import Call

            _, _, body = super().handle(
                Call("GET", "https://app.daytona.io/api/sandbox/new-workspace", {}, None)
            )
            if call.method == "GET":
                body = json.dumps(
                    {
                        "items": [json.loads(body)] if self.created and self.visible else [],
                        "nextCursor": None,
                    }
                ).encode()
            return 200, {"content-type": "application/json"}, body
        return super().handle(call)
