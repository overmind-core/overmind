import pytest
from django.utils import timezone

from overbae.models import Cell, Dataset, Project
from overbae.services.mcp.resources import dataset_run_job_payload

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("count", [1, 10])
def test_snapshot_reads_chain_once_and_retains_versions(count, django_assert_num_queries):
    project = Project.objects.create(name="Snapshots", slug="snapshots")
    dataset = Dataset.objects.create(project=project, intent="eval", state="idle")
    cells = [
        Cell.objects.create(
            dataset=dataset,
            position=position,
            title=f"Cell {position}",
            state="ok",
            fingerprint=f"frame-{position}",
            rows=2,
            intent_report={"eval": {"ok": True}},
            used_at=timezone.now() if position == count // 2 else None,
        )
        for position in range(count)
    ]
    dataset.active = cells[0]
    dataset.save(update_fields=["active"])
    versions = dataset.versions()
    with django_assert_num_queries(2):
        result = dataset_run_job_payload(dataset, "overmind://jobs/dataset_run/test")
        assert result["active"]["id"] == str(cells[0].id)
        assert result["active"]["version"] == versions[cells[0].id]
        assert result["cells"] == {"n": count, "states": {"ok": count}}
        assert result["next_action"]["tool"] == "check_evaluation_readiness"
        assert result["next_action"]["arguments"]["dataset"] == str(dataset.id)

    cells[0].state = "failed"
    cells[0].save(update_fields=["state"])
    expected = str(cells[-1].id) if count > 1 else None
    job = dataset_run_job_payload(dataset, "overmind://jobs/dataset_run/test")
    assert (job["active"]["id"] if job["active"] else None) == expected
