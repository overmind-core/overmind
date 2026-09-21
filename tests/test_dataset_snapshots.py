from unittest.mock import patch

import pytest
from django.utils import timezone

from overbae.models import Capability, Cell, Dataset, Project
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.notebook import agent, workspace
from overbae.services.mcp.resources import dataset_run_job_payload

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("count", [1, 10])
@pytest.mark.parametrize("reader", ["status", "workspace", "job"])
def test_snapshot_reads_chain_once_and_retains_versions_and_proposals(
    count, reader, django_assert_num_queries
):
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
    proposal = Cell.objects.create(
        dataset=dataset, position=count, title="Proposal", state="proposed"
    )
    dataset.active = cells[0]
    dataset.save(update_fields=["active"])
    versions = dataset.versions()
    with django_assert_num_queries(1):
        if reader == "status":
            result = agent.status(dataset)
            assert result["active_id"] == str(cells[0].id)
            assert [c["version"] for c in result["cells"]] == [*versions.values(), "proposed"]
            assert [c["frozen"] for c in result["cells"]] == [
                c.position <= count // 2 for c in [*cells, proposal]
            ]
        elif reader == "workspace":
            root = workspace.prepare(dataset, "Instructions")
            for cell in cells[1:]:
                text = (root / "cells" / f"{cell.position:03d}_cell_{cell.position}.py").read_text()
                assert text.startswith(f"# {versions[cell.id]} · Cell {cell.position} · ok")
                assert (" · frozen" in text) == (cell.position <= count // 2)
            assert "proposed" in (root / "cells" / f"{count:03d}_proposal.py").read_text()
        else:
            result = dataset_run_job_payload(dataset, "overmind://jobs/dataset_run/test")
            assert result["active"]["id"] == str(cells[0].id)
            assert result["active"]["version"] == versions[cells[0].id]
            assert result["cells"] == {"n": count + 1, "states": {"ok": count, "proposed": 1}}
            assert result["next_action"]["arguments"]["proposal_cell"] == str(proposal.id)

    cells[0].state = "failed"
    cells[0].save(update_fields=["state"])
    expected = str(cells[-1].id) if count > 1 else None
    assert agent.status(dataset)["active_id"] == expected
    job = dataset_run_job_payload(dataset, "overmind://jobs/dataset_run/test")
    assert (job["active"]["id"] if job["active"] else None) == expected


def test_status_reuses_context_within_response_but_rechecks_after_changes():
    project = Project.objects.create(name="Context", slug="context")
    capability = Capability.objects.create(project=project, name="Task", slug="task")
    dataset = Dataset.objects.create(project=project, intent="eval", capability=capability)
    context = context_fingerprint(capability)
    for position in range(3):
        Cell.objects.create(
            dataset=dataset,
            position=position,
            state="ok",
            fingerprint=f"frame-{position}",
            quality_report={
                "fingerprint": f"frame-{position}",
                "context_fingerprint": context,
                "intent": "eval",
                "checks": [{"name": "task_alignment", "result": "pass"}],
            },
        )
    with patch.object(agent, "context_fingerprint", wraps=context_fingerprint) as fingerprint:
        assert all(c["readiness"]["quality_reviewed"] for c in agent.status(dataset)["cells"])
        fingerprint.assert_called_once_with(capability)
    capability.description = "A different task"
    capability.save(update_fields=["description"])
    assert all(not c["readiness"]["quality_reviewed"] for c in agent.status(dataset)["cells"])
