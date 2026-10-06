"""A release swaps the image and nothing else; cloud-platform owns every other field.

Failure modes this file owns:
- a release overwrites infrastructure-owned fields (command, environment, health check);
- a release changes a sidecar image;
- a migration task starts the web server;
- a release reports ready before the new revision is running and healthy;
- a release starts producers while a lane it routes to has no provisioned service;
- producers deploy before the consumers and the scheduler they depend on.
"""

import copy
from pathlib import Path

import pytest
import yaml

from overbae.lanes import LANES, service
from scripts.deploy_ecs import (
    absent_services,
    lane_services,
    migration_request,
    render_task_definition,
    service_ready,
)

REPO = "overmind-prod-app"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deploy-api.yml"


@pytest.fixture
def definition():
    return {
        "family": "celery-landing-worker",
        "revision": 6,
        "taskDefinitionArn": "arn:aws:ecs:eu-west-1:123:task-definition/celery-landing-worker:6",
        "status": "ACTIVE",
        "requiresAttributes": [],
        "compatibilities": ["FARGATE"],
        "registeredAt": "2026-10-06T00:00:00Z",
        "cpu": "4096",
        "memory": "8192",
        "networkMode": "awsvpc",
        "taskRoleArn": "arn:aws:iam::123:role/app",
        "containerDefinitions": [
            {
                "name": "celery-landing-worker",
                "image": f"123.dkr.ecr.eu-west-1.amazonaws.com/{REPO}:sha-old",
                "entryPoint": ["/usr/local/bin/worker-entrypoint.sh"],
                "command": ["python", "-m", "overbae.lanes", "landing"],
                "environment": [{"name": "KEEP", "value": "yes"}],
                "healthCheck": {
                    "command": ["CMD", "python", "-m", "overbae.worker_health", "landing"]
                },
                "essential": True,
            },
            {"name": "otel", "image": "otel:stable", "command": ["collector"], "essential": False},
        ],
    }


@pytest.fixture
def service_description():
    return {
        "serviceName": "api",
        "launchType": "FARGATE",
        "platformVersion": "LATEST",
        "networkConfiguration": {
            "awsvpcConfiguration": {"subnets": ["subnet-a"], "securityGroups": ["sg-a"]}
        },
    }


def test_release_changes_only_the_app_image(definition):
    original = copy.deepcopy(definition)
    image = f"123.dkr.ecr.eu-west-1.amazonaws.com/{REPO}:sha-new"
    rendered = render_task_definition(definition, image=image, repository=REPO)
    app, sidecar = rendered["containerDefinitions"]
    assert app == {**original["containerDefinitions"][0], "image": image}
    assert sidecar == original["containerDefinitions"][1]
    assert "revision" not in rendered and "registeredAt" not in rendered
    assert definition == original


def test_release_refuses_a_definition_without_the_app_image(definition):
    definition["containerDefinitions"][0]["image"] = "elsewhere:latest"
    with pytest.raises(ValueError, match=REPO):
        render_task_definition(definition, image="new", repository=REPO)


def test_migration_cannot_start_http_server(definition, service_description):
    definition["containerDefinitions"][0]["name"] = "api"
    request = migration_request(
        definition, service_description, task_definition="api:7", cluster="test-cluster"
    )
    override = request["overrides"]["containerOverrides"][0]
    assert override["command"] == ["python", "manage.py", "migrate", "--noinput"]
    assert {"name": "RUN_DB_BOOTSTRAP", "value": "0"} in override["environment"]
    assert request["networkConfiguration"] == service_description["networkConfiguration"]


def test_migration_selects_api_even_when_an_essential_sidecar_is_first(
    definition, service_description
):
    app = definition["containerDefinitions"][0]
    app["name"] = "api"
    sidecar = {"name": "essential-sidecar", "image": "sidecar:stable", "essential": True}
    definition["containerDefinitions"] = [sidecar, app]
    request = migration_request(
        definition, service_description, task_definition="api:7", cluster="test-cluster"
    )
    assert [item["name"] for item in request["overrides"]["containerOverrides"]] == ["api"]


def test_migration_refuses_a_task_definition_without_the_api_container(
    definition, service_description
):
    with pytest.raises(ValueError, match="api"):
        migration_request(
            definition, service_description, task_definition="wrong:7", cluster="test-cluster"
        )


def test_ready_means_the_new_revision_runs_and_passes_its_own_health_check():
    service_state = {
        "desiredCount": 1,
        "runningCount": 1,
        "pendingCount": 0,
        "deployments": [
            {"status": "PRIMARY", "taskDefinition": "new:7", "rolloutState": "COMPLETED"}
        ],
    }
    task = {"lastStatus": "RUNNING", "taskDefinitionArn": "new:7", "healthStatus": "HEALTHY"}
    checked = {"task_definition": "new:7", "health_checked": True}
    assert service_ready(service_state, [task], **checked)
    assert not service_ready(service_state, [{**task, "healthStatus": "UNKNOWN"}], **checked)
    assert not service_ready(service_state, [{**task, "taskDefinitionArn": "old:6"}], **checked)
    assert not service_ready({**service_state, "desiredCount": 0}, [], **checked)
    unchecked = {"task_definition": "new:7", "health_checked": False}
    assert service_ready(service_state, [{**task, "healthStatus": "UNKNOWN"}], **unchecked)


def test_every_lane_is_deployed_and_must_be_provisioned_first():
    assert lane_services() == [service(lane) for lane in LANES]
    found = [{"serviceName": name} for name in ["api", "celery-beat", *lane_services()[1:]]]
    assert absent_services(found) == [lane_services()[0]]


def test_consumers_deploy_before_scheduler_and_scheduler_before_api():
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]

    def before(name):
        needs = jobs[name].get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        return set(needs).union(*(before(n) for n in needs))

    assert "preflight" in before("migrate")
    assert "migrate" in before("deploy-workers")
    assert "deploy-workers" in before("deploy-beat")
    assert "deploy-beat" in before("deploy-api")
    matrix = jobs["deploy-workers"]["strategy"]["matrix"]["service"]
    assert matrix == "${{ fromJSON(needs.preflight.outputs.lanes) }}"
