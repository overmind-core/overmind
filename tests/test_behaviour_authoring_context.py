"""Task-scoped eval authoring: the Behaviour authoring-context endpoint and
its anchor-segment/step validation helpers."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    Behaviour,
    BehaviourVersion,
    Capability,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.behaviour.contract import anchor_segment_valid, available_steps

pytestmark = pytest.mark.django_db

SHA = "a" * 40

CONTRACT = {
    "key": "happy",
    "claim": "code_path",
    "entry_anchor": "m.run",
    "anchor_sequence": ["m.run", "m.fetch", "m.finish"],
    "anchors": [
        {"qualname": q, "kind": "function", "file": "app/agent.py#L1-L10"}
        for q in ["m.run", "m.fetch", "m.finish"]
    ],
    "steps": [
        {"step": "Fetch data", "kind": "agent_step", "anchors": ["m.fetch"]},
        {"step": "Wrap up", "kind": "agent_step", "anchors": ["m.finish"]},
    ],
    "tool_set": [{"name": "fetch", "declared_name": "Fetch", "purpose": "", "side_effect": ""}],
    "terminal": {"kind": "emits_record", "description": ""},
}


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _capability(project) -> Capability:
    return Capability.objects.create(project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}")


def _behaviour(capability) -> Behaviour:
    behaviour = Behaviour.objects.create(
        project=capability.project,
        capability=capability,
        key="happy",
        display_name="Happy path",
        entry_anchor="m.run",
        first_seen_sha=SHA,
        last_seen_sha=SHA,
    )
    BehaviourVersion.objects.create(behaviour=behaviour, analyzed_sha=SHA, contract=dict(CONTRACT))
    return behaviour


def _client_for(project) -> APIClient:
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def test_available_steps_mirrors_card_compiler_backbone_steps():
    assert available_steps(CONTRACT) == CONTRACT["steps"]


def test_anchor_segment_valid_checks_declared_order():
    assert anchor_segment_valid(["m.run", "m.finish"], CONTRACT)
    assert not anchor_segment_valid(["m.finish", "m.run"], CONTRACT)
    assert not anchor_segment_valid(["m.nope"], CONTRACT)
    assert anchor_segment_valid([], CONTRACT)


def test_authoring_context_returns_the_behaviours_contract():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability)
    client = _client_for(project)

    body = client.get(f"/api/behaviours/{behaviour.id}/authoring-context/").json()

    assert body["behaviour_key"] == "happy"
    assert body["contract"]["anchor_sequence"] == CONTRACT["anchor_sequence"]
    assert [s["step"] for s in body["contract"]["steps"]] == ["Fetch data", "Wrap up"]


def test_authoring_context_with_no_analyzed_version_returns_null_contract():
    project = _project()
    capability = _capability(project)
    behaviour = Behaviour.objects.create(
        project=capability.project,
        capability=capability,
        key="unanalyzed",
        display_name="Unanalyzed",
        first_seen_sha=SHA,
        last_seen_sha=SHA,
    )
    client = _client_for(project)

    body = client.get(f"/api/behaviours/{behaviour.id}/authoring-context/").json()

    assert body["contract"] is None
