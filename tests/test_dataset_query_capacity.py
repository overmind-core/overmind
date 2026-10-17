import time

import pytest
from asgiref.sync import async_to_sync
from factories import make_project, make_user

from overbae.models import APIToken, Dataset
from overbae.services.datasets import land
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)


def test_missing_query_admission_backend_is_retryable_and_does_not_change_the_source(settings):
    user = make_user()
    project = make_project(member=user)
    dataset = Dataset.objects.create(project=project, name="retained", intent="explore")
    land.land_rows(dataset, [{"value": 1}], user=user)
    dataset.refresh_from_db()
    _, token = APIToken.create_for_user(user, project=project)
    context = MCPContext(user=user, token=token, project=project)
    settings.CELERY_BROKER_URL = "redis://127.0.0.1:1/12"
    started = time.monotonic()
    response = async_to_sync(CATALOG.call)(
        "query_dataset",
        {
            "dataset": str(dataset.pk),
            "cell": str(dataset.active_cell.pk),
            "sql": "SELECT count(*) FROM t",
        },
        context,
    )
    assert time.monotonic() - started < 2
    error = response.structuredContent["error"]
    assert response.isError and error["code"] == "query_capacity_unavailable"
    assert error["retryable"] and error["fields"]["cell"] == str(dataset.active_cell.pk)
    assert error["fields"]["retry_after_seconds"] == "1"
    assert dataset.cells.count() == 1
