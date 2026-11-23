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
# A large index build outlasts the AWS CLI waiter's 10 minutes; this stays inside the
# deploy role's one-hour credentials.
MIGRATION_SECONDS = 45 * 60
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


def _app_container(definition, service):
    containers = [item for item in definition["containerDefinitions"] if item["name"] == service]
    if len(containers) != 1:
        raise ValueError(f"Expected exactly one app container named {service!r}")
    return containers[0]


def render_task_definition(document, *, service, image):
    """Terraform (cloud-platform) owns every other field; a release changes only what
    ships with the code."""
    result = copy.deepcopy(document)
    for field in READ_ONLY_FIELDS:
        result.pop(field, None)
    app = _app_container(result, service)
    app["image"] = image
    topology = json.loads((ROOT / "docker/worker-topology.json").read_text())
    if service in topology:
        app["command"] = topology[service]
        app["entryPoint"] = ["/usr/local/bin/worker-entrypoint.sh"]
    elif service != "api":
        raise ValueError(f"No reviewed command for service {service!r}")
    return result


def preflight(cluster):
    for name in (
        "sandbox",
        "celery-landing-worker",
        "celery-interactive-worker",
        "celery-traces-worker",
        "celery-assistant-worker",
    ):
        service = describe_service(cluster, name)
        family = service["taskDefinition"].rsplit("/", 1)[-1].rsplit(":", 1)[0]
        definition = aws("ecs", "describe-task-definition", "--task-definition", family)[
            "taskDefinition"
        ]
        if not any(c["name"] == name for c in definition["containerDefinitions"]):
            raise ValueError(f"Missing {name} container in its task definition")
    print("Required consumer infrastructure is configured; no services or schema were changed.")


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
    rendered = render_task_definition(definition, service=service_name, image=image)
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
    task = _stopped(cluster, arn, MIGRATION_SECONDS)
    if task is None:
        raise TimeoutError(
            f"Migration {arn} still runs after {MIGRATION_SECONDS // 60} minutes; "
            "re-run the release once it stops."
        )
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
    task = _stopped(cluster, arn, timeout + 180)
    if task is None:
        raise TimeoutError(f"IO drain did not complete: {arn}; worker deployment stopped")
    app = next(c for c in task["containers"] if c["name"] == "api")
    if app.get("exitCode") != 0:
        raise RuntimeError(f"IO drain failed in {arn}; worker deployment stopped")
    print(f"Mixed IO consumers drained: {arn}", flush=True)


def _stopped(cluster, arn, timeout):
    """The task once it stopped, or None at the deadline."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = aws("ecs", "describe-tasks", "--cluster", cluster, "--tasks", arn)["tasks"][0]
        if task["lastStatus"] == "STOPPED":
            return task
        time.sleep(10)
    return None


def deploy(cluster, service_name, image, timeout=4200, desired_count=None):
    if desired_count is not None and desired_count < 1:
        raise ValueError("Deployment requires a positive desired count.")
    current = describe_service(cluster, service_name)
    if not current.get("desiredCount") and desired_count is None:
        raise ValueError(f"{service_name} is scaled to zero; pass --desired-count to restore it.")
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
            require_health="healthCheck" in _app_container(definition, service_name),
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
    parser.add_argument("--desired-count", type=int, help="Task count for a service scaled to zero")
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
