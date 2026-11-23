import uuid

import pytest
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext

from overbae.models import EvalRun, EvalRunRow, EvalScore, Project, User

pytestmark = [pytest.mark.django_db, pytest.mark.covers("capacity")]


def _pages(client, size):
    project = Project.objects.create(name="admin", slug=uuid.uuid4().hex)
    runs = EvalRun.objects.bulk_create(
        [
            EvalRun(project=project, name=f"run {index}", status="completed", model_name="m")
            for index in range(size)
        ]
    )
    EvalRunRow.objects.bulk_create(
        [EvalRunRow(run=runs[0], row_index=index, error="lost") for index in range(size)]
    )
    EvalScore.objects.bulk_create(
        [
            EvalScore(project=project, run=run, source_id=f"cell:{index}", score=1)
            for index, run in enumerate(runs)
        ]
    )
    root = f"/{settings.ADMIN_URL_PATH}overbae"
    score = EvalScore.objects.filter(run=runs[0]).get()
    measured = []
    for url in (
        f"{root}/evalrun/{runs[0].pk}/change/",
        f"{root}/evalscore/{score.pk}/change/",
        f"{root}/evalscore/",
    ):
        with CaptureQueriesContext(connection) as queries:
            response = client.get(url)
        assert response.status_code == 200, url
        measured.append((len(response.content), len(queries)))
    return measured


def test_eval_admin_pages_do_not_grow_with_rows_runs_or_scores(client):
    client.force_login(User.objects.create_superuser(email="ops@example.com", password="x" * 16))
    small, large = _pages(client, 5), _pages(client, 400)
    for (_, small_queries), (_, large_queries) in zip(small, large, strict=True):
        assert large_queries <= small_queries
    # A change page renders one record; a list page grows only to its page size.
    for (small_bytes, _), (large_bytes, _) in zip(small[:2], large[:2], strict=True):
        assert large_bytes < small_bytes * 1.2
