"""A release must update worker commands and prove the new consumer is healthy."""

import copy

import pytest

from scripts.deploy_ecs import migration_request, render_task_definition, service_ready
from scripts.plan_landing_capacity import capacity_plan


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


def test_bootstrap_isolated_worker_preserves_storage_and_secrets(definition, service):
    original = copy.deepcopy(definition)
    plan = capacity_plan(
        definition,
        service,
        image="repo:new",
        cluster="test-cluster",
        min_capacity=1,
        max_capacity=4,
    )
    landing = plan["task-definition.json"]
    container = landing["containerDefinitions"][0]
    assert landing["family"] == "celery-landing-worker"
    assert container["name"] == "celery-landing-worker"
    assert container["command"][-2:] == ["-Q", "landing"]
    assert "--pool=prefork" in container["command"]
    assert "--disable-prefetch" in container["command"]
    assert container["secrets"] == original["containerDefinitions"][0]["secrets"]
    assert container["mountPoints"] == original["containerDefinitions"][0]["mountPoints"]
    assert landing["volumes"] == original["volumes"]
    assert landing["containerDefinitions"][1] == original["containerDefinitions"][1]
    assert "revision" not in landing and "taskDefinitionArn" not in landing
    assert plan["create-service.json"]["networkConfiguration"] == service["networkConfiguration"]
    assert definition == original


def test_every_release_overrides_stale_worker_command(definition):
    definition["family"] = "celery-landing-worker"
    definition["containerDefinitions"][0]["name"] = "celery-landing-worker"
    rendered = render_task_definition(
        definition, service="celery-landing-worker", image="repo:new", cluster="test-cluster"
    )
    container = rendered["containerDefinitions"][0]
    assert container["image"] == "repo:new"
    assert container["command"][-2:] == ["-Q", "landing"]
    assert container["entryPoint"] == ["/usr/local/bin/worker-entrypoint.sh"]
    assert "overbae.worker_health" in " ".join(container["healthCheck"]["command"])
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


def test_capacity_plan_has_hard_bounds_and_actionable_missing_metrics_alarm(definition, service):
    plan = capacity_plan(
        definition,
        service,
        image="repo:new",
        cluster="test-cluster",
        min_capacity=1,
        max_capacity=4,
    )
    target = plan["scalable-target.json"]
    assert (target["MinCapacity"], target["MaxCapacity"]) == (1, 4)
    policy = plan["backlog-policy.json"]["TargetTrackingScalingPolicyConfiguration"]
    assert policy["CustomizedMetricSpecification"]["MetricName"] == "BacklogPerWorker"
    assert policy["DisableScaleIn"] is True
    alarms = plan["alarms.json"]
    assert any(a.get("MetricName") == "OldestQueuedAgeSeconds" for a in alarms)
    assert any(
        a.get("MetricName") == "MetricHeartbeat" and a["TreatMissingData"] == "breaching"
        for a in alarms
    )
    assert any(a.get("MetricName") == "BlockedImports" for a in alarms)
    metric_permission = plan["metrics-role-policy.json"]["Statement"][0]
    assert metric_permission["Action"] == ["cloudwatch:PutMetricData"]
    assert (
        metric_permission["Condition"]["StringEquals"]["cloudwatch:namespace"] == "Overmind/Queues"
    )


def test_invalid_or_zero_warm_capacity_is_rejected(definition, service):
    for minimum, maximum in [(0, 4), (4, 3), (1, 0)]:
        with pytest.raises(ValueError):
            capacity_plan(
                definition,
                service,
                image="repo:new",
                cluster="test-cluster",
                min_capacity=minimum,
                max_capacity=maximum,
            )


def test_deploy_iam_grant_is_limited_to_migrations_and_task_inspection(definition, service):
    plan = capacity_plan(
        definition,
        service,
        image="repo:new",
        cluster="test-cluster",
        min_capacity=1,
        max_capacity=4,
    )
    statements = plan["deploy-role-policy.json"]["Statement"]
    migrate = next(row for row in statements if row["Action"] == ["ecs:RunTask"])
    assert migrate["Resource"].endswith(":task-definition/api:*")
    assert migrate["Condition"]["ArnEquals"]["ecs:cluster"].endswith(":cluster/test-cluster")
    assert not any("iam:PassRole" in row["Action"] for row in statements)


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
