from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    DeployedModel,
    FinetuningJob,
    Project,
    ProjectMembership,
    Subscription,
    SubscriptionStatus,
    User,
)
from overbae.services.plan_limits import (
    FREE_LIMITS,
    PlanLimitExceeded,
    SeatLimitExceeded,
    effective_projects_limit,
    is_pro,
    require_plan_quota,
    require_seat_for_invite,
    usage_count,
)
from overbae.tasks.model_deployment import register_finetuned_model

pytestmark = pytest.mark.django_db


def _user(email: str | None = None, **extra) -> User:
    return User.objects.create_user(
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        **extra,
    )


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def _project(user: User) -> Project:
    project = Project.objects.create(
        name=f"P-{uuid.uuid4().hex[:6]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
    )
    ProjectMembership.objects.create(user=user, project=project)
    return project


def _make_pro(user: User) -> None:
    Subscription.objects.update_or_create(
        user=user,
        defaults={
            "status": SubscriptionStatus.ACTIVE,
            "stripe_subscription_id": f"sub_{uuid.uuid4().hex[:10]}",
        },
    )
    user.projects_limit = None
    user.save(update_fields=["projects_limit"])


def test_new_free_user_has_projects_limit_5():
    u = _user()
    assert u.projects_limit == 5
    assert effective_projects_limit(u) == 5
    assert not is_pro(u)


def test_past_due_is_not_pro_and_gets_free_project_cap():
    u = _user()
    u.projects_limit = None
    u.save(update_fields=["projects_limit"])
    Subscription.objects.create(
        user=u,
        status=SubscriptionStatus.PAST_DUE,
        stripe_subscription_id="sub_past_due",
    )
    assert not is_pro(u)
    assert effective_projects_limit(u) == FREE_LIMITS["projects"]


def test_free_with_explicit_lower_projects_limit_honored():
    u = _user()
    u.projects_limit = 1
    u.save(update_fields=["projects_limit"])
    assert effective_projects_limit(u) == 1


def test_pro_has_unlimited_projects():
    u = _user()
    _make_pro(u)
    assert is_pro(u)
    assert effective_projects_limit(u) is None


def test_free_cannot_invite_second_member():
    owner = _user("owner-seat@example.com")
    project = _project(owner)
    invitee = _user("invitee-seat@example.com")

    with pytest.raises(SeatLimitExceeded):
        require_seat_for_invite(owner, project)

    r = _auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": invitee.email},
        format="json",
    )
    assert r.status_code == 403
    assert r.data.get("code") == "seat_limit_exceeded"
    assert not ProjectMembership.objects.filter(project=project, user=invitee).exists()


def test_pro_can_invite_members():
    owner = _user("pro-owner@example.com")
    _make_pro(owner)
    project = _project(owner)
    invitee = _user("pro-invitee@example.com")

    require_seat_for_invite(owner, project)
    r = _auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": invitee.email},
        format="json",
    )
    assert r.status_code == 201
    assert ProjectMembership.objects.filter(project=project, user=invitee).exists()


def test_training_quota_blocks_free_at_cap():
    user = _user()
    project = _project(user)
    dataset = frozen_dataset(project, TRAIN_ROWS, name="ds")
    for i in range(FREE_LIMITS["training_jobs"]):
        FinetuningJob.objects.create(
            project=project,
            dataset=dataset,
            base_model="Qwen/Qwen3-8B",
            triggered_by=user,
            name=f"job-{i}",
        )
    assert usage_count(user, "training_jobs") == FREE_LIMITS["training_jobs"]
    with pytest.raises(PlanLimitExceeded):
        require_plan_quota(user, "training_jobs")


def test_pro_ignores_training_cap():
    user = _user()
    _make_pro(user)
    project = _project(user)
    dataset = frozen_dataset(project, TRAIN_ROWS, name="ds")
    for i in range(FREE_LIMITS["training_jobs"] + 3):
        FinetuningJob.objects.create(
            project=project,
            dataset=dataset,
            base_model="Qwen/Qwen3-8B",
            triggered_by=user,
            name=f"job-{i}",
        )
    require_plan_quota(user, "training_jobs")


def test_base_model_deploy_does_not_count_toward_deploy_quota():
    user = _user()
    project = _project(user)
    DeployedModel.objects.create(
        project=project,
        model_id=f"base-{uuid.uuid4().hex[:8]}",
        finetuning_job=None,
        status=DeployedModel.Status.READY,
    )
    assert usage_count(user, "deploy_jobs") == 0


def test_finetuned_deploy_counts_toward_deploy_quota():
    user = _user()
    project = _project(user)
    dataset = frozen_dataset(project, TRAIN_ROWS, name="ds")
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-8B",
        triggered_by=user,
        name="ft",
        remote_job_id="bt:1",
        status=FinetuningJob.Status.DEPLOYING,
    )
    DeployedModel.objects.create(
        project=project,
        finetuning_job=job,
        model_id=f"ft-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.QUEUED,
    )
    assert usage_count(user, "deploy_jobs") == 1


@patch("overbae.tasks.model_deployment.modal", create=True)
def test_register_finetuned_skips_deploy_when_over_cap(_modal):
    user = _user()
    project = _project(user)
    dataset = frozen_dataset(project, TRAIN_ROWS, name="ds")

    for i in range(FREE_LIMITS["deploy_jobs"]):
        j = FinetuningJob.objects.create(
            project=project,
            dataset=dataset,
            base_model="Qwen/Qwen3-8B",
            triggered_by=user,
            name=f"prior-{i}",
            remote_job_id=f"bt:prior-{i}",
            status=FinetuningJob.Status.SUCCEEDED,
        )
        DeployedModel.objects.create(
            project=project,
            finetuning_job=j,
            model_id=f"prior-ft-{uuid.uuid4().hex[:8]}",
            status=DeployedModel.Status.READY,
        )

    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-8B",
        triggered_by=user,
        name="blocked-deploy",
        remote_job_id="bt:blocked",
        status=FinetuningJob.Status.DEPLOYING,
    )

    register_finetuned_model.run(job_id=str(job.id))

    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.SUCCEEDED
    assert "deploy limit" in job.error_message.lower()
    assert not DeployedModel.objects.filter(finetuning_job=job).exists()


def test_subscription_payload_includes_usage():
    user = _user()
    r = _auth_client(user).get(reverse("billing-subscription"))
    assert r.status_code == 200
    usage = r.data["usage"]
    assert usage["training_jobs"]["limit"] == FREE_LIMITS["training_jobs"]
    assert usage["seats"]["limit"] == FREE_LIMITS["seats"]
    assert usage["projects"]["limit"] == FREE_LIMITS["projects"]
    assert "eval_runs" not in usage
