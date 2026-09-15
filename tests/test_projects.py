"""Members are added to a project by ``email``; unknown emails go through the
``ProjectInvite`` resource (tests/test_project_invites.py)."""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Project, ProjectMembership, Subscription, SubscriptionStatus, User

pytestmark = pytest.mark.django_db


def _user(email: str, *, projects_limit: int = 5) -> User:
    return User.objects.create_user(
        email=email,
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        projects_limit=projects_limit,
    )


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def _project_payload(slug_suffix: str | None = None) -> dict:
    suf = slug_suffix or uuid.uuid4().hex[:8]
    return {
        "name": f"Project {suf}",
        "slug": f"proj-{suf}",
        "is_active": True,
        "settings": {},
    }


def test_list_projects_includes_only_memberships():
    u1 = _user("a@example.com")
    u2 = _user("b@example.com")
    p1 = Project.objects.create(name="One", slug=f"one-{uuid.uuid4().hex[:6]}")
    p2 = Project.objects.create(name="Two", slug=f"two-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=u1, project=p1)
    ProjectMembership.objects.create(user=u2, project=p2)

    r = _auth_client(u1).get(reverse("project-list"))
    assert r.status_code == 200
    ids = {str(x["id"]) for x in r.data["results"]}
    assert str(p1.id) in ids
    assert str(p2.id) not in ids


def test_create_project_creates_owner_membership():
    u = _user("owner@example.com")
    client = _auth_client(u)
    payload = _project_payload()
    r = client.post(reverse("project-list"), payload, format="json")
    assert r.status_code == 201
    pid = r.data["id"]
    assert ProjectMembership.objects.filter(user=u, project_id=pid).exists()


def test_account_scoped_api_key_can_create_project():
    from overbae.models import APIToken

    u = _user("acct@example.com")
    raw, _token = APIToken.create_for_user(u, name="account")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw)
    r = client.post(reverse("project-list"), _project_payload(), format="json")
    assert r.status_code == 201, r.data
    assert ProjectMembership.objects.filter(user=u, project_id=r.data["id"]).exists()


def test_project_scoped_api_key_cannot_create_project():
    from overbae.models import APIToken

    u = _user("proj-key@example.com")
    existing = Project.objects.create(name="Pinned", slug=f"pin-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=u, project=existing)
    raw, _token = APIToken.create_for_user(u, name="pinned", project=existing)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw)
    before = Project.objects.count()
    r = client.post(reverse("project-list"), _project_payload(), format="json")
    assert r.status_code == 403, r.data
    assert Project.objects.count() == before
    detail = str(r.data.get("detail") or "")
    assert "account-scoped" in detail.lower() or "project-scoped" in detail.lower()


def test_destroy_project():
    u = _user("del@example.com")
    project = Project.objects.create(name="Gone", slug=f"gone-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=u, project=project)

    r = _auth_client(u).delete(reverse("project-detail", kwargs={"id": project.id}))
    assert r.status_code == 204
    assert not Project.objects.filter(id=project.id).exists()


def test_create_project_rejects_when_user_at_projects_limit():
    u = _user("full@example.com", projects_limit=1)
    p0 = Project.objects.create(name="Existing", slug=f"ex-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=u, project=p0)

    r = _auth_client(u).post(reverse("project-list"), _project_payload(), format="json")
    assert r.status_code == 403
    detail = r.data.get("detail") or ""
    assert "Free plan" in detail or "Upgrade to Pro" in detail
    assert r.data.get("code") == "plan_limit_exceeded"


def test_retrieve_other_project_not_visible():
    u1 = _user("u1@example.com")
    u2 = _user("u2@example.com")
    p2 = Project.objects.create(name="Foreign", slug=f"fr-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=u2, project=p2)

    r = _auth_client(u1).get(reverse("project-detail", kwargs={"id": p2.id}))
    assert r.status_code == 404


def test_unauthenticated_requests_rejected():
    r = APIClient().get(reverse("project-list"))
    assert r.status_code == 401


def test_list_memberships_for_project():
    owner = _user("owner2@example.com")
    peer = _user("peer@example.com")
    project = Project.objects.create(name="Team", slug=f"team-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=owner, project=project)
    ProjectMembership.objects.create(user=peer, project=project)

    r = _auth_client(owner).get(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
    )
    assert r.status_code == 200
    emails = {row["user_email"] for row in r.data["results"]}
    assert owner.email in emails
    assert peer.email in emails


def test_add_member_by_email():
    owner = _user("lead@example.com")
    Subscription.objects.update_or_create(
        user=owner,
        defaults={"status": SubscriptionStatus.ACTIVE, "stripe_subscription_id": "sub_lead"},
    )
    owner.projects_limit = None
    owner.save(update_fields=["projects_limit"])
    invitee = _user("joiner@example.com", projects_limit=5)
    project = Project.objects.create(name="Collab", slug=f"col-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=owner, project=project)

    r = _auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": invitee.email},
        format="json",
    )
    assert r.status_code == 201
    assert ProjectMembership.objects.filter(project=project, user=invitee).exists()


def test_add_member_rejects_when_invitee_at_projects_limit():
    owner = _user("lead2@example.com")
    Subscription.objects.update_or_create(
        user=owner,
        defaults={"status": SubscriptionStatus.ACTIVE, "stripe_subscription_id": "sub_lead2"},
    )
    owner.projects_limit = None
    owner.save(update_fields=["projects_limit"])
    invitee = _user("saturated@example.com", projects_limit=1)
    p_other = Project.objects.create(name="Other", slug=f"oth-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=invitee, project=p_other)

    project = Project.objects.create(name="New", slug=f"new-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=owner, project=project)

    r = _auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": invitee.email},
        format="json",
    )
    assert r.status_code == 403
    detail = str(r.data.get("detail") or "")
    assert "Free plan" in detail or "Upgrade to Pro" in detail
    assert r.data.get("code") == "plan_limit_exceeded"


def test_add_member_rejects_when_project_member_cap_reached():
    """Free actors cannot invite — seat limit is 1 (owner only)."""
    owner = _user("captest@example.com", projects_limit=5)
    project = Project.objects.create(name="Cap", slug=f"cap-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=owner, project=project)

    newcomer = _user("newcomer@example.com")
    r = _auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": newcomer.email},
        format="json",
    )
    assert r.status_code == 403
    assert r.data.get("code") == "seat_limit_exceeded"


def test_add_member_idempotent_for_existing_member():
    owner = _user("idemp@example.com")
    member = _user("already@example.com", projects_limit=5)
    project = Project.objects.create(name="Same", slug=f"same-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=owner, project=project)
    ProjectMembership.objects.create(user=member, project=project)

    r = _auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": member.email},
        format="json",
    )
    assert r.status_code == 201
    assert ProjectMembership.objects.filter(project=project, user=member).count() == 1


def test_add_member_unknown_email():
    owner = _user("badmail@example.com")
    project = Project.objects.create(name="Bad", slug=f"bad-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=owner, project=project)

    r = _auth_client(owner).post(
        reverse("project-membership-list", kwargs={"project_id": project.id}),
        {"email": "nobody@example.com"},
        format="json",
    )
    assert r.status_code == 400
    assert r.data.get("code") == "user_not_found"
    assert "No user found" in r.data.get("detail", "")


def test_remove_membership():
    owner = _user("rm@example.com")
    peer = _user("gone@example.com")
    project = Project.objects.create(name="Rm", slug=f"rm-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=owner, project=project)
    mem = ProjectMembership.objects.create(user=peer, project=project)

    r = _auth_client(owner).delete(
        reverse(
            "project-membership-detail",
            kwargs={"project_id": project.id, "id": mem.id},
        ),
    )
    assert r.status_code == 204
    assert not ProjectMembership.objects.filter(id=mem.id).exists()


def test_cannot_remove_own_membership():
    owner = _user("selfrm@example.com")
    project = Project.objects.create(name="SelfRm", slug=f"srm-{uuid.uuid4().hex[:6]}")
    mem = ProjectMembership.objects.create(user=owner, project=project)

    r = _auth_client(owner).delete(
        reverse(
            "project-membership-detail",
            kwargs={"project_id": project.id, "id": mem.id},
        ),
    )
    assert r.status_code == 403
    assert ProjectMembership.objects.filter(id=mem.id).exists()


def test_partial_update_project():
    u = _user("patch@example.com")
    project = Project.objects.create(name="Old", slug=f"old-{uuid.uuid4().hex[:6]}")
    ProjectMembership.objects.create(user=u, project=project)

    r = _auth_client(u).patch(
        reverse("project-detail", kwargs={"id": project.id}),
        {"name": "Updated"},
        format="json",
    )
    assert r.status_code == 200
    assert r.data["name"] == "Updated"
