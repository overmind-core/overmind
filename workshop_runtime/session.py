import argparse
import base64
import fcntl
import hashlib
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from queue import Empty
from uuid import UUID, uuid4

from jupyter_client import KernelManager

from workshop_runtime.mailbox import save

MAX_OUTPUT = 16 * 1024 * 1024
KERNEL_UID = 10001
CONTEXT_FILES = (
    "skills",
    "briefing.json",
    "input.json",
    "native.json",
    "presentation.json",
    "task.json",
    "context.json",
    "workflow.json",
    "runtime.json",
    "contract.json",
    "consumers.json",
    "GUIDE.md",
    "platform.json",
    "taxonomy.json",
    "research.json",
)


def seal(root):
    root = Path(root).resolve()
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    context = root / "context"
    context.mkdir(exist_ok=True)
    for name in ("data", *CONTEXT_FILES):
        source = work / name
        target = root / "inputs" if name == "data" else context / name
        if source.exists() and not source.is_symlink():
            source.rename(target)
        if target.exists() and not source.exists() and not source.is_symlink():
            source.symlink_to(target, target_is_directory=name == "data")
    for directory in (root / "inputs", context):
        if directory.exists():
            for path in [*directory.rglob("*"), directory]:
                if path.is_symlink():
                    raise ValueError("Staged sources must not contain symlinks")
                path.chmod(0o555 if path.is_dir() else 0o444)


def file_request(root, payload):
    root = Path(root).resolve()
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(KERNEL_UID)
        os.setuid(KERNEL_UID)
    path = (root / "work" / payload["path"]).resolve()
    if not any(path.is_relative_to(root / area) for area in ("work", "inputs", "context")):
        raise ValueError("Path is outside the task data and working files")
    if payload["operation"] == "write":
        if not path.is_relative_to(root / "work"):
            raise ValueError("Source and context files are read-only")
        data = payload["text"].encode()
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("File exceeds the transfer limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {
            "path": payload["path"],
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    with path.open("rb") as stream:
        data = stream.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("File exceeds the transfer limit")
    return {"base64": base64.b64encode(data).decode()}


def kernel_environment(work):
    return {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "HOME": str(work),
        "IPYTHONDIR": str(work / ".ipython"),
        "JUPYTER_RUNTIME_DIR": str(work / ".jupyter"),
        "MPLBACKEND": "Agg",
        "TIKTOKEN_CACHE_DIR": "/opt/overmind-runtime/tokenizers",
        "PIP_NO_INDEX": "1",
        "HF_HUB_OFFLINE": "1",
        "PYTHONNOUSERSITE": "1",
        "LANG": "C.UTF-8",
    }


class WorkspaceKernelManager(KernelManager):
    def write_connection_file(self, **kwargs):
        super().write_connection_file(**kwargs)
        # The provisioner rewrites this file immediately before launching the lower-privilege kernel.
        if os.geteuid() == 0:
            os.chown(self.connection_file, 0, KERNEL_UID)
            Path(self.connection_file).chmod(0o640)


class Session:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.work = self.root / "work"
        self.private = self.root / ".session"
        self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.work.mkdir(exist_ok=True)
        self.runs = self.private / "runs"
        self.runs.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.manager = None
        self.client = None
        self.active = None
        self.meta_path = self.private / "kernel.json"
        self.meta = json.loads(self.meta_path.read_text()) if self.meta_path.exists() else {}
        # A new supervisor cannot infer the state of an orphaned Python namespace.
        self.fenced = bool(self.meta)
        for path in self.runs.glob("*.json"):
            record = json.loads(path.read_text())
            if record["state"] in {"accepted", "running"}:
                record.update(state="unknown", reason="supervisor_restarted")
                save(path, record)
        if os.geteuid() == 0:
            os.chown(self.work, KERNEL_UID, KERNEL_UID)
        if not self.fenced:
            self.start_kernel()

    def start_kernel(self):
        generation = str(uuid4())
        directory = self.root / "kernel"
        directory.mkdir(exist_ok=True, mode=0o755)
        connection = directory / f"{generation}.json"
        manager = WorkspaceKernelManager(connection_file=str(connection))
        manager.kernel_spec.argv = [
            sys.executable,
            "-m",
            "ipykernel_launcher",
            "-f",
            "{connection_file}",
        ]
        manager.write_connection_file()
        options = (
            {"user": KERNEL_UID, "group": KERNEL_UID, "extra_groups": []}
            if os.geteuid() == 0
            else {}
        )
        with (self.private / f"kernel-{generation}.log").open("a") as log:
            manager.start_kernel(
                cwd=str(self.work),
                env=kernel_environment(self.work),
                stdout=log,
                stderr=log,
                start_new_session=True,
                **options,
            )
        self.manager = manager
        self.meta = {
            "generation": generation,
            "pid": manager.provisioner.pid,
            "connection_file": str(connection),
        }
        save(self.meta_path, self.meta)
        client = manager.blocking_client()
        client.start_channels()
        client.wait_for_ready(timeout=30)
        self.client = client
        self.fenced = False

    def health(self):
        return {
            "supervisor_pid": os.getpid(),
            "generation": self.meta.get("generation"),
            "pid": self.meta.get("pid"),
            "active": self.active,
            "fenced": self.fenced,
            "alive": bool(self.manager and self.manager.is_alive()),
        }

    def record(self, execution):
        return self.runs / f"{UUID(execution)}.json"

    def status(self, execution, offset=0):
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid output offset")
        record = json.loads(self.record(execution).read_text())
        record.pop("code", None)
        output = self.runs / f"{execution}.jsonl"
        text = output.read_text() if output.exists() else ""
        record.update(
            output=text[offset : offset + 32000],
            offset=offset,
            next_offset=offset + 32000 if len(text) > offset + 32000 else None,
            total_output_chars=len(text),
        )
        return record

    def submit(self, execution, code, timeout=120):
        execution = str(UUID(execution))
        if not isinstance(code, str) or not 1 <= len(code) <= 64000:
            raise ValueError("Code must contain 1–64000 characters")
        if type(timeout) is not int or not 1 <= timeout <= 900:
            raise ValueError("Execution timeout must be 1–900 seconds")
        digest = hashlib.sha256(json.dumps([code, timeout]).encode()).hexdigest()
        with self.lock:
            path = self.record(execution)
            if path.exists():
                record = json.loads(path.read_text())
                if record["request_sha256"] != digest:
                    raise ValueError("Execution identity conflicts with a different request")
                return self.status(execution)
            if self.fenced or not self.manager.is_alive():
                raise ValueError("Kernel state is unknown or lost; reset before executing")
            if self.active:
                raise ValueError("Another execution is active; inspect its receipt")
            record = {
                "execution": execution,
                "request_sha256": digest,
                "code": code,
                "generation": self.meta["generation"],
                "state": "accepted",
                "timeout": timeout,
                "accepted_at": time.time(),
            }
            save(path, record)
            self.active = execution
            threading.Thread(target=self.execute, args=(record,), daemon=True).start()
            return self.status(execution)

    def execute(self, record):
        path = self.record(record["execution"])
        try:
            record.update(state="running", started_at=time.time())
            save(path, record)
            message = self.client.execute(record["code"], allow_stdin=False, stop_on_error=True)
            record["message_id"] = message
            save(path, record)
            deadline = time.monotonic() + record["timeout"]
            interrupted = False
            size = 0
            idle = False
            reply = None
            with path.with_suffix(".jsonl").open("a") as output:
                while not (idle and reply is not None):
                    if not self.manager.is_alive():
                        raise RuntimeError("kernel_lost")
                    if time.monotonic() > deadline:
                        if interrupted:
                            raise RuntimeError("interrupt_not_acknowledged")
                        self.manager.interrupt_kernel()
                        interrupted = True
                        deadline = time.monotonic() + 10
                    try:
                        event = self.client.get_iopub_msg(timeout=0.1)
                        if event.get("parent_header", {}).get("msg_id") == message:
                            kind, content = event["msg_type"], event["content"]
                            if kind == "status" and content["execution_state"] == "idle":
                                idle = True
                            if kind in {
                                "stream",
                                "error",
                                "execute_result",
                                "display_data",
                                "update_display_data",
                            }:
                                line = (
                                    json.dumps({"type": kind, "content": content}, default=str)
                                    + "\n"
                                )
                                size += len(line.encode())
                                if size > MAX_OUTPUT:
                                    raise RuntimeError("execution_output_limit_exceeded")
                                output.write(line)
                                output.flush()
                                os.fsync(output.fileno())
                    except Empty:
                        pass
                    try:
                        event = self.client.get_shell_msg(timeout=0.01)
                        if event.get("parent_header", {}).get("msg_id") == message:
                            reply = event["content"]
                    except Empty:
                        pass
            record.update(
                state="completed",
                exit_code=0 if reply["status"] == "ok" else 1,
                error=reply.get("ename"),
                timed_out=interrupted,
            )
        except Exception as exc:
            self.fenced = True
            record.update(state="unknown", reason=str(exc))
            if self.manager and self.manager.is_alive():
                self.manager.interrupt_kernel()
        finally:
            record["finished_at"] = time.time()
            save(path, record)
            with self.lock:
                self.active = None

    def interrupt(self, execution):
        with self.lock:
            if self.active == str(UUID(execution)):
                self.manager.interrupt_kernel()
            return self.status(execution)

    def reset(self):
        with self.lock:
            if self.active:
                raise ValueError("Interrupt and settle the active execution before reset")
            if self.client:
                self.client.stop_channels()
            if self.manager:
                self.manager.shutdown_kernel(now=True)
            elif self.meta:
                pid = self.meta["pid"]
                if sys.platform == "linux":
                    process = Path(f"/proc/{pid}/cmdline")
                    command = (
                        process.read_bytes().replace(b"\0", b" ").decode()
                        if process.exists()
                        else ""
                    )
                else:
                    command = subprocess.run(
                        ["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True
                    ).stdout
                if command:
                    if (
                        self.meta["connection_file"] not in command
                        or "ipykernel_launcher" not in command
                    ):
                        raise ValueError("Orphan process identity cannot be established")
                    os.killpg(pid, signal.SIGKILL)
            self.start_kernel()
            return self.health()

    def handle(self, request):
        operation = request["operation"]
        if operation == "health":
            return self.health()
        if operation == "submit":
            return self.submit(request["execution"], request["code"], request.get("timeout", 120))
        if operation == "status":
            return self.status(request["execution"], request.get("offset", 0))
        if operation == "interrupt":
            return self.interrupt(request["execution"])
        if operation == "reset":
            return self.reset()
        if operation == "close":
            if self.active:
                raise ValueError("Execution is still active")
            if self.manager:
                self.manager.shutdown_kernel(now=True)
                self.client.stop_channels()
            self.meta_path.unlink(missing_ok=True)
            self.fenced = True
            return {"closed": True}
        raise ValueError("Unknown session operation")


def request(root, payload):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(45)
        client.connect(str(Path(root) / ".session/socket"))
        client.sendall(json.dumps(payload).encode() + b"\n")
        with client.makefile("rb") as stream:
            line = stream.readline(1024 * 1024)
            if not line:
                raise ConnectionError("Supervisor disconnected before acknowledging the request")
            result = json.loads(line)
    if "transport_error" in result:
        raise ValueError(result["transport_error"])
    return result


def serve(root):
    root = Path(root).resolve()
    private = root / ".session"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (private / "server.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    session = Session(root)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                payload = json.loads(self.rfile.readline(512 * 1024))
                result = session.handle(payload)
            except Exception as exc:
                result = {"transport_error": str(exc)}
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(json.dumps(result).encode() + b"\n")

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    address = private / "socket"
    address.unlink(missing_ok=True)
    with Server(str(address), Handler) as server:
        address.chmod(0o600)
        server.serve_forever()


def ensure(root):
    try:
        return request(root, {"operation": "health"})
    except (OSError, ValueError):
        pass
    private = Path(root) / ".session"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    seal(root)
    with (private / "server.log").open("a") as log:
        subprocess.Popen(
            [sys.executable, "-m", "workshop_runtime.session", "--root", str(root), "serve"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            return request(root, {"operation": "health"})
        except (OSError, ValueError):
            time.sleep(0.1)
    raise RuntimeError("Session startup failed; inspect supervisor log")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("operation", choices=["serve", "ensure", "request", "file"])
    parser.add_argument("--payload")
    args = parser.parse_args()
    if args.operation == "serve":
        serve(args.root)
    elif args.operation == "file":
        print(json.dumps(file_request(args.root, json.loads(base64.b64decode(args.payload)))))
    else:
        result = (
            ensure(args.root)
            if args.operation == "ensure"
            else request(args.root, json.loads(base64.b64decode(args.payload)))
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()
