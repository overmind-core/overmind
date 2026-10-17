#!/usr/bin/env python3
"""Deploy reviewed immutable images and source-controlled worker commands.

AWS calls require an explicit ``preflight``, ``migrate`` or ``deploy`` subcommand. The rendering
helpers are pure so task definitions can be reviewed and tested without AWS.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEALTH_CHECKED_QUEUES = {
    "celery-io-worker": "io",
    "celery-traces-worker": "io_traces",
    "celery-landing-worker": "landing",
    "celery-interactive-worker": "interactive",
}
READ_ONLY_FIELDS = (
    "taskDefinitionArn",
    "revision",
    "status",
    "requiresAttributes",
    "compatibilities",
    "registeredAt",
    "registeredBy",
    "deregisteredAt",
)


def _environment(container, **values):
    existing = {item["name"]: item["value"] for item in container.get("environment", [])}
    existing.update({name: str(value) for name, value in values.items()})
    container["environment"] = [{"name": key, "value": value} for key, value in existing.items()]


def render_task_definition(document, *, service, image, cluster):
    result = copy.deepcopy(document)
    for field in READ_ONLY_FIELDS:
        result.pop(field, None)
    containers = [item for item in result["containerDefinitions"] if item["name"] == service]
    if len(containers) != 1:
        raise ValueError(f"Expected exactly one app container named {service!r}")
    app = containers[0]
    app["image"] = image
    topology = json.loads((ROOT / "docker/worker-topology.json").read_text())
    if service in topology:
        app["command"] = topology[service]
        app["entryPoint"] = ["/usr/local/bin/worker-entrypoint.sh"]
    elif service != "api":
        raise ValueError(f"No reviewed command for service {service!r}")
    if service == "sandbox":
        linux = app.setdefault("linuxParameters", {})
        linux["initProcessEnabled"] = True
        linux["capabilities"] = {
            "add": ["SYS_PTRACE"],
            "drop": [
                "AUDIT_WRITE",
                "FSETID",
                "MKNOD",
                "NET_BIND_SERVICE",
                "NET_RAW",
                "SETFCAP",
                "SETPCAP",
                "SYS_CHROOT",
            ],
        }
        app["healthCheck"] = {
            "command": [
                "CMD",
                "python",
                "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5)",
            ],
            "interval": 30,
            "timeout": 15,
            "retries": 3,
            "startPeriod": 90,
        }
        app["stopTimeout"] = 120
    if service != "sandbox":
        _environment(app, RUN_DB_BOOTSTRAP="0", WORKSHOP_EXECUTION_BACKEND="sandbox-package-v1")
    if service == "api" and cluster in {"overmind-prod-cluster", "overmind-staging-cluster"}:
        host = (
            "api.overmindlab.ai"
            if cluster == "overmind-prod-cluster"
            else "api-staging.overmindlab.ai"
        )
        _environment(app, MCP_SERVER_URL=f"https://{host}/api/mcp/")
    if service == "celery-control-worker":
        _environment(app, QUEUE_METRICS_ENABLED="1", QUEUE_METRICS_CLUSTER=cluster)
    if service in HEALTH_CHECKED_QUEUES:
        app["healthCheck"] = {
            "command": [
                "CMD",
                "python",
                "-m",
                "overbae.worker_health",
                HEALTH_CHECKED_QUEUES[service],
            ],
            "interval": 30,
            "timeout": 15,
            "retries": 3,
            "startPeriod": 90,
        }
        # Durable leases own retries after an interrupted process; scale-in
        # remains disabled until a reviewed graceful-drain policy is installed.
        app["stopTimeout"] = 120
    return result


def preflight(cluster):
    for name in (
        "sandbox",
        "celery-landing-worker",
        "celery-interactive-worker",
        "celery-traces-worker",
    ):
        service = describe_service(cluster, name)
        family = service["taskDefinition"].rsplit("/", 1)[-1].rsplit(":", 1)[0]
        definition = aws("ecs", "describe-task-definition", "--task-definition", family)[
            "taskDefinition"
        ]
        if not any(c["name"] == name for c in definition["containerDefinitions"]):
            raise ValueError(f"Missing {name} container in its task definition")
    api = describe_service(cluster, "api")
    definition = aws("ecs", "describe-task-definition", "--task-definition", api["taskDefinition"])[
        "taskDefinition"
    ]
    app = next(c for c in definition["containerDefinitions"] if c["name"] == "api")
    environment = {item["name"]: item["value"] for item in app.get("environment", [])}
    if environment.get("WORKSHOP_EXECUTION_BACKEND") != "sandbox-package-v1":
        require_drained_consumers(cluster)
    print("Required consumer infrastructure is configured; no services or schema were changed.")


def require_drained_consumers(cluster):
    names = ["api", *json.loads((ROOT / "docker/worker-topology.json").read_text())]
    for name in names:
        service = describe_service(cluster, name)
        if any(service.get(key, 0) for key in ("desiredCount", "runningCount", "pendingCount")):
            raise RuntimeError(
                f"The schema cutover requires drained, stopped consumers; {name} is active. "
                "Follow scripts/workshop-rollout.md before migrating."
            )


def migration_request(definition, service, *, task_definition, cluster):
    matches = [c for c in definition["containerDefinitions"] if c["name"] == "api"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one migration container named 'api'")
    app = matches[0]
    request = {
        "cluster": cluster,
        "taskDefinition": task_definition,
        "count": 1,
        "networkConfiguration": service["networkConfiguration"],
        "overrides": {
            "containerOverrides": [
                {
                    "name": app["name"],
                    "command": ["python", "manage.py", "migrate", "--noinput"],
                    "environment": [{"name": "RUN_DB_BOOTSTRAP", "value": "0"}],
                }
            ]
        },
    }
    for key in ("launchType", "capacityProviderStrategy", "platformVersion"):
        if service.get(key):
            request[key] = service[key]
    return request


def service_ready(service, tasks, *, task_definition, require_health=True):
    desired = service.get("desiredCount", 0)
    if desired < 1 or service.get("pendingCount", 0) or service.get("runningCount", 0) < desired:
        return False
    deployments = service.get("deployments", [])
    if len(deployments) != 1 or deployments[0].get("taskDefinition") != task_definition:
        return False
    if deployments[0].get("rolloutState") != "COMPLETED":
        return False
    current = [
        task
        for task in tasks
        if task.get("taskDefinitionArn") == task_definition
        and task.get("lastStatus") == "RUNNING"
        and (not require_health or task.get("healthStatus") == "HEALTHY")
    ]
    return len(current) >= desired


def aws(*args, payload=None):
    command = ["aws", *args, "--output", "json"]
    if payload is None:
        return json.loads(subprocess.check_output(command, text=True))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as fp:
        json.dump(payload, fp)
        fp.flush()
        return json.loads(
            subprocess.check_output(
                [*command, "--cli-input-json", f"file://{fp.name}"],
                text=True,
            )
        )


def describe_service(cluster, service):
    response = aws("ecs", "describe-services", "--cluster", cluster, "--services", service)
    if response.get("failures") or len(response.get("services", [])) != 1:
        raise RuntimeError(
            f"Service {service!r} is absent. Apply it in cloud-platform (Terraform) "
            "before this release; no producer has been deployed."
        )
    return response["services"][0]


def register(cluster, service_name, image):
    service = describe_service(cluster, service_name)
    # Terraform (cloud-platform) registers env, secret and role changes as new
    # revisions without moving the service; the family name resolves to the latest.
    family = service["taskDefinition"].rsplit("/", 1)[-1].rsplit(":", 1)[0]
    definition = aws("ecs", "describe-task-definition", "--task-definition", family)[
        "taskDefinition"
    ]
    rendered = render_task_definition(
        definition, service=service_name, image=image, cluster=cluster
    )
    registered = aws("ecs", "register-task-definition", payload=rendered)["taskDefinition"]
    return service, registered


def migrate(cluster, image):
    preflight(cluster)
    service, definition = register(cluster, "api", image)
    response = aws(
        "ecs",
        "run-task",
        payload=migration_request(
            definition,
            service,
            task_definition=definition["taskDefinitionArn"],
            cluster=cluster,
        ),
    )
    if response.get("failures") or len(response.get("tasks", [])) != 1:
        raise RuntimeError(f"Migration task was not accepted: {response.get('failures')}")
    arn = response["tasks"][0]["taskArn"]
    subprocess.run(
        ["aws", "ecs", "wait", "tasks-stopped", "--cluster", cluster, "--tasks", arn],
        check=True,
    )
    task = aws("ecs", "describe-tasks", "--cluster", cluster, "--tasks", arn)["tasks"][0]
    # Only the API app container runs migrations; ignore a successfully stopped sidecar.
    app = next(c for c in task["containers"] if c["name"] == "api")
    if app.get("exitCode") != 0:
        raise RuntimeError(
            f"Migration failed in {arn}: {app.get('reason', task.get('stoppedReason'))}"
        )
    print(f"Migration completed: {arn}")


def drain_io(cluster, image, timeout=4200):
    service, definition = register(cluster, "api", image)
    request = migration_request(
        definition, service, task_definition=definition["taskDefinitionArn"], cluster=cluster
    )
    request["overrides"]["containerOverrides"][0]["command"] = [
        "python",
        "-m",
        "overbae.worker_drain",
        "--timeout",
        str(timeout),
    ]
    result = aws("ecs", "run-task", payload=request)
    if result.get("failures") or len(result.get("tasks", [])) != 1:
        raise RuntimeError("IO drain task was not accepted; worker deployment stopped")
    arn = result["tasks"][0]["taskArn"]
    deadline = time.monotonic() + timeout + 180
    while time.monotonic() < deadline:
        task = aws("ecs", "describe-tasks", "--cluster", cluster, "--tasks", arn)["tasks"][0]
        if task["lastStatus"] == "STOPPED":
            app = next(c for c in task["containers"] if c["name"] == "api")
            if app.get("exitCode") != 0:
                raise RuntimeError(f"IO drain failed in {arn}; worker deployment stopped")
            print(f"Mixed IO consumers drained: {arn}", flush=True)
            return
        time.sleep(10)
    raise TimeoutError(f"IO drain did not complete: {arn}; worker deployment stopped")


def deploy(cluster, service_name, image, timeout=4200, desired_count=None):
    if desired_count is not None and desired_count < 1:
        raise ValueError("Deployment requires a positive desired count.")
    current = describe_service(cluster, service_name)
    if not current.get("desiredCount") and desired_count is None:
        raise ValueError(
            "Restore the recorded service capacity with --desired-count after cutover."
        )
    _, definition = register(cluster, service_name, image)
    arn = definition["taskDefinitionArn"]
    if service_name == "celery-io-worker":
        drain_io(cluster, image, timeout)
    count = desired_count
    options = ["--desired-count", str(count)] if count is not None else []
    aws(
        "ecs",
        "update-service",
        "--cluster",
        cluster,
        "--service",
        service_name,
        "--task-definition",
        arn,
        *options,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        service = describe_service(cluster, service_name)
        primary = next(
            (d for d in service.get("deployments", []) if d.get("status") == "PRIMARY"),
            {},
        )
        if primary.get("rolloutState") == "FAILED" or primary.get("taskDefinition") != arn:
            raise RuntimeError(
                f"Deployment {arn} failed or rolled back; producer rollout is stopped"
            )
        arns = aws(
            "ecs",
            "list-tasks",
            "--cluster",
            cluster,
            "--service-name",
            service_name,
            "--desired-status",
            "RUNNING",
        )["taskArns"]
        tasks = []
        for offset in range(0, len(arns), 100):
            tasks.extend(
                aws(
                    "ecs",
                    "describe-tasks",
                    "--cluster",
                    cluster,
                    "--tasks",
                    *arns[offset : offset + 100],
                )["tasks"]
            )
        if service_ready(
            service,
            tasks,
            task_definition=arn,
            require_health=service_name in HEALTH_CHECKED_QUEUES or service_name == "sandbox",
        ):
            print(f"Ready: {service_name} {arn}")
            return
        time.sleep(10)
    raise TimeoutError(f"Service {service_name} did not become ready; producer rollout is stopped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["migrate", "deploy", "preflight"])
    parser.add_argument(
        "--cluster",
        default=os.environ.get("ECS_CLUSTER"),
        required=not os.environ.get("ECS_CLUSTER"),
    )
    parser.add_argument(
        "--image",
        default=os.environ.get("IMAGE_URI"),
    )
    parser.add_argument("--service", default=os.environ.get("SERVICE", "api"))
    parser.add_argument(
        "--desired-count", type=int, help="Explicitly restore recorded cutover capacity"
    )
    args = parser.parse_args()
    if args.action == "preflight":
        preflight(args.cluster)
        return
    if not args.image:
        parser.error("--image or IMAGE_URI is required for deployment")
    if args.action == "migrate":
        migrate(args.cluster, args.image)
    else:
        deploy(args.cluster, args.service, args.image, desired_count=args.desired_count)


if __name__ == "__main__":
    main()
