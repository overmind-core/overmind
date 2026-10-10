import asyncio
import uuid
from unittest.mock import patch

import pytest

from overbae.models import Dataset, DatasetPipeline, DatasetPipelineBinding, DatasetPipelineRun
from overbae.services.datasets import pipeline_bindings, workbench
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.contracts.workbench import SaveInput
from tests.test_workshop_redesign import workspace

pytestmark = pytest.mark.django_db(transaction=True)


def historical_workspace():
    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "name": "Source", "source": {"rows": [{"text": "evidence"}]}},
        format="json",
    )
    assert response.status_code == 201, response.data
    dataset = Dataset.objects.get(pk=response.data["id"])
    recipe = DatasetPipeline.objects.create(
        project=project,
        created_by=context.user,
        name="Historical selection",
        request_key="historical",
        steps=[{"operation": "select", "columns": ["text"]}],
        fingerprint="a" * 64,
    )
    return project, client, context, dataset, recipe


@pytest.mark.parametrize("extra", [{}, {"steps": [{"operation": "select", "columns": ["text"]}]}])
def test_mcp_requires_retained_package_before_creating_revision(extra):
    project, _, context = workspace()
    result = asyncio.run(
        CATALOG.call(
            "save_dataset_pipeline",
            {"name": "Selection", "request_key": "missing", **extra},
            context,
        )
    )
    assert result.isError, result
    assert "package" in str(result.structuredContent).lower()
    assert not DatasetPipeline.objects.filter(project=project).exists()
    schema = SaveInput.model_json_schema()
    assert "package" in schema["required"]
    assert "steps" not in schema["properties"]


@pytest.mark.parametrize("project_route", [False, True])
@pytest.mark.parametrize("package", [None, str(uuid.UUID(int=1))])
def test_rest_rejects_package_free_and_ignored_step_definitions(project_route, package):
    project, client, _, dataset, _ = historical_workspace()
    payload = {"name": "Selection", "request_key": "missing", "steps": [{"operation": "merge"}]}
    if package:
        payload["package"] = package
    if project_route:
        payload["project"] = str(project.pk)
    response = client.post(
        "/api/dataset-pipelines/" if project_route else f"/api/datasets/{dataset.pk}/pipelines/",
        payload,
        format="json",
    )
    assert response.status_code == 400, response.data
    assert "steps" in response.data, response.data
    assert not DatasetPipeline.objects.filter(request_key="missing").exists()


def test_historical_recipe_is_readable_but_cannot_create_new_work():
    project, client, context, dataset, recipe = historical_workspace()
    source = dataset.active_cell
    detail = client.get(f"/api/datasets/{dataset.pk}/workbench/")
    assert detail.status_code == 200, detail.data
    historical = next(p for p in detail.data["pipelines"] if p["id"] == str(recipe.pk))
    assert historical["package"] is None
    assert historical["executable"] is False
    for action in (
        lambda: workbench.validate_pipeline(project, recipe.pk),
        lambda: workbench.submit(
            dataset,
            context.user,
            pipeline=recipe.pk,
            source_cell=source.pk,
            source_fingerprint=source.fingerprint,
            request_key="blocked-run",
        ),
    ):
        with patch("overbae.tasks.datasets.execute_pipeline.delay") as dispatch:
            with pytest.raises(DatasetError, match="package") as error:
                action()
            assert error.value.code == "pipeline_package_required"
            dispatch.assert_not_called()
    assert not DatasetPipelineRun.objects.exists()
    dataset.refresh_from_db()
    assert dataset.active_cell.pk == source.pk
    assert dataset.state == Dataset.State.IDLE


def test_package_free_bindings_cannot_be_created_enabled_or_advanced():
    project, _, context, source, recipe = historical_workspace()
    target = Dataset.objects.create(project=project, name="Target")
    with pytest.raises(DatasetError, match="package"):
        pipeline_bindings.save(
            project,
            context.user,
            dataset=target.pk,
            source_dataset=source.pk,
            pipeline=recipe.pk,
            request_key="binding",
        )
    binding = DatasetPipelineBinding.objects.create(
        project=project,
        dataset=target,
        source_dataset=source,
        pipeline=recipe,
        created_by=context.user,
        request_key="historical-binding",
        fingerprint="a" * 64,
    )
    with pytest.raises(DatasetError, match="package"):
        pipeline_bindings.set_state(project, binding=binding.pk, expected_version=1, enabled=True)
    with pytest.raises(DatasetError, match="package"):
        pipeline_bindings.advance(project, binding.pk, manual=True)
    binding.refresh_from_db()
    assert binding.version == 1 and binding.last_checked_at is None
    assert not DatasetPipelineRun.objects.exists()
