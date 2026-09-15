from __future__ import annotations

import uuid

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Dataset, FinetuningJob, Project, ProjectMembership, User

pytestmark = pytest.mark.django_db

RUNS_URL = reverse("finetuningjob-runs")
LIST_URL = reverse("finetuningjob-list")


def _setup() -> tuple[APIClient, Project, Dataset]:
    user = User.objects.create_user(
        email=f"ft-runs-{uuid.uuid4().hex[:8]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        projects_limit=5,
    )
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    dataset = frozen_dataset(project, TRAIN_ROWS, name="ds")
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client, project, dataset


def _job(project, dataset, **kwargs) -> FinetuningJob:
    return FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model=kwargs.pop("base_model", "meta-llama/Llama-3.2-1B-Instruct"),
        status=kwargs.pop("status", FinetuningJob.Status.SUCCEEDED),
        **kwargs,
    )


def test_group_id_filter_finds_a_run_regardless_of_its_age():
    client, project, dataset = _setup()
    group = uuid.uuid4()
    wanted = [_job(project, dataset, group_id=group) for _ in range(2)]
    for _ in range(30):
        _job(project, dataset, group_id=uuid.uuid4())

    r = client.get(LIST_URL, {"group_id": str(group), "ordering": "-created_at"})
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["count"] == 2
    assert {row["id"] for row in body["results"]} == {str(j.id) for j in wanted}


def test_runs_returns_whole_runs_and_counts_runs_not_jobs():
    client, project, dataset = _setup()
    groups = [uuid.uuid4() for _ in range(3)]
    for group in groups:
        for _ in range(4):
            _job(project, dataset, group_id=group)

    r = client.get(RUNS_URL, {"project": str(project.id)})
    assert r.status_code == 200, r.content
    body = r.json()
    # 3 runs of 4 jobs each: the count is 3, not 12.
    assert body["count"] == 3
    assert len(body["results"]) == 3
    assert all(len(run["jobs"]) == 4 for run in body["results"])
    assert {run["group_id"] for run in body["results"]} == {str(g) for g in groups}
    assert [run["run_id"] for run in body["results"]] == [
        run["group_id"] for run in body["results"]
    ]


def test_a_run_is_never_split_across_a_page_boundary():
    """Paging over jobs would leave a run half on page 1, corrupting every
    aggregate the client derives from its members."""
    client, project, dataset = _setup()
    groups = [uuid.uuid4() for _ in range(3)]
    for group in groups:
        for _ in range(3):
            _job(project, dataset, group_id=group)

    first = client.get(RUNS_URL, {"page_size": 2}).json()
    second = client.get(RUNS_URL, {"page_size": 2, "page": 2}).json()

    assert first["count"] == 3
    assert len(first["results"]) == 2
    assert len(second["results"]) == 1
    assert all(len(run["jobs"]) == 3 for run in first["results"] + second["results"])
    seen = [run["run_id"] for run in first["results"] + second["results"]]
    assert sorted(seen) == sorted({str(g) for g in groups})


def test_runs_are_ordered_by_their_most_recent_job():
    client, project, dataset = _setup()
    older, newer = uuid.uuid4(), uuid.uuid4()
    _job(project, dataset, group_id=older)
    _job(project, dataset, group_id=newer)
    # Give the older group a fresh job — it becomes the most recently active run.
    _job(project, dataset, group_id=older)

    results = client.get(RUNS_URL).json()["results"]
    assert [run["run_id"] for run in results] == [str(older), str(newer)]


def test_a_groupless_job_is_its_own_run_keyed_by_its_id():
    """Matches the client's ``job.groupId ?? job.id``, and keeps ``group_id``
    null so it is never mistaken for a value ``?group_id=`` would match."""
    client, project, dataset = _setup()
    lone = _job(project, dataset, group_id=None)

    results = client.get(RUNS_URL).json()["results"]
    assert len(results) == 1
    assert results[0]["run_id"] == str(lone.id)
    assert results[0]["group_id"] is None
    assert [job["id"] for job in results[0]["jobs"]] == [str(lone.id)]


def test_status_filter_selects_runs_with_any_matching_job_and_still_returns_them_whole():
    """Run-level status means "has a job with this status"; dropping the non-matching
    jobs would corrupt the status, progress and cost the client computes."""
    client, project, dataset = _setup()
    mixed = uuid.uuid4()
    _job(project, dataset, group_id=mixed, status=FinetuningJob.Status.SUCCEEDED)
    _job(project, dataset, group_id=mixed, status=FinetuningJob.Status.SUCCEEDED)
    _job(project, dataset, group_id=mixed, status=FinetuningJob.Status.FAILED)
    clean = uuid.uuid4()
    _job(project, dataset, group_id=clean, status=FinetuningJob.Status.SUCCEEDED)

    failed = client.get(RUNS_URL, {"status": "failed"}).json()
    assert failed["count"] == 1
    assert failed["results"][0]["run_id"] == str(mixed)
    assert sorted(job["status"] for job in failed["results"][0]["jobs"]) == [
        "failed",
        "succeeded",
        "succeeded",
    ]

    succeeded = client.get(RUNS_URL, {"status": "succeeded"}).json()
    assert {run["run_id"] for run in succeeded["results"]} == {str(mixed), str(clean)}


def test_runs_honour_the_jobs_list_filters_and_search():
    client, project, dataset = _setup()
    other_dataset = frozen_dataset(project, TRAIN_ROWS, name="other")
    wanted = uuid.uuid4()
    _job(project, dataset, group_id=wanted, name="checkout tuning")
    _job(project, other_dataset, group_id=uuid.uuid4(), name="refund tuning")

    by_dataset = client.get(RUNS_URL, {"dataset": str(dataset.id)}).json()
    assert [run["run_id"] for run in by_dataset["results"]] == [str(wanted)]

    by_search = client.get(RUNS_URL, {"search": "checkout"}).json()
    assert [run["run_id"] for run in by_search["results"]] == [str(wanted)]


def test_runs_never_reach_another_projects_jobs():
    client, project, dataset = _setup()
    _job(project, dataset, group_id=uuid.uuid4())
    stranger = Project.objects.create(name="X", slug=f"x-{uuid.uuid4().hex[:8]}")
    stranger_dataset = frozen_dataset(stranger, TRAIN_ROWS, name="ds")
    _job(stranger, stranger_dataset, group_id=uuid.uuid4())

    assert client.get(RUNS_URL).json()["count"] == 1


def test_dataset_facet_offers_every_dataset_with_a_job_not_just_the_loaded_page():
    client, project, dataset = _setup()
    old_dataset = frozen_dataset(project, TRAIN_ROWS, name="aardvark")
    _job(project, old_dataset, group_id=uuid.uuid4())
    for _ in range(30):
        _job(project, dataset, group_id=uuid.uuid4())

    r = client.get(reverse("finetuningjob-datasets"))
    assert r.status_code == 200, r.content
    # A bare array, not a pagination envelope.
    assert r.json() == [
        {"id": str(old_dataset.id), "name": "aardvark"},
        {"id": str(dataset.id), "name": "ds"},
    ]


def test_base_model_facet_is_distinct_and_scoped_to_the_callers_filters():
    client, project, dataset = _setup()
    other_dataset = frozen_dataset(project, TRAIN_ROWS, name="other")
    _job(project, dataset, base_model="a-model")
    _job(project, dataset, base_model="a-model")
    _job(project, other_dataset, base_model="z-model")

    r = client.get(reverse("finetuningjob-base-models"))
    assert r.status_code == 200, r.content
    assert r.json() == ["a-model", "z-model"]

    scoped = client.get(reverse("finetuningjob-base-models"), {"dataset": str(dataset.id)})
    assert scoped.json() == ["a-model"]


def test_facets_never_leak_another_projects_values():
    client, project, dataset = _setup()
    _job(project, dataset, base_model="mine")
    stranger = Project.objects.create(name="X", slug=f"x-{uuid.uuid4().hex[:8]}")
    stranger_dataset = frozen_dataset(stranger, TRAIN_ROWS, name="secret")
    _job(stranger, stranger_dataset, base_model="theirs")

    assert client.get(reverse("finetuningjob-base-models")).json() == ["mine"]
    assert [row["name"] for row in client.get(reverse("finetuningjob-datasets")).json()] == ["ds"]
