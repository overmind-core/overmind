"""Cross-entry-point dedup for connector upserts."""

from __future__ import annotations

import uuid

import pytest

from overbae.models import ConnectorCredential, Project, Span
from overbae.services.connectors.langfuse.client import LangFuseObservation
from overbae.services.connectors.langfuse.mapping import LANGFUSE
from overbae.services.connectors.mapping import observations_to_span_dicts
from overbae.tasks.connector_sync import _upsert_spans

pytestmark = pytest.mark.django_db


def test_upsert_dedupes_same_observation_across_entry_points():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    cred = ConnectorCredential.objects.create(
        project=project,
        name="LF",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="pk",
        api_secret="sk",
    )
    obs = [
        LangFuseObservation(
            id="obs-dup",
            trace_id="t-dup",
            parent_observation_id=None,
            type="SPAN",
            name="root",
            start_time="2026-01-01T00:00:00Z",
            end_time=None,
            is_root_observation=True,
        )
    ]
    first = observations_to_span_dicts(obs, credential=cred, conventions=LANGFUSE)
    second = observations_to_span_dicts(obs, credential=cred, conventions=LANGFUSE)
    assert first[0]["span_id"] == second[0]["span_id"]

    assert _upsert_spans(project, first, credential=cred) == 1
    assert _upsert_spans(project, second, credential=cred) == 0
    assert Span.objects.filter(project=project).count() == 1
