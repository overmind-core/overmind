#!/usr/bin/env python3
"""Roll an immutable image out to the ECS services cloud-platform provisions.

A release changes only the app image. cloud-platform owns every other task-definition
field (command, environment, health check, resources), so an apply and a release never
undo each other. Subcommands:

  lanes      print the worker services this release deploys, as JSON
  preflight  refuse the release unless every service it needs exists
  migrate    run migrations in a one-off API task that never starts the web server
  deploy     roll one service to the image and wait until it is running and healthy
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXED_SERVICES = ("api", "celery-beat")
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


def _lanes():
    # Loaded by path: the deploy runner has none of the app's dependencies.
    spec = importlib.util.spec_from_file_location("overbae_lanes", ROOT / "overbae/lanes.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def lane_services() -> list[str]:
    lanes = _lanes()
    return [lanes.service(lane) for lane in lanes.LANES]


def absent_services(found: list[dict]) -> list[str]:
    present = {item["serviceName"] for item in found}
    return [name for name in [*FIXED_SERVICES, *lane_services()] if name not in present]


def render_task_definition(document, *, image, repository):
    result = copy.deepcopy(document)
    for field in READ_ONLY_FIELDS:
        result.pop(field, None)
    apps = [
        container
        for container in result["containerDefinitions"]
        if f"/{repository}:" in container["image"] or f"/{repository}@" in container["image"]
    ]
    if not apps:
        raise ValueError(f"No container runs an image from {repository!r}")
    for container in apps:
        container["image"] = image
    return result


def migration_request(definition, service, *, task_definition, cluster):
    matches = [c for c in definition["containerDefinitions"] if c["name"] == "api"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one migration container named 'api'")
    request = {
        "cluster": cluster,
        "taskDefinition": task_definition,
        "count": 1,
        "networkConfiguration": service["networkConfiguration"],
        "overrides": {
            "containerOverrides": [
                {
                    "name": "api",
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


def service_ready(service, tasks, *, task_definition, health_checked):
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
        and (not health_checked or task.get("healthStatus") == "HEALTHY")
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
            subprocess.check_output([*command, "--cli-input-json", f"file://{fp.name}"], text=True)
        )


def describe_service(cluster, name):
    response = aws("ecs", "describe-services", "--cluster", cluster, "--services", name)
    if response.get("failures") or len(response.get("services", [])) != 1:
        raise RuntimeError(f"Service {name!r} is absent; apply cloud-platform first.")
    return response["services"][0]


def preflight(cluster):
    names = [*FIXED_SERVICES, *lane_services()]
    found = []
    # DescribeServices accepts at most ten services per call.
    for offset in range(0, len(names), 10):
        response = aws(
            "ecs",
            "describe-services",
            "--cluster",
            cluster,
            "--services",
            *names[offset : offset + 10],
        )
        found += [s for s in response.get("services", []) if s.get("status") == "ACTIVE"]
    absent = absent_services(found)
    if absent:
        raise RuntimeError(
            f"{', '.join(absent)} not provisioned in {cluster}; apply cloud-platform first. "
            "Nothing was deployed."
        )
    print(f"All {len(names)} services are provisioned in {cluster}.")


def register(cluster, name, image, repository):
    service = describe_service(cluster, name)
    definition = aws(
        "ecs", "describe-task-definition", "--task-definition", service["taskDefinition"]
    )["taskDefinition"]
    rendered = render_task_definition(definition, image=image, repository=repository)
    registered = aws("ecs", "register-task-definition", payload=rendered)["taskDefinition"]
    return service, registered


def migrate(cluster, image, repository):
    service, definition = register(cluster, "api", image, repository)
    request = migration_request(
        definition, service, task_definition=definition["taskDefinitionArn"], cluster=cluster
    )
    response = aws("ecs", "run-task", payload=request)
    if response.get("failures") or len(response.get("tasks", [])) != 1:
        raise RuntimeError(f"Migration task was not accepted: {response.get('failures')}")
    arn = response["tasks"][0]["taskArn"]
    subprocess.run(
        ["aws", "ecs", "wait", "tasks-stopped", "--cluster", cluster, "--tasks", arn], check=True
    )
    task = aws("ecs", "describe-tasks", "--cluster", cluster, "--tasks", arn)["tasks"][0]
    app = next(c for c in task["containers"] if c["name"] == "api")
    if app.get("exitCode") != 0:
        raise RuntimeError(
            f"Migration failed in {arn}: {app.get('reason', task.get('stoppedReason'))}"
        )
    print(f"Migration completed: {arn}")


def deploy(cluster, name, image, repository, timeout=4200):
    # Busy workers keep ECS task protection until their own work finishes, so a
    # worker rollout can legitimately take as long as its longest task.
    _, definition = register(cluster, name, image, repository)
    arn = definition["taskDefinitionArn"]
    health_checked = any(c.get("healthCheck") for c in definition["containerDefinitions"])
    aws("ecs", "update-service", "--cluster", cluster, "--service", name, "--task-definition", arn)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        service = describe_service(cluster, name)
        primary = next(
            (d for d in service.get("deployments", []) if d.get("status") == "PRIMARY"), {}
        )
        if primary.get("rolloutState") == "FAILED" or primary.get("taskDefinition") != arn:
            raise RuntimeError(f"Deployment {arn} failed or rolled back; the release is stopped")
        arns = aws(
            "ecs",
            "list-tasks",
            "--cluster",
            cluster,
            "--service-name",
            name,
            "--desired-status",
            "RUNNING",
        )["taskArns"]
        tasks = []
        for offset in range(0, len(arns), 100):
            tasks += aws(
                "ecs",
                "describe-tasks",
                "--cluster",
                cluster,
                "--tasks",
                *arns[offset : offset + 100],
            )["tasks"]
        if service_ready(service, tasks, task_definition=arn, health_checked=health_checked):
            print(f"Ready: {name} {arn}")
            return
        time.sleep(10)
    raise TimeoutError(f"Service {name} did not become ready; the release is stopped")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=["lanes", "preflight", "migrate", "deploy"])
    parser.add_argument("--cluster", default=os.environ.get("ECS_CLUSTER"))
    parser.add_argument("--image", default=os.environ.get("IMAGE_URI"))
    parser.add_argument("--repository", default=os.environ.get("ECR_REPOSITORY"))
    parser.add_argument("--service", default=os.environ.get("SERVICE"))
    args = parser.parse_args()
    if args.action == "lanes":
        print(json.dumps(lane_services()))
        return
    if not args.cluster:
        parser.error("--cluster or ECS_CLUSTER is required")
    if args.action == "preflight":
        preflight(args.cluster)
        return
    if not args.image or not args.repository:
        parser.error("--image and --repository (or IMAGE_URI and ECR_REPOSITORY) are required")
    if args.action == "migrate":
        migrate(args.cluster, args.image, args.repository)
    elif not args.service:
        parser.error("--service or SERVICE is required")
    else:
        deploy(args.cluster, args.service, args.image, args.repository)


if __name__ == "__main__":
    main()
