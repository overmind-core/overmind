"""A release must update worker commands and prove the new consumer is healthy."""

import copy

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
    latest = {
        **copy.deepcopy(definition),
        "revision": 9,
        "taskRoleArn": "arn:aws:iam::123:role/latest",
    }
    latest["containerDefinitions"][0]["environment"] = [{"name": "FROM", "value": "terraform"}]
    revisions = {definition["taskDefinitionArn"]: definition, "celery-batch-worker": latest}
    registered = []

    def fake_aws(*args, payload=None):
        if args[:2] == ("ecs", "describe-services"):
            return {"services": [{"taskDefinition": definition["taskDefinitionArn"]}]}
        if args[:2] == ("ecs", "describe-task-definition"):
            return {"taskDefinition": revisions[args[-1]]}
        registered.append(payload)
        return {"taskDefinition": {**payload, "taskDefinitionArn": "celery-batch-worker:10"}}

    monkeypatch.setattr(deploy_ecs, "aws", fake_aws)
    deploy_ecs.register("test-cluster", "celery-batch-worker", "repo:new")
    [release] = registered
    assert release["taskRoleArn"] == "arn:aws:iam::123:role/latest"
    assert release["containerDefinitions"][0]["environment"] == [
        {"name": "FROM", "value": "terraform"},
        {"name": "RUN_DB_BOOTSTRAP", "value": "0"},
        {"name": "WORKSHOP_EXECUTION_BACKEND", "value": "sandbox-package-v1"},
    ]
    assert release["containerDefinitions"][0]["image"] == "repo:new"


def test_document_parser_keeps_main_isolation_command_and_health(definition):
    definition["containerDefinitions"][0]["name"] = "sandbox"
    rendered = render_task_definition(
        definition, service="sandbox", image="repo:new", cluster="test"
    )
    container = rendered["containerDefinitions"][0]
    assert container["command"] == ["python", "-m", "sandbox"]
    assert container["linuxParameters"]["initProcessEnabled"] is True
    assert container["linuxParameters"]["capabilities"]["add"] == ["SYS_PTRACE"]
    assert "NET_RAW" in container["linuxParameters"]["capabilities"]["drop"]
    assert "/health" in container["healthCheck"]["command"][-1]


def test_dedicated_workshop_controller_has_no_deployable_command(definition):
    definition["containerDefinitions"][0]["name"] = "workshop-runner"
    with pytest.raises(ValueError, match="No reviewed command"):
        render_task_definition(
            definition, service="workshop-runner", image="repo:new", cluster="test"
        )


@pytest.mark.parametrize("counter", ["desiredCount", "runningCount", "pendingCount"])
def test_first_cutover_refuses_active_consumers(monkeypatch, counter):
    from scripts import deploy_ecs

    monkeypatch.setattr(deploy_ecs, "describe_service", lambda cluster, name: {counter: 1})
    with pytest.raises(RuntimeError, match="drained, stopped"):
        deploy_ecs.require_drained_consumers("test")


def test_first_cutover_checks_every_consumer(monkeypatch):
    from scripts import deploy_ecs

    checked = []

    def stopped(cluster, name):
        checked.append(name)
        return {"desiredCount": 0, "runningCount": 0, "pendingCount": 0}

    monkeypatch.setattr(deploy_ecs, "describe_service", stopped)
    deploy_ecs.require_drained_consumers("test")
    assert set(checked) == {
        "api",
        "celery-control-worker",
        "celery-io-worker",
        "celery-batch-worker",
        "celery-landing-worker",
        "celery-interactive-worker",
        "celery-beat",
        "sandbox",
    }


def test_cutover_does_not_guess_service_capacity(monkeypatch):
    from scripts import deploy_ecs

    monkeypatch.setattr(deploy_ecs, "describe_service", lambda cluster, name: {"desiredCount": 0})
    with pytest.raises(ValueError, match="recorded service capacity"):
        deploy_ecs.deploy("test", "api", "repo:new")


@pytest.mark.parametrize("migrated", [False, True])
@pytest.mark.parametrize("desired", [0, 1])
def test_cutover_checks_deployed_release_and_not_desired_count(monkeypatch, migrated, desired):
    def service(cluster, name):
        return {"taskDefinition": name + ":1", "desiredCount": desired, "runningCount": 1}

    def aws(*args, **kwargs):
        name = args[-1].split(":")[0]
        environment = (
            [{"name": "WORKSHOP_EXECUTION_BACKEND", "value": "sandbox-package-v1"}]
            if migrated
            else []
        )
        return {
            "taskDefinition": {"containerDefinitions": [{"name": name, "environment": environment}]}
        }

    monkeypatch.setattr(deploy_ecs, "describe_service", service)
    monkeypatch.setattr(deploy_ecs, "aws", aws)
    if migrated:
        deploy_ecs.preflight("test")
    else:
        with pytest.raises(RuntimeError, match="drained, stopped"):
            deploy_ecs.preflight("test")
