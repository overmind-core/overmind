"""A release must update worker commands and prove the new consumer is healthy."""

import pytest

from scripts import deploy_ecs
from scripts.deploy_ecs import migration_request, render_task_definition, service_ready


@pytest.fixture
def definition():
    return {
        "family": "celery-batch-worker",
        "revision": 6,
        "taskDefinitionArn": "arn:aws:ecs:eu-west-1:123:task-definition/celery-batch-worker:6",
        "status": "ACTIVE",
        "requiresAttributes": [],
        "compatibilities": ["FARGATE"],
        "cpu": "4096",
        "memory": "8192",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "taskRoleArn": "arn:aws:iam::123:role/app",
        "executionRoleArn": "arn:aws:iam::123:role/exec",
        "volumes": [{"name": "data", "efsVolumeConfiguration": {"fileSystemId": "fs-123"}}],
        "containerDefinitions": [
            {
                "name": "celery-batch-worker",
                "image": "123.dkr.ecr.eu-west-1.amazonaws.com/overmind-prod-app:v0.1.0",
                "command": ["celery", "-A", "overbae", "worker", "-Q", "batch"],
                "secrets": [
                    {
                        "name": "DATABASE_URL",
                        "valueFrom": "arn:aws:secretsmanager:eu-west-1:123:secret:database",
                    }
                ],
                "environment": [{"name": "KEEP", "value": "yes"}],
                "mountPoints": [{"sourceVolume": "data", "containerPath": "/data"}],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {"awslogs-group": "/ecs/prod/batch", "awslogs-stream-prefix": "ecs"},
                },
                "essential": True,
                "stopTimeout": 120,
            },
            {"name": "otel", "image": "otel:stable", "command": ["collector"], "essential": False},
        ],
    }


@pytest.fixture
def service():
    return {
        "serviceName": "celery-batch-worker",
        "desiredCount": 1,
        "runningCount": 1,
        "pendingCount": 0,
        "launchType": "FARGATE",
        "platformVersion": "LATEST",
        "networkConfiguration": {
            "awsvpcConfiguration": {
                "subnets": ["subnet-a"],
                "securityGroups": ["sg-a"],
                "assignPublicIp": "DISABLED",
            }
        },
        "deploymentConfiguration": {
            "deploymentCircuitBreaker": {"enable": True, "rollback": True},
            "maximumPercent": 200,
            "minimumHealthyPercent": 100,
        },
    }


@pytest.mark.parametrize("queue", ["landing", "interactive"])
def test_every_release_overrides_stale_worker_command(definition, queue):
    service = f"celery-{queue}-worker"
    definition["family"] = service
    definition["containerDefinitions"][0]["name"] = service
    rendered = render_task_definition(
        definition, service=service, image="repo:new", cluster="test-cluster"
    )
    container = rendered["containerDefinitions"][0]
    assert container["image"] == "repo:new"
    assert container["command"][-2:] == ["-Q", queue]
    assert f"--hostname={queue}@%h" in container["command"]
    assert container["entryPoint"] == ["/usr/local/bin/worker-entrypoint.sh"]
    assert container["healthCheck"]["command"][-2:] == ["overbae.worker_health", queue]
    assert container["stopTimeout"] == 120
    assert rendered["containerDefinitions"][1]["image"] == "otel:stable"


def test_migration_cannot_start_http_server(definition, service):
    definition["containerDefinitions"][0]["name"] = "api"
    request = migration_request(
        definition, service, task_definition="new:7", cluster="test-cluster"
    )
    override = request["overrides"]["containerOverrides"][0]
    assert override["command"] == ["python", "manage.py", "migrate", "--noinput"]
    assert {entry["name"]: entry["value"] for entry in override["environment"]}[
        "RUN_DB_BOOTSTRAP"
    ] == "0"
    assert (
        request["count"] == 1 and request["networkConfiguration"] == service["networkConfiguration"]
    )


def test_running_container_without_health_or_current_revision_is_not_ready():
    service = {
        "desiredCount": 1,
        "runningCount": 1,
        "pendingCount": 0,
        "deployments": [
            {"status": "PRIMARY", "taskDefinition": "new:7", "rolloutState": "COMPLETED"}
        ],
    }
    task = {"lastStatus": "RUNNING", "taskDefinitionArn": "new:7", "healthStatus": "HEALTHY"}
    assert service_ready(service, [task], task_definition="new:7")
    assert not service_ready(
        service, [{**task, "healthStatus": "UNKNOWN"}], task_definition="new:7"
    )
    assert not service_ready(
        service, [{**task, "taskDefinitionArn": "old:6"}], task_definition="new:7"
    )
    assert not service_ready({**service, "desiredCount": 0}, [], task_definition="new:7")


def test_migration_selects_api_even_when_an_essential_sidecar_is_first(definition, service):
    app = definition["containerDefinitions"][0]
    app["name"] = "api"
    sidecar = {"name": "essential-sidecar", "image": "sidecar:stable", "essential": True}
    definition["containerDefinitions"] = [sidecar, app]
    request = migration_request(
        definition, service, task_definition="api:7", cluster="test-cluster"
    )
    assert [item["name"] for item in request["overrides"]["containerOverrides"]] == ["api"]
    assert definition["containerDefinitions"][0] == sidecar


def test_migration_refuses_a_task_definition_without_the_api_container(definition, service):
    with pytest.raises(ValueError, match="api"):
        migration_request(definition, service, task_definition="wrong:7", cluster="test-cluster")


def test_a_release_builds_on_the_latest_revision_of_the_family(monkeypatch, definition):
    """Terraform registers env, secret and role changes as new revisions without moving
    the service, so a release copied from the service's pinned revision would drop them."""
    described = []

    def fake_aws(*args, payload=None):
        if args[:2] == ("ecs", "describe-services"):
            return {"services": [{"taskDefinition": definition["taskDefinitionArn"]}]}
        if args[:2] == ("ecs", "describe-task-definition"):
            described.append(args[-1])
            return {"taskDefinition": definition}
        return {"taskDefinition": {**payload, "taskDefinitionArn": "celery-batch-worker:7"}}

    monkeypatch.setattr(deploy_ecs, "aws", fake_aws)
    deploy_ecs.register("test-cluster", "celery-batch-worker", "repo:new")
    assert described == ["celery-batch-worker"]
