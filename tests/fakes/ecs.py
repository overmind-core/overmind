"""The ECS surfaces a hosted worker talks to, on one loopback server.

A task reads its own identity from the container metadata endpoint, sets scale-in
protection through the ECS agent, and calls the ECS and CloudWatch APIs with
task-role credentials. Point a test at it with ``FakeECS.env()``.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLUSTER = "test-cluster"
TASK_ARN = f"arn:aws:ecs:eu-west-1:123456789012:task/{CLUSTER}/0123456789abcdef"
OWN_REVISION = "arn:aws:ecs:eu-west-1:123456789012:task-definition/celery-landing-worker:7"
NEXT_REVISION = "arn:aws:ecs:eu-west-1:123456789012:task-definition/celery-landing-worker:8"


class FakeECS:
    def __init__(self, *, service: str = "celery-landing-worker"):
        self.service = service
        self.primary_revision = OWN_REVISION
        self.running = {}
        self.agent_status = 200
        self.api_status = 200
        self.protection: list[bool] = []
        self.api_calls: list[str] = []
        self.metric_requests: list[dict] = []
        self.credential_reads = 0
        self.short_lived_credentials = False
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def env(self) -> dict[str, str]:
        return {
            "ECS_CONTAINER_METADATA_URI_V4": f"{self.url}/metadata",
            "ECS_AGENT_URI": f"{self.url}/agent",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI": f"{self.url}/credentials",
            "AWS_ENDPOINT_URL_ECS": f"{self.url}/ecs",
            "AWS_ENDPOINT_URL_CLOUDWATCH": f"{self.url}/cloudwatch",
        }

    def start(self) -> FakeECS:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def _credentials(self) -> dict:
        self.credential_reads += 1
        # A first credential that is about to expire forces botocore to refresh it.
        lifetime = 30 if self.short_lived_credentials and self.credential_reads == 1 else 3600
        return {
            "AccessKeyId": f"task-role-{self.credential_reads}",
            "SecretAccessKey": "task-role-secret",
            "Token": "task-role-token",
            "Expiration": (datetime.now(UTC) + timedelta(seconds=lifetime)).isoformat(),
        }

    def _ecs(self, target: str, body: dict) -> dict:
        operation = target.rsplit(".", 1)[-1]
        self.api_calls.append(operation)
        if operation == "DescribeTasks":
            return {
                "tasks": [
                    {
                        "taskArn": arn,
                        "group": f"service:{self.service}",
                        "taskDefinitionArn": OWN_REVISION,
                    }
                    for arn in body["tasks"]
                ]
            }
        if operation == "DescribeServices":
            return {
                "services": [
                    {
                        "serviceName": name,
                        "runningCount": self.running.get(name, 1),
                        "deployments": [
                            {"status": "PRIMARY", "taskDefinition": self.primary_revision},
                            {"status": "ACTIVE", "taskDefinition": OWN_REVISION},
                        ],
                    }
                    for name in body["services"]
                ]
            }
        raise AssertionError(f"Unexpected ECS operation {operation}")

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status: int, payload: dict | None, kind="application/json"):
                body = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_GET(self):
                if self.path == "/metadata/task":
                    cluster = f"arn:aws:ecs:eu-west-1:123456789012:cluster/{CLUSTER}"
                    return self._send(200, {"TaskARN": TASK_ARN, "Cluster": cluster})
                if self.path == "/credentials":
                    return self._send(200, fake._credentials())
                self._send(404, {})

            def do_PUT(self):
                if self.path != "/agent/task-protection/v1/state":
                    return self._send(404, {})
                enabled = json.loads(self._body())["ProtectionEnabled"]
                if fake.agent_status != 200:
                    return self._send(fake.agent_status, {"error": {"Code": "AccessDenied"}})
                fake.protection.append(enabled)
                self._send(200, {"protection": {"ProtectionEnabled": enabled}})

            def do_POST(self):
                body = self._body()
                if self.path.startswith("/cloudwatch"):
                    fake.metric_requests.append(dict(self.headers))
                    return self._send(200, None, kind="application/cbor")
                if fake.api_status != 200:
                    return self._send(fake.api_status, {"__type": "ServerException"})
                payload = fake._ecs(self.headers["X-Amz-Target"], json.loads(body))
                self._send(200, payload, kind="application/x-amz-json-1.1")

        return Handler
