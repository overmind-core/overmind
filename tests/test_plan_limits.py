from __future__ import annotations

import uuid

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.urls import reverse
from factories import auth_client, make_project, make_user

from overbae.models import (
    DeployedModel,
    FinetuningJob,
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
    u = make_user()
    assert u.projects_limit == 5
    assert effective_projects_limit(u) == 5
    assert not is_pro(u)


def test_past_due_is_not_pro_and_gets_free_project_cap():
    u = make_user()
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
    u = make_user()
    u.projects_limit = 1
    u.save(update_fields=["projects_limit"])
    assert effective_projects_limit(u) == 1


def test_pro_has_unlimited_projects():
    u = make_user()
    _make_pro(u)
    assert is_pro(u)
    assert effective_projects_limit(u) is None


def test_free_cannot_invite_second_member():
    owner = make_user("owner-seat@example.com")
    project = make_project(member=owner)
    invitee = make_user("invitee-seat@example.com")

    with pytest.raises(SeatLimitExceeded):
        require_seat_for_invite(owner, project)

    r = auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": invitee.email},
        format="json",
    )
    assert r.status_code == 403
    assert r.data.get("code") == "seat_limit_exceeded"
    assert not ProjectMembership.objects.filter(project=project, user=invitee).exists()


def test_pro_can_invite_members():
    owner = make_user("pro-owner@example.com")
    _make_pro(owner)
    project = make_project(member=owner)
    invitee = make_user("pro-invitee@example.com")

    require_seat_for_invite(owner, project)
    r = auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": invitee.email},
        format="json",
    )
    assert r.status_code == 201
    assert ProjectMembership.objects.filter(project=project, user=invitee).exists()


def test_training_quota_blocks_free_at_cap():
    user = make_user()
    project = make_project(member=user)
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
    user = make_user()
    _make_pro(user)
    project = make_project(member=user)
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
    user = make_user()
    project = make_project(member=user)
    DeployedModel.objects.create(
        project=project,
        model_id=f"base-{uuid.uuid4().hex[:8]}",
        finetuning_job=None,
        status=DeployedModel.Status.READY,
    )
    assert usage_count(user, "deploy_jobs") == 0


def test_finetuned_deploy_counts_toward_deploy_quota():
    user = make_user()
    project = make_project(member=user)
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


def test_register_finetuned_skips_deploy_when_over_cap(fake_modal):
    user = make_user()
    project = make_project(member=user)
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
    assert fake_modal.log == []


def test_subscription_payload_includes_usage():
    user = make_user()
    r = auth_client(user).get(reverse("billing-subscription"))
    assert r.status_code == 200
    usage = r.data["usage"]
    assert usage["training_jobs"]["limit"] == FREE_LIMITS["training_jobs"]
    assert usage["seats"]["limit"] == FREE_LIMITS["seats"]
    assert usage["projects"]["limit"] == FREE_LIMITS["projects"]
    assert "eval_runs" not in usage
