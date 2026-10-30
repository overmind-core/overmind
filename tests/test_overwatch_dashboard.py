from __future__ import annotations

from datetime import timedelta

import pytest
from django.apps import apps
from django.db import connection
from django.utils import timezone
from factories import (
    api_key_client,
    auth_client,
    make_capability,
    make_project,
    make_span,
    make_user,
)
from rest_framework.test import APIClient

from overbae.models import (
    Dataset,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    ProjectMembership,
    User,
)

pytestmark = pytest.mark.django_db

TOKEN = "overwatch-test-token"


@pytest.fixture(autouse=True)
def _token(settings):
    settings.SALES_DASHBOARD_TOKEN = TOKEN


def overwatch_client(token: str = TOKEN) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.fixture
def accounts():
    guest = make_user(email="guest@example.com", is_guest=True)
    guest_project = make_project(member=guest)
    make_span(guest_project, trace_id="a" * 32)

    active = make_user(email="active@example.com")
    project = make_project(member=active)
    make_span(project, trace_id="b" * 32)
    make_span(project, trace_id="b" * 32, parent_span_id="c" * 16)
    capability = make_capability(project)
    dataset = Dataset.objects.create(project=project, name="Train", intent="train")
    EvalRun.objects.create(project=project, name="Eval", status=EvalRun.Status.COMPLETED)
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-4B",
        status=FinetuningJob.Status.SUCCEEDED,
    )
    deployed = DeployedModel.objects.create(
        project=project,
        finetuning_job=job,
        model_id="ft-qwen-1",
        status=DeployedModel.Status.READY,
        deployed_at=timezone.now(),
    )
    capability.active_model = deployed
    capability.first_application_request_at = timezone.now()
    capability.save(update_fields=["active_model", "first_application_request_at"])
    paying = make_user(email="paying@example.com")

    old = make_user(email="old@example.com")
    User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=60))
    return {"active": active, "paying": paying, "old": old}


@pytest.mark.parametrize("path", ["overview", "activation", "users", "users/{id}"])
def test_rejects_anything_but_the_configured_token(settings, path):
    user = make_user()
    url = f"/api/internal/overwatch/{path.format(id=user.id)}/"

    assert APIClient().get(url).status_code == 403
    assert overwatch_client("wrong").get(url).status_code == 403
    assert auth_client(user).get(url).status_code == 403
    assert api_key_client(user).get(url).status_code == 403
    assert overwatch_client().get(url).status_code == 200

    settings.SALES_DASHBOARD_TOKEN = ""
    assert overwatch_client("").get(url).status_code == 403


def test_unicode_bearer_token_is_rejected_safely():
    assert overwatch_client("☃").get("/api/internal/overwatch/overview/").status_code == 403


@pytest.mark.parametrize("path", ["overview", "activation", "users", "users/{id}", "product-usage"])
def test_product_analytics_endpoints_only_read_the_database(accounts, path):
    url = f"/api/internal/overwatch/{path.format(id=accounts['active'].pk)}/"

    def only_reads(execute, sql, params, many, context):
        assert (
            sql.lstrip().upper().startswith(("SELECT", "SHOW", "SAVEPOINT", "RELEASE", "ROLLBACK"))
        ), "An Overwatch analytics request attempted a database write."
        return execute(sql, params, many, context)

    client = overwatch_client()
    with connection.execute_wrapper(only_reads):
        assert client.get(url).status_code == 200
        for method in (client.post, client.patch, client.delete):
            assert method(url).status_code == 405


def test_overwatch_has_no_product_models_or_periodic_writers(settings):
    assert not any(model.__name__.startswith("Overwatch") for model in apps.get_models())
    assert not any(
        schedule["task"].startswith("overbae.tasks.overwatch.")
        for schedule in settings.CELERY_BEAT_SCHEDULE.values()
    )


def test_dashboard_credential_cannot_create_a_core_product_project():
    response = overwatch_client().post(
        "/api/projects/", {"name": "Overwatch must not create this"}, format="json"
    )
    assert response.status_code in {401, 403}
    assert not ProjectMembership.objects.filter(
        project__name="Overwatch must not create this"
    ).exists()


@pytest.fixture(autouse=True)
def _sandbox(settings):
    # These tests never call the parser sandbox; outbound requests remain guarded.
    settings.SANDBOX_URL = "http://sandbox.test"
    settings.SANDBOX_TOKEN = "analytics-test"


def test_posthog_credentials_cannot_be_sent_to_unapproved_host(settings, scripted):
    settings.POSTHOG_PERSONAL_API_KEY = "posthog-read-test"
    settings.POSTHOG_PROJECT_ID = "42"
    settings.POSTHOG_API_HOST = "https://attacker.test"
    attacker = scripted("https://attacker.test").reply(json_body={"results": []})
    response = overwatch_client().get("/api/internal/overwatch/product-usage/")
    assert response.json()["available"] is False
    assert attacker.calls == []
