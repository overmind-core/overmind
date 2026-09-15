from __future__ import annotations

import asyncio
import uuid

import pytest

from overbae.models import (
    Annotation,
    APIToken,
    EvalRun,
    EvalSample,
    EvalVariant,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)


def _user() -> User:
    return User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )


def _context(project: Project, user: User | None = None) -> MCPContext:
    owner = user or _user()
    ProjectMembership.objects.create(user=owner, project=project)
    token = APIToken(
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": ["read", "write"],
        }
    )
    return MCPContext(user=owner, token=token, project=project)


def _sample(name: str = "run") -> EvalSample:
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    run = EvalRun.objects.create(project=project, name=name, status=EvalRun.Status.COMPLETED)
    variant = EvalVariant.objects.create(run=run, label="model-a", order=0)
    return EvalSample.objects.create(run=run, variant=variant, trajectory={"final_output": "hi"})


def _call(name: str, context: MCPContext, arguments: dict):
    return asyncio.run(CATALOG.call(name, arguments, context))


def test_create_then_read_back_annotation(monkeypatch):
    sample = _sample()
    ctx = _context(sample.run.project)

    created = _call(
        "annotate_evaluation_sample",
        ctx,
        {"sample": str(sample.id), "value": 0.5, "label": "partial", "note": "half right"},
    )
    output = created.structuredContent
    assert created.isError is False
    assert output["status"] == "created"
    assert output["annotated_by"] == ctx.user.get_username()
    annotation = Annotation.objects.get(id=output["id"])
    assert annotation.value == 0.5
    assert annotation.label == "partial"
    assert annotation.sample_id == sample.id
    assert annotation.sample.trajectory["final_output"] == "hi"


def test_missing_sample_id_is_invalid_input():
    sample = _sample()
    ctx = _context(sample.run.project)

    result = _call("annotate_evaluation_sample", ctx, {"label": "pass"})
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "invalid_input"


def test_annotations_are_project_scoped(monkeypatch):
    sample = _sample()
    other = Project.objects.create(name="Other", slug=f"p-{uuid.uuid4().hex[:8]}")
    owner = _user()
    created = _call(
        "annotate_evaluation_sample",
        _context(sample.run.project, user=owner),
        {"sample": str(sample.id), "label": "pass"},
    )
    intruder = _context(other, user=owner)

    assert created.isError is False
    listed = Annotation.objects.filter(project=other)
    assert list(listed) == []
    assert (
        _call(
            "annotate_evaluation_sample",
            intruder,
            {"annotation": created.structuredContent["id"], "label": "pass"},
        ).structuredContent["error"]["code"]
        == "annotation_not_found"
    )
    assert (
        _call(
            "annotate_evaluation_sample",
            intruder,
            {"sample": str(sample.id), "label": "pass"},
        ).structuredContent["error"]["code"]
        == "eval_sample_not_found"
    )


def test_create_attributes_annotation_to_authenticated_user(monkeypatch):
    sample = _sample()
    ctx = _context(sample.run.project)
    result = _call(
        "annotate_evaluation_sample",
        ctx,
        {"sample": str(sample.id), "label": "pass"},
    )
    assert result.isError is False
    annotation = Annotation.objects.get(id=result.structuredContent["id"])
    assert annotation.user_id == ctx.user.id
    assert annotation.project_id == ctx.project.id
    assert result.structuredContent["annotated_by"] == ctx.user.get_username()
