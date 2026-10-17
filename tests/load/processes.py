import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import requests
from celery.contrib.testing.worker import start_worker
from django.db import connection

from overbae.celery import app


class ProcessAPI:
    def __init__(self, settings, directory, workers=2):
        # Match the hosted API's two workers without sharing the load generator's interpreter.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.workers = workers
        self.log_path = Path(directory) / "api-processes.log"
        self.env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "tests.load.settings",
            "OVERMIND_LOAD_WORKER_DATABASE": connection.settings_dict["NAME"],
            "OVERMIND_LOAD_MEDIA_ROOT": str(settings.MEDIA_ROOT),
            "SANDBOX_URL": settings.SANDBOX_URL,
            "SANDBOX_TOKEN": settings.SANDBOX_TOKEN,
            "DATASET_QUERY_TIMEOUT_SECONDS": str(settings.DATASET_QUERY_TIMEOUT_SECONDS),
            "DATASET_QUERY_MAX_IN_FLIGHT": str(settings.DATASET_QUERY_MAX_IN_FLIGHT),
            "DATASET_QUERY_PROJECT_CONCURRENCY": str(settings.DATASET_QUERY_PROJECT_CONCURRENCY),
        }

    def __enter__(self):
        self.log = self.log_path.open("w")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "overbae.asgi:application",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--workers",
                str(self.workers),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            env=self.env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        while self.process.poll() is None and time.monotonic() < deadline:
            try:
                if requests.get(self.url + "/health", timeout=1).status_code == 200:
                    return self
            except requests.RequestException:
                pass
            time.sleep(0.1)
        self.__exit__(None, None, None)
        raise AssertionError(f"Separate API did not start: {self.log_path.read_text()[-4000:]}")

    def __exit__(self, *_):
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.log.close()


def drain_process_results(ledger, timeout=120):
    deadline = time.monotonic() + timeout
    while pending := ledger.pending():
        for task_id in pending:
            result = app.AsyncResult(task_id)
            if result.ready():
                ledger.finished[task_id] = result.state
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Unfinished worker tasks: {ledger.pending()}")
        time.sleep(0.05)


class ProcessWorker:
    """A separate prefork worker; macOS uses Linux to avoid unsafe library forks."""

    def __init__(self, settings, directory, queue="interactive", concurrency=4):
        self.settings = settings
        self.hostname = f"capacity-{uuid.uuid4().hex}@localhost"
        self.log_path = Path(directory) / f"{queue}-worker.log"
        self.container = None
        arguments = [
            "-m",
            "celery",
            "-A",
            "overbae",
            "worker",
            "--pool=prefork",
            f"--concurrency={concurrency}",
            "--prefetch-multiplier=1",
            "--disable-prefetch",
            "--hostname",
            self.hostname,
            "-Q",
            queue,
            "--loglevel=INFO",
        ]
        self.env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "tests.load.settings",
            "OVERMIND_LOAD_WORKER_DATABASE": connection.settings_dict["NAME"],
            "OVERMIND_LOAD_MEDIA_ROOT": str(settings.MEDIA_ROOT),
            "MEDIA_ROOT": str(settings.MEDIA_ROOT),
            "SANDBOX_URL": settings.SANDBOX_URL,
            "SANDBOX_TOKEN": settings.SANDBOX_TOKEN,
        }
        self.command = [sys.executable, *arguments]
        if sys.platform == "darwin":
            self.container = "overmind-capacity-" + uuid.uuid4().hex[:12]
            self.command = [
                "docker",
                "run",
                "--rm",
                "--name",
                self.container,
                "--cpus=4",
                "--memory=8g",
                "-w",
                "/code",
                "--entrypoint",
                "python",
                "-v",
                f"{settings.MEDIA_ROOT}:{settings.MEDIA_ROOT}",
            ]
            for directory in ("overbae", "tests", "sandbox", "modal_shared"):
                self.command.extend(["-v", f"{Path.cwd() / directory}:/code/{directory}:ro"])
            overrides = {
                key: self.env[key]
                for key in (
                    "DJANGO_SETTINGS_MODULE",
                    "OVERMIND_LOAD_TEST_DATABASE",
                    "OVERMIND_LOAD_WORKER_DATABASE",
                    "OVERMIND_LOAD_MEDIA_ROOT",
                    "MEDIA_ROOT",
                    "SANDBOX_TOKEN",
                )
            }
            overrides.update(
                PYTHONPATH="/code",
                TEST_POSTGRES_HOST="host.docker.internal",
                TEST_REDIS_URL=os.environ["TEST_REDIS_URL"]
                .replace("localhost", "host.docker.internal")
                .replace("127.0.0.1", "host.docker.internal"),
                SANDBOX_URL=settings.SANDBOX_URL.replace("127.0.0.1", "host.docker.internal"),
            )
            for key, value in overrides.items():
                self.command.extend(["-e", f"{key}={value}"])
            self.command.extend([os.environ["SANDBOX_TEST_IMAGE"], *arguments])

    def __enter__(self):
        self.log = self.log_path.open("w")
        self.process = subprocess.Popen(
            self.command, env=self.env, stdout=self.log, stderr=subprocess.STDOUT
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if app.control.ping(destination=[self.hostname], timeout=1):
                return self
            if self.process.poll() is not None:
                break
        self.__exit__(None, None, None)
        raise AssertionError(f"Prefork worker did not start: {self.log_path.read_text()[-4000:]}")

    def kill_task(self, pid):
        if self.container:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    self.container,
                    "python",
                    "-c",
                    "import os,signal,sys; os.kill(int(sys.argv[1]),signal.SIGKILL)",
                    str(pid),
                ],
                check=True,
            )
        else:
            os.kill(pid, signal.SIGKILL)

    def stats(self):
        if not self.container:
            return {"status": "unmeasured", "reason": "native worker process"}
        result = json.loads(
            subprocess.check_output(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", self.container],
                text=True,
                timeout=10,
            )
        )
        return {key: result[key] for key in ("CPUPerc", "MemUsage", "PIDs", "BlockIO")}

    def __exit__(self, *_):
        if self.container:
            subprocess.run(
                ["docker", "stop", "-t", "15", self.container], capture_output=True, timeout=25
            )
        self.process.terminate()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.log.close()


@contextmanager
def landing_workers(settings, directory):
    from overbae.tasks.datasets import dispatch_landings

    with (
        start_worker(
            app,
            pool="threads",
            concurrency=1,
            queues=["control"],
            hostname=f"landing-control-{uuid.uuid4().hex}",
            perform_ping_check=False,
            shutdown_timeout=30,
        ),
        ProcessWorker(settings, directory, queue="landing", concurrency=2),
    ):
        dispatch_landings.delay()
        yield
