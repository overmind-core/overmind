"""
Integration tests for the agents API.

Covers:
- GET /api/v1/agents/           → only active prompts returned; pending_version badge
- GET /api/v1/agents/{slug}     → active version, pending version, all version list
- POST /api/v1/agents/{slug}/accept-version → promotes pending to active, supersedes old
- POST /api/v1/agents/{slug}/metadata       → display-name + tag update/validation
"""

import hashlib

import pytest
from sqlalchemy import select

from overmind.models.prompts import (
    Prompt,
    PROMPT_STATUS_ACTIVE,
    PROMPT_STATUS_PENDING,
    PROMPT_STATUS_REJECTED,
    PROMPT_STATUS_SUPERSEDED,
)
from overmind.models.suggestions import Suggestion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _create_prompt(
    db_session, project_id, user_id, slug, version, status, text=None
):
    text = text or f"You are agent {slug} v{version}."
    p = Prompt(
        slug=slug,
        hash=_hash(text),
        prompt=text,
        display_name=f"{slug} v{version}",
        user_id=user_id,
        project_id=project_id,
        version=version,
        status=status,
    )
    db_session.add(p)
    await db_session.flush()
    return p


# ---------------------------------------------------------------------------
# GET /api/v1/agents/  — list endpoint
# ---------------------------------------------------------------------------


class TestListAgents:
    async def test_returns_empty_when_no_prompts(
        self, seed_user, test_client, auth_headers
    ):
        project = seed_user.project
        resp = await test_client.get(
            f"/api/v1/agents/?project_id={project.project_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_only_active_prompts_are_returned(
        self, seed_user, test_client, auth_headers, db_session
    ):
        """Pending and superseded versions must NOT appear as top-level agents."""
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(
            db_session, pid, uid, "my-agent", 1, PROMPT_STATUS_SUPERSEDED
        )
        await _create_prompt(db_session, pid, uid, "my-agent", 2, PROMPT_STATUS_ACTIVE)
        await _create_prompt(db_session, pid, uid, "my-agent", 3, PROMPT_STATUS_PENDING)
        await db_session.commit()

        resp = await test_client.get(
            f"/api/v1/agents/?project_id={pid}", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["version"] == 2
        assert data[0]["slug"] == "my-agent"

    async def test_pending_version_badge_shown_when_pending_exists(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(db_session, pid, uid, "chatbot", 1, PROMPT_STATUS_ACTIVE)
        await _create_prompt(db_session, pid, uid, "chatbot", 2, PROMPT_STATUS_PENDING)
        await db_session.commit()

        resp = await test_client.get(
            f"/api/v1/agents/?project_id={pid}", headers=auth_headers
        )
        assert resp.status_code == 200
        agents = resp.json()["data"]
        assert len(agents) == 1
        assert agents[0]["latest_version"] == 2

    async def test_no_pending_version_badge_when_only_active(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(db_session, pid, uid, "solo", 1, PROMPT_STATUS_ACTIVE)
        await db_session.commit()

        resp = await test_client.get(
            f"/api/v1/agents/?project_id={pid}", headers=auth_headers
        )
        assert resp.status_code == 200
        agents = resp.json()["data"]
        assert agents[0]["latest_version"] is None

    async def test_multiple_agents_each_show_active_version(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(db_session, pid, uid, "agent-a", 1, PROMPT_STATUS_ACTIVE)
        await _create_prompt(db_session, pid, uid, "agent-b", 3, PROMPT_STATUS_ACTIVE)
        await db_session.commit()

        resp = await test_client.get(
            f"/api/v1/agents/?project_id={pid}", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        by_slug = {a["slug"]: a for a in data}
        assert by_slug["agent-a"]["version"] == 1
        assert by_slug["agent-b"]["version"] == 3


# ---------------------------------------------------------------------------
# GET /api/v1/agents/{slug}  — detail endpoint
# ---------------------------------------------------------------------------


class TestAgentDetail:
    async def test_returns_active_version_details(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(
            db_session, pid, uid, "detail-agent", 1, PROMPT_STATUS_SUPERSEDED
        )
        await _create_prompt(
            db_session, pid, uid, "detail-agent", 2, PROMPT_STATUS_ACTIVE
        )
        await db_session.commit()

        resp = await test_client.get(
            f"/api/v1/agents/detail-agent/detail?project_id={pid}", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_version"] == 2
        assert data["slug"] == "detail-agent"

    async def test_pending_version_exposed_in_detail(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(
            db_session, pid, uid, "pending-agent", 1, PROMPT_STATUS_ACTIVE
        )
        await _create_prompt(
            db_session, pid, uid, "pending-agent", 2, PROMPT_STATUS_PENDING
        )
        await db_session.commit()

        resp = await test_client.get(
            f"/api/v1/agents/pending-agent/detail?project_id={pid}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pending_version"] == 2

    async def test_versions_list_includes_all_statuses(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(
            db_session, pid, uid, "multi-v", 1, PROMPT_STATUS_SUPERSEDED
        )
        await _create_prompt(db_session, pid, uid, "multi-v", 2, PROMPT_STATUS_ACTIVE)
        await _create_prompt(db_session, pid, uid, "multi-v", 3, PROMPT_STATUS_PENDING)
        await db_session.commit()

        resp = await test_client.get(
            f"/api/v1/agents/multi-v/detail?project_id={pid}", headers=auth_headers
        )
        assert resp.status_code == 200
        versions = resp.json()["versions"]
        assert len(versions) == 3
        statuses = {v["version"]: v["status"] for v in versions}
        assert statuses[1] == PROMPT_STATUS_SUPERSEDED
        assert statuses[2] == PROMPT_STATUS_ACTIVE
        assert statuses[3] == PROMPT_STATUS_PENDING

    async def test_returns_404_for_unknown_slug(
        self, seed_user, test_client, auth_headers
    ):
        pid = seed_user.project.project_id
        resp = await test_client.get(
            f"/api/v1/agents/does-not-exist/detail?project_id={pid}",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_rejected_max_version_not_shown_as_pending(
        self, seed_user, test_client, auth_headers, db_session
    ):
        """A rejected version must not surface as pending_version even if it is the max."""
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(db_session, pid, uid, "rej-check", 1, PROMPT_STATUS_ACTIVE)
        await _create_prompt(
            db_session, pid, uid, "rej-check", 2, PROMPT_STATUS_REJECTED
        )
        await db_session.commit()

        resp = await test_client.get(
            f"/api/v1/agents/rej-check/detail?project_id={pid}", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["pending_version"] is None

    async def test_superseded_max_version_not_shown_as_pending(
        self, seed_user, test_client, auth_headers, db_session
    ):
        """A superseded version must not surface as pending_version."""
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(
            db_session, pid, uid, "sup-check", 1, PROMPT_STATUS_SUPERSEDED
        )
        await _create_prompt(db_session, pid, uid, "sup-check", 2, PROMPT_STATUS_ACTIVE)
        await db_session.commit()

        resp = await test_client.get(
            f"/api/v1/agents/sup-check/detail?project_id={pid}", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["pending_version"] is None

    async def test_get_detail_does_not_auto_accept_pending_version(
        self, seed_user, test_client, auth_headers, db_session, span_factory
    ):
        """GET detail must be side-effect-free: a pending version must remain pending
        after the request, regardless of whether spans exist for it."""
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        active_p = await _create_prompt(
            db_session, pid, uid, "no-auto-accept", 1, PROMPT_STATUS_ACTIVE
        )
        pending_p = await _create_prompt(
            db_session, pid, uid, "no-auto-accept", 2, PROMPT_STATUS_PENDING
        )
        await db_session.flush()

        # Create a real production span attached to the pending version so that the
        # old auto-accept logic *would* have triggered.
        await span_factory(
            project_id=pid,
            user_id=uid,
            prompt_id=pending_p.prompt_id,
            operation="llm.chat",
        )
        await db_session.commit()

        resp = await test_client.get(
            f"/api/v1/agents/no-auto-accept/detail?project_id={pid}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # The GET response must still show the version as pending, not active.
        assert resp.json()["pending_version"] == 2
        assert resp.json()["active_version"] == 1

        # DB state must be unchanged — pending stays pending, active stays active.
        await db_session.refresh(active_p)
        await db_session.refresh(pending_p)
        assert active_p.status == PROMPT_STATUS_ACTIVE
        assert pending_p.status == PROMPT_STATUS_PENDING


# ---------------------------------------------------------------------------
# POST /api/v1/agents/{slug}/accept-version
# ---------------------------------------------------------------------------


class TestAcceptVersion:
    async def test_pending_version_becomes_active(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(db_session, pid, uid, "accept-me", 1, PROMPT_STATUS_ACTIVE)
        await _create_prompt(
            db_session, pid, uid, "accept-me", 2, PROMPT_STATUS_PENDING
        )
        await db_session.commit()

        resp = await test_client.post(
            f"/api/v1/agents/accept-me/accept-version?project_id={pid}",
            headers=auth_headers,
            json={"version": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["active_version"] == 2

        # Verify DB state
        result = await db_session.execute(
            select(Prompt).where(Prompt.slug == "accept-me", Prompt.project_id == pid)
        )
        prompts = {p.version: p for p in result.scalars().all()}
        assert prompts[2].status == PROMPT_STATUS_ACTIVE
        assert prompts[1].status == PROMPT_STATUS_SUPERSEDED

    async def test_previously_active_becomes_superseded(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        old = await _create_prompt(
            db_session, pid, uid, "supersede-me", 1, PROMPT_STATUS_ACTIVE
        )
        await _create_prompt(
            db_session, pid, uid, "supersede-me", 2, PROMPT_STATUS_PENDING
        )
        await db_session.commit()

        await test_client.post(
            f"/api/v1/agents/supersede-me/accept-version?project_id={pid}",
            headers=auth_headers,
            json={"version": 2},
        )

        await db_session.refresh(old)
        assert old.status == PROMPT_STATUS_SUPERSEDED

    async def test_rejected_version_not_changed_to_superseded(
        self, seed_user, test_client, auth_headers, db_session
    ):
        """Rejected versions must stay rejected, not be overwritten to superseded."""
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        rejected = await _create_prompt(
            db_session, pid, uid, "with-rejected", 1, PROMPT_STATUS_REJECTED
        )
        await _create_prompt(
            db_session, pid, uid, "with-rejected", 2, PROMPT_STATUS_ACTIVE
        )
        await _create_prompt(
            db_session, pid, uid, "with-rejected", 3, PROMPT_STATUS_PENDING
        )
        await db_session.commit()

        resp = await test_client.post(
            f"/api/v1/agents/with-rejected/accept-version?project_id={pid}",
            headers=auth_headers,
            json={"version": 3},
        )
        assert resp.status_code == 200

        await db_session.refresh(rejected)
        assert rejected.status == PROMPT_STATUS_REJECTED

    async def test_associated_pending_suggestion_accepted(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id

        await _create_prompt(db_session, pid, uid, "with-sugg", 1, PROMPT_STATUS_ACTIVE)
        await _create_prompt(
            db_session, pid, uid, "with-sugg", 2, PROMPT_STATUS_PENDING
        )

        sugg = Suggestion(
            prompt_slug="with-sugg",
            project_id=pid,
            title="Use clearer instructions",
            description="The prompt can be improved.",
            new_prompt_version=2,
            status="pending",
        )
        db_session.add(sugg)
        await db_session.commit()

        await test_client.post(
            f"/api/v1/agents/with-sugg/accept-version?project_id={pid}",
            headers=auth_headers,
            json={"version": 2},
        )

        await db_session.refresh(sugg)
        assert sugg.status == "accepted"

    async def test_returns_404_for_unknown_agent(
        self, seed_user, test_client, auth_headers
    ):
        pid = seed_user.project.project_id
        resp = await test_client.post(
            f"/api/v1/agents/ghost-agent/accept-version?project_id={pid}",
            headers=auth_headers,
            json={"version": 1},
        )
        assert resp.status_code == 404

    async def test_returns_404_for_unknown_version(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id
        await _create_prompt(db_session, pid, uid, "only-v1", 1, PROMPT_STATUS_ACTIVE)
        await db_session.commit()

        resp = await test_client.post(
            f"/api/v1/agents/only-v1/accept-version?project_id={pid}",
            headers=auth_headers,
            json={"version": 99},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/v1/agents/{slug}/metadata  — name and tags
# ---------------------------------------------------------------------------


class TestAgentMetadata:
    async def test_update_display_name(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id
        await _create_prompt(db_session, pid, uid, "rename-me", 1, PROMPT_STATUS_ACTIVE)
        await db_session.commit()

        resp = await test_client.put(
            f"/api/v1/agents/rename-me/metadata?project_id={pid}",
            headers=auth_headers,
            json={"name": "My Custom Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "My Custom Name"

    async def test_update_tags(self, seed_user, test_client, auth_headers, db_session):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id
        await _create_prompt(db_session, pid, uid, "tag-me", 1, PROMPT_STATUS_ACTIVE)
        await db_session.commit()

        resp = await test_client.put(
            f"/api/v1/agents/tag-me/metadata?project_id={pid}",
            headers=auth_headers,
            json={"tags": ["HR", "finance"]},
        )
        assert resp.status_code == 200
        assert sorted(resp.json()["tags"]) == ["HR", "finance"]

    @pytest.mark.parametrize(
        "name,expected_status",
        [
            ("ab", 400),  # too short
            ("a" * 256, 400),  # too long
            ("Valid Name", 200),  # exactly fine
        ],
        ids=["too-short", "too-long", "valid"],
    )
    async def test_name_length_validation(
        self, seed_user, test_client, auth_headers, db_session, name, expected_status
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id
        await _create_prompt(
            db_session, pid, uid, "validate-name", 1, PROMPT_STATUS_ACTIVE
        )
        await db_session.commit()

        resp = await test_client.put(
            f"/api/v1/agents/validate-name/metadata?project_id={pid}",
            headers=auth_headers,
            json={"name": name},
        )
        assert resp.status_code == expected_status

    async def test_too_many_tags_rejected(
        self, seed_user, test_client, auth_headers, db_session
    ):
        pid = seed_user.project.project_id
        uid = seed_user.user.user_id
        await _create_prompt(
            db_session, pid, uid, "too-many-tags", 1, PROMPT_STATUS_ACTIVE
        )
        await db_session.commit()

        resp = await test_client.put(
            f"/api/v1/agents/too-many-tags/metadata?project_id={pid}",
            headers=auth_headers,
            json={"tags": [f"tag-{i}" for i in range(21)]},
        )
        assert resp.status_code == 400
