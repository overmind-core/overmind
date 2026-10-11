import http.client
import io
import json
import logging
import socket
import struct
import tarfile
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import close_old_connections, connection
from django.utils import timezone

from overbae.models import DatasetPipelineRun, DatasetPipelineRunner
from overbae.services.datasets import heartbeat as worker_heartbeat
from overbae.services.datasets import pipeline_bindings, store, workbench

logger = logging.getLogger(__name__)


class RuntimeUnavailableError(ValueError):
    pass


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(settings.WORKSHOP_DOCKER_SOCKET)


class DockerEngine:
    def request(
        self,
        method,
        path,
        *,
        body=None,
        limit=1024 * 1024,
        missing=False,
    ):
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body).encode()
        connection = DockerConnection("localhost", timeout=30)
        try:
            connection.request(method, "/v1.45" + path, body=body, headers=headers)
            response = connection.getresponse()
            if missing and response.status == 404:
                return None
            if not 200 <= response.status < 300:
                failure = response.read(4096)
                try:
                    detail = str(json.loads(failure).get("message", ""))[:500]
                except (ValueError, AttributeError):
                    detail = "No structured Docker error was returned."
                raise RuntimeUnavailableError(
                    f"The isolated runtime returned HTTP {response.status}: {detail}"
                )
            data = response.read(limit + 1)
            if len(data) > limit:
                raise RuntimeUnavailableError("The runtime response exceeds its size limit.")
            return json.loads(data) if data else {}
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
            raise RuntimeUnavailableError(
                "The isolated Docker runtime is unavailable. No host execution fallback was used."
            ) from exc
        finally:
            connection.close()

    def inspect(self, identity):
        return self.request("GET", f"/containers/{quote(identity, safe='')}/json", missing=True)

    def transfer(self, identity, command, *, incoming=None, destination=None, limit=65536):
        execution = self.request(
            "POST",
            f"/containers/{identity}/exec",
            body={
                "Cmd": command,
                "User": "65532:65532",
                "AttachStdin": incoming is not None,
                "AttachStdout": True,
                "AttachStderr": True,
            },
        )["Id"]
        connection = DockerConnection("localhost", timeout=30)
        try:
            connection.request(
                "POST",
                f"/v1.45/exec/{execution}/start",
                body=json.dumps({"Detach": False, "Tty": False}),
                headers={
                    "Content-Type": "application/json",
                    "Connection": "Upgrade",
                    "Upgrade": "tcp",
                },
            )
            transport = connection.sock
            response = connection.getresponse()
            if response.status != 101:
                raise RuntimeUnavailableError(
                    f"Docker did not establish a transfer stream (HTTP {response.status})."
                )
            if incoming is not None:
                incoming.seek(0)
                while data := incoming.read(1024 * 1024):
                    transport.sendall(data)
                transport.shutdown(socket.SHUT_WR)
            written, errors = 0, bytearray()
            # Docker's hijacked stream frames stdout/stderr with an eight-byte header.
            while header := response.fp.read(8):
                if len(header) != 8 or header[0] not in (1, 2):
                    raise RuntimeUnavailableError("Docker returned an invalid transfer frame.")
                remaining = struct.unpack(">I", header[4:])[0]
                while remaining:
                    data = response.fp.read(min(remaining, 65536))
                    if not data:
                        raise RuntimeUnavailableError("Docker's transfer stream ended early.")
                    remaining -= len(data)
                    if header[0] == 1:
                        written += len(data)
                        if written > limit:
                            raise RuntimeUnavailableError(
                                "The runtime artifact exceeds its declared size limit."
                            )
                        if destination:
                            destination.write(data)
                    elif len(errors) < 4096:
                        errors.extend(data[: 4096 - len(errors)])
            state = self.request("GET", f"/exec/{execution}/json")
            if state["Running"]:
                raise RuntimeUnavailableError(
                    "Docker has not confirmed the transfer outcome; it was not replayed."
                )
            return state["ExitCode"]
        except (OSError, http.client.HTTPException) as exc:
            raise RuntimeUnavailableError(
                "Docker transfer was interrupted; it was not replayed."
            ) from exc
        finally:
            connection.close()

    def upload(self, identity, incoming):
        code = self.transfer(
            identity, ["tar", "-xf", "-", "-C", "/work", "--no-same-owner"], incoming=incoming
        )
        if code != 0:
            raise RuntimeUnavailableError(f"Container input transfer failed with exit code {code}.")

    def artifact(self, identity, name, directory, limit, *, truncate=False):
        destination = directory / name
        reader = (
            "import pathlib,stat,sys; p=pathlib.Path(sys.argv[1]); "
            "sys.exit(44) if not p.exists() else None; "
            "sys.exit(45) if not stat.S_ISREG(p.lstat().st_mode) else None; "
            "bound=int(sys.argv[2]); "
            "sys.exit(46) if p.stat().st_size>bound and sys.argv[3]!='truncate' else None; "
            "f=p.open('rb'); "
            "exec('while bound:\\n data=f.read(min(bound,65536))\\n if not data: break\\n sys.stdout.buffer.write(data)\\n bound-=len(data)')"
        )
        with destination.open("wb") as output:
            code = self.transfer(
                identity,
                [
                    "python",
                    "-I",
                    "-c",
                    reader,
                    "/work/" + name,
                    str(limit),
                    "truncate" if truncate else "complete",
                ],
                destination=output,
                limit=limit,
            )
        if code == 44:
            return None
        if code != 0:
            raise RuntimeUnavailableError(
                f"Container artifact transfer failed with exit code {code}."
            )
        return destination


def heartbeat(status="ready"):
    DatasetPipelineRunner.objects.update_or_create(
        pk="local",
        defaults={
            "images": settings.WORKSHOP_RUNTIME_IMAGES,
            "heartbeat_at": timezone.now(),
            "status": status,
        },
    )


@contextmanager
def controller():
    # A dedicated session keeps ownership across close_old_connections and releases it on process death.
    owner = connection.copy()
    lock = int.from_bytes(b"workshop", "big")
    try:
        with owner.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock])
            if not cursor.fetchone()[0]:
                raise RuntimeUnavailableError("A Workshop controller is already running.")
        for run_id in DatasetPipelineRun.objects.filter(
            state="running", pipeline__isnull=False
        ).values_list("pk", flat=True):
            workbench.fail(
                run_id,
                "The execution controller stopped. No output was published. "
                "Inspect the retained diagnostics and submit a new request key to retry.",
            )
        yield owner
    finally:
        owner.close()


def execute_script(run, index, source, directory, step, files, progress):
    engine = DockerEngine()
    receipt = run.result["steps"][index]
    started = checkpoint = time.monotonic()
    stage = None
    timings = {}

    def phase(next_stage, *, notify=True):
        nonlocal checkpoint, stage
        now = time.monotonic()
        if stage is not None:
            timings[stage] = round(timings.get(stage, 0) + now - checkpoint, 4)
        checkpoint, stage = now, next_stage
        receipt["runtime"] = {
            "stage": stage,
            "stage_seconds": dict(timings),
            "seconds": round(now - started, 4),
            "observed_at": timezone.now().isoformat(),
        }
        if notify and not progress():
            raise RuntimeUnavailableError(
                "Publication was cancelled; the isolated container is stopping."
            )

    phase("container_setup")
    manifest = run.pipeline.package.manifest
    runtime = manifest["runtime"]
    if runtime not in settings.WORKSHOP_RUNTIME_IMAGES:
        raise RuntimeUnavailableError(
            "The pinned runtime is no longer approved on this deployment."
        )
    image = engine.request("GET", f"/images/{runtime}/json", missing=True)
    if not image or image.get("Id") != runtime:
        raise RuntimeUnavailableError(
            "The pinned runtime image is not installed. No image was pulled automatically."
        )
    limits = {
        "seconds": 300,
        "memory_mb": 512,
        "scratch_mb": 512,
        "cpus": 1,
        **manifest.get("limits", {}),
    }
    name = f"overmind-workshop-{run.pk}-{run.attempt}-{index}"
    if batch := receipt.get("batches", {}).get("current"):
        name += f"-batch-{batch}"
    existing = engine.inspect(name)
    if existing:
        raise RuntimeUnavailableError(
            "An earlier container owns this step. Inspect its receipt; it was not replayed."
        )
    scratch = limits["scratch_mb"] * 1024 * 1024
    configuration = {
        "Image": runtime,
        "User": "65532:65532",
        "WorkingDir": "/work",
        "Entrypoint": ["python", "-I", "-c", "import time; time.sleep(86400)"],
        "Cmd": [],
        "Env": ["PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1"],
        "Labels": {
            "overmind.workshop.run": str(run.pk),
            "overmind.workshop.attempt": str(run.attempt),
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": 64,
            "Memory": limits["memory_mb"] * 1024 * 1024,
            "MemorySwap": limits["memory_mb"] * 1024 * 1024,
            "NanoCpus": limits["cpus"] * 1000000000,
            "Tmpfs": {
                "/work": f"rw,nosuid,nodev,size={scratch},mode=1777",
                "/tmp": "rw,nosuid,nodev,noexec,size=16777216,mode=1777",
            },
            "LogConfig": {
                "Type": "local",
                "Config": {"max-size": "1m", "max-file": "1", "compress": "false"},
            },
        },
    }
    # Persist deterministic ownership before create; a lost acknowledgement never authorizes another container.
    owned = DatasetPipelineRun.objects.filter(
        pk=run.pk, state="running", attempt=run.attempt
    ).update(container_id=name)
    if not owned:
        raise RuntimeUnavailableError("Publication was cancelled before container creation.")
    identity = None
    output_complete = False
    cleanup_confirmed = False
    try:
        created = engine.request("POST", f"/containers/create?name={name}", body=configuration)
        identity = created["Id"]
        engine.request("POST", f"/containers/{identity}/start")
        phase("input_transfer")
        with tempfile.TemporaryFile() as archive:
            with tarfile.open(fileobj=archive, mode="w") as bundle:

                def add(name, data):
                    entry = tarfile.TarInfo(name)
                    entry.size, entry.mode, entry.uid, entry.gid = len(data), 0o600, 65532, 65532
                    bundle.addfile(entry, io.BytesIO(data))

                for filename, content in files.items():
                    add("package/" + filename, content.encode())
                add(
                    "parameters.json",
                    json.dumps(run.specification["parameters"], allow_nan=False).encode(),
                )
                with tempfile.TemporaryFile() as incoming:
                    for row in store.iter_rows(source):
                        row.pop("_overmind_parent_rows", None)
                        incoming.write(
                            (
                                json.dumps(
                                    row, ensure_ascii=False, allow_nan=False, cls=DjangoJSONEncoder
                                )
                                + "\n"
                            ).encode()
                        )
                        if incoming.tell() > scratch // 2:
                            raise RuntimeUnavailableError(
                                "The input exceeds half the declared scratch space. Increase the "
                                "package limit, or declare batch_rows for a row-independent step."
                            )
                    entry = tarfile.TarInfo("input.jsonl")
                    entry.size, entry.mode, entry.uid, entry.gid = (
                        incoming.tell(),
                        0o600,
                        65532,
                        65532,
                    )
                    incoming.seek(0)
                    bundle.addfile(entry, incoming)
            archive.seek(0)
            engine.upload(identity, archive)
        with tempfile.TemporaryFile() as archive:
            with tarfile.open(fileobj=archive, mode="w") as bundle:
                data = json.dumps(
                    {
                        "entrypoint": step["entrypoint"],
                        "seconds": limits["seconds"],
                        "max_file_bytes": scratch // 2,
                    }
                ).encode()
                entry = tarfile.TarInfo("request.json")
                entry.size, entry.mode, entry.uid, entry.gid = len(data), 0o600, 65532, 65532
                bundle.addfile(entry, io.BytesIO(data))
            archive.seek(0)
            engine.upload(identity, archive)
        phase("script_execution")
        execution = engine.request(
            "POST",
            f"/containers/{identity}/exec",
            body={
                "Cmd": ["python", "-I", "/runner.py"],
                "User": "65532:65532",
                "AttachStdin": False,
                "AttachStdout": False,
                "AttachStderr": False,
            },
        )["Id"]
        run.result["steps"][index]["provider_execution"] = execution
        run.result["steps"][index]["container"] = name
        if not progress():
            raise RuntimeUnavailableError("Publication was cancelled before script execution.")
        engine.request("POST", f"/exec/{execution}/start", body={"Detach": True, "Tty": False})
        deadline = time.monotonic() + limits["seconds"] + 10
        poll_interval = 0.05
        with tempfile.TemporaryDirectory(dir=directory, prefix=f"script-{index}-") as scratch_dir:
            scratch_path = Path(scratch_dir)
            while time.monotonic() < deadline:
                heartbeat("executing")
                if not progress():
                    raise RuntimeUnavailableError(
                        "Publication was cancelled; the isolated container is stopping."
                    )
                execution_state = engine.request("GET", f"/exec/{execution}/json")
                if not execution_state["Running"]:
                    phase("artifact_transfer")
                    for channel in ("stdout.log", "stderr.log"):
                        log = engine.artifact(identity, channel, scratch_path, 65536, truncate=True)
                        if log:
                            with log.open("rb") as content:
                                (directory / f"{index}-{channel}").write_bytes(content.read(65536))
                    run.result["steps"][index]["exit_code"] = execution_state.get("ExitCode")
                    progress()
                    if execution_state.get("ExitCode") != 0:
                        raise RuntimeUnavailableError(
                            "The script failed or exceeded its time limit. Inspect the step's retained diagnostics."
                        )
                    output = engine.artifact(identity, "output.jsonl", scratch_path, scratch // 2)
                    if output is None:
                        raise RuntimeUnavailableError("The script did not produce output.jsonl.")
                    phase("output_reading")
                    with output.open("rb") as rows:
                        for line in iter(lambda: rows.readline(4 * 1024 * 1024 + 1), b""):
                            if len(line) > 4 * 1024 * 1024:
                                raise RuntimeUnavailableError("An output row exceeds 4 MiB.")
                            row = json.loads(line)
                            if not isinstance(row, dict):
                                raise RuntimeUnavailableError(
                                    "Every output line must contain a row object."
                                )
                            workbench.digest(row)
                            if len(row) > 200:
                                raise RuntimeUnavailableError(
                                    "A script output row exceeds 200 columns."
                                )
                            yield row
                    output_complete = True
                    return
                state = engine.inspect(identity)
                if not state or not state["State"]["Running"]:
                    raise RuntimeUnavailableError(
                        "The isolated container stopped before producing a result."
                    )
                time.sleep(poll_interval)
                poll_interval = min(0.5, poll_interval * 2)
            raise RuntimeUnavailableError("The isolated script exceeded its deadline.")
    finally:
        phase("cleanup", notify=False)
        known = identity or name
        try:
            if engine.inspect(known):
                engine.request("DELETE", f"/containers/{known}?force=true")
            DatasetPipelineRun.objects.filter(pk=run.pk, attempt=run.attempt).update(
                container_id=""
            )
            cleanup_confirmed = True
        except RuntimeUnavailableError:
            logger.exception("Container termination is unconfirmed for run %s", run.pk)
        phase("completed" if output_complete else "stopped", notify=False)
        receipt["runtime"]["cleanup_confirmed"] = cleanup_confirmed
        progress()


def tick():
    close_old_connections()
    engine = DockerEngine()
    try:
        engine.request("GET", "/version")
        heartbeat()
    except RuntimeUnavailableError:
        heartbeat("unavailable")
        return
    for run in DatasetPipelineRun.objects.filter(
        state__in=["failed", "cancelled"], container_id__startswith="overmind-workshop-"
    )[:20]:
        try:
            if engine.inspect(run.container_id):
                engine.request("DELETE", f"/containers/{run.container_id}?force=true")
            DatasetPipelineRun.objects.filter(pk=run.pk, container_id=run.container_id).update(
                container_id=""
            )
        except RuntimeUnavailableError:
            logger.exception("Container termination remains unconfirmed for run %s", run.pk)
    workbench.expire_runs()
    pipeline_bindings.tick()
    for run in DatasetPipelineRun.objects.filter(state="queued", pipeline__isnull=False).order_by(
        "created_at"
    )[:1]:
        heartbeat("executing")
        beat = worker_heartbeat.start(lambda: heartbeat("executing"))
        try:
            workbench.execute(run.pk, script_executor=execute_script)
        finally:
            beat.set()
            heartbeat()
