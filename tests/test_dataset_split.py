"""Train + eval from one source: the cut, the pair of datasets it lands, and
the surfaces that create it."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from conftest import EVAL_ROWS
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Dataset, Project, ProjectMembership, Span, User
from overbae.services.datasets import dispatch, land, paths, store
from overbae.services.datasets.lifecycle import DatasetError

pytestmark = pytest.mark.django_db

ROWS = [{"input": f"q{i}", "expected_output": f"a{i}"} for i in range(10)]


def _landing() -> land.Landing:
    return land.read_rows([dict(r) for r in ROWS])


def _inputs(part: land.Landing) -> list[str]:
    return [r["input"] for r in part.rows]


def test_split_takes_the_eval_slice_from_the_head_tail_or_a_fixed_draw():
    train, evaluation = _landing().split(eval_percent=20, position="head")
    assert _inputs(evaluation) == ["q0", "q1"]
    assert _inputs(train) == [f"q{i}" for i in range(2, 10)]
    train, evaluation = _landing().split(eval_percent=20, position="tail")
    assert _inputs(evaluation) == ["q8", "q9"]
    first = _landing().split(eval_percent=30, position="random")
    again = _landing().split(eval_percent=30, position="random")
    assert _inputs(first[1]) == _inputs(again[1]) and len(first[1].rows) == 3
    assert sorted(_inputs(first[0]) + _inputs(first[1])) == sorted(_inputs(_landing()))
    assert _inputs(first[0]) == sorted(_inputs(first[0]), key=lambda s: int(s[1:]))


def test_split_keeps_at_least_one_row_on_each_side():
    two = land.read_rows(ROWS[:2])
    train, evaluation = two.split(eval_percent=1, position="tail")
    assert len(train.rows) == 1 and len(evaluation.rows) == 1
    train, evaluation = two.split(eval_percent=99, position="tail")
    assert len(train.rows) == 1 and len(evaluation.rows) == 1
    with pytest.raises(land.LandError):
        land.read_rows(ROWS[:1]).split(eval_percent=50, position="tail")
    with pytest.raises(land.LandError):
        two.split(eval_percent=50, position="middle")


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _user() -> User:
    return User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )


def _client(project) -> APIClient:
    user = _user()
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def test_create_split_lands_two_datasets_with_disjoint_rows_and_queues_both_diagnoses():
    project, user = _project(), _user()
    with patch("overbae.tasks.datasets.diagnose.apply_async") as diagnose:
        train, evaluation = dispatch.create_split(
            project=project,
            user=user,
            name="Support",
            source={"rows": [dict(r) for r in EVAL_ROWS * 5]},
            eval_percent=20,
            position="tail",
        )
    train.refresh_from_db()
    evaluation.refresh_from_db()
    assert (train.name, train.intent, train.state) == ("Support train", "train", "diagnosing")
    assert (evaluation.name, evaluation.intent, evaluation.state) == (
        "Support eval",
        "eval",
        "diagnosing",
    )
    assert train.source.rows == 8 and evaluation.source.rows == 2
    assert train.source_spec["split"] == {
        "eval_percent": 20,
        "position": "tail",
        "role": "train",
        "sibling": str(evaluation.id),
    }
    assert evaluation.source_spec["split"]["sibling"] == str(train.id)
    frames = [store.read_frame(paths.cell_path(ds.id, ds.source.id)) for ds in (train, evaluation)]
    assert list(frames[0][store.SOURCE_ROW]) == list(range(8))
    assert list(frames[1][store.SOURCE_ROW]) == [0, 1]
    assert {call.kwargs["kwargs"]["dataset_id"] for call in diagnose.call_args_list} == {
        str(train.id),
        str(evaluation.id),
    }


def test_create_split_refuses_a_bad_cut_or_a_short_source_before_creating():
    project, user = _project(), _user()
    for bad in ({"eval_percent": 0, "position": "tail"}, {"eval_percent": 20, "position": "x"}):
        with pytest.raises(DatasetError):
            dispatch.create_split(
                project=project, user=user, name="S", source={"rows": ROWS}, **bad
            )
    with pytest.raises(DatasetError, match="Two rows"):
        dispatch.create_split(
            project=project,
            user=user,
            name="S",
            source={"rows": ROWS[:1]},
            eval_percent=20,
            position="tail",
        )
    assert Dataset.objects.filter(project=project).count() == 0


def test_split_endpoint_returns_the_pair_and_validates_the_cut():
    project = _project()
    client = _client(project)
    body = {
        "name": "Support",
        "project": str(project.id),
        "source": {"rows": ROWS},
        "eval_percent": 30,
        "position": "head",
    }
    res = client.post("/api/datasets/split/", body, format="json")
    assert res.status_code == 201, res.content
    assert res.data["train"]["intent"] == "train" and res.data["eval"]["intent"] == "eval"
    assert res.data["train"]["name"] == "Support train"
    train = Dataset.objects.get(pk=res.data["train"]["id"])
    evaluation = Dataset.objects.get(pk=res.data["eval"]["id"])
    assert train.source.rows == 7 and evaluation.source.rows == 3
    bad = client.post("/api/datasets/split/", {**body, "eval_percent": 100}, format="json")
    assert bad.status_code == 400 and "eval_percent" in bad.data
    bad = client.post("/api/datasets/split/", {**body, "position": "middle"}, format="json")
    assert bad.status_code == 400 and "position" in bad.data


@pytest.mark.django_db(transaction=True)
def test_mcp_create_from_traces_with_split_returns_both_datasets():
    import asyncio

    from overbae.models import APIToken
    from overbae.services.mcp.catalog import CATALOG
    from overbae.services.mcp.context import MCPContext

    project, user = _project(), _user()
    ProjectMembership.objects.create(user=user, project=project)
    token = APIToken(
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": ["read", "write"],
        }
    )
    context = MCPContext(user=user, token=token, project=project)
    traces = []
    for _ in range(4):
        trace_id = uuid.uuid4().hex
        traces.append(trace_id)
        Span.objects.create(
            span_id=uuid.uuid4().hex[:16],
            trace_id=trace_id,
            project=project,
            span_type="entry_point",
            name="run",
            start_time_ns=1,
            end_time_ns=2,
            duration_ns=1,
        )

    def call(arguments):
        return asyncio.run(CATALOG.call("create_dataset_from_traces", arguments, context))

    refused = call({"name": "T", "trace_ids": traces, "intent": "train", "split": {}})
    assert refused.isError is True
    result = call(
        {"name": "T", "trace_ids": traces, "split": {"eval_percent": 25, "position": "head"}}
    )
    assert result.isError is False, result.structuredContent
    body = result.structuredContent
    assert body["dataset"]["name"] == "T train" and body["eval_dataset"]["name"] == "T eval"
    assert body["traces"] == 4 and len(body["resource_links"]) == 4
    train = Dataset.objects.get(pk=body["dataset"]["id"])
    evaluation = Dataset.objects.get(pk=body["eval_dataset"]["id"])
    assert train.source.rows == 3 and evaluation.source.rows == 1
