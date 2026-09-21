import uuid
from unittest.mock import Mock, patch

import pytest

from overbae.models import Cell, Dataset, Project
from overbae.services.datasets import dispatch, land, lifecycle, paths, store
from overbae.services.datasets.notebook import agent, engines
from overbae.services.mcp.contracts.datasets import ChatTurn
from overbae.services.mcp.resources import dataset_run_job_payload
from overbae.tasks import datasets as tasks

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def dataset():
    project = Project.objects.create(name="Approvals", slug=f"approval-{uuid.uuid4()}")
    dataset = Dataset.objects.create(project=project, name="Seed", intent="eval")
    land.land_rows(
        dataset,
        [{"input": "one", "expected_output": "yes"}, {"input": "two", "expected_output": "no"}],
    )
    dataset.refresh_from_db()
    lifecycle.set_active(dataset, dataset.source)
    return dataset


def propose(dataset, monkeypatch, *, generation=False, error=""):
    class Engine:
        name = "test"

        def run(self, dataset, message, tools, pending):
            if generation:
                tools.seed_examples({"target_rows": 5, "instruction": "Cover new scenarios"})
            tools.add_cell(
                {
                    "title": "Resolve ambiguity",
                    "script": "df['expected_output'] = 'unknown'",
                    "kind": "semantic",
                }
            )
            yield from pending
            return engines.Outcome(text="Approve or deny the proposed labels.", error=error)

    monkeypatch.setattr(engines, "select", lambda: Engine())
    list(agent.follow_up(dataset.id, "Prepare these rows and check their quality"))
    dataset.refresh_from_db()
    return dataset.cells.get(state=Cell.State.PROPOSED)


def test_approval_activates_exact_preview_and_resumes_once(dataset, monkeypatch):
    proposal = propose(dataset, monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(tasks.turn, "apply_async", enqueue)
    with patch(
        "overbae.services.datasets.notebook.runner.run", side_effect=AssertionError("reran preview")
    ):
        dispatch.run_dataset(dataset, None, proposal=proposal)
    dataset.refresh_from_db()
    proposal.refresh_from_db()
    assert dataset.active_id == proposal.id
    assert dataset.state == Dataset.State.DIAGNOSING
    assert dataset.chat[-1]["status"] == "resolved"
    assert dataset.chat[-1]["error"] == ""
    assert store.read_frame(paths.cell_path(dataset.id, proposal.id))[
        "expected_output"
    ].tolist() == ["unknown", "unknown"]
    kwargs = enqueue.call_args.kwargs["kwargs"]
    assert "Prepare these rows and check their quality" in kwargs["message"]
    assert "approved" in kwargs["message"]
    assert "record quality checks" in kwargs["message"]
    assert kwargs["display"] == "Proposal decisions: Resolve ambiguity — approved"
    dispatch.run_dataset(dataset, None, proposal=proposal)
    tasks.run(dataset_id=str(dataset.id), proposal_id=str(proposal.id))
    assert enqueue.call_count == 1


def test_denial_preserves_active_rows_and_resumes_with_the_decision(dataset, monkeypatch):
    proposal = propose(dataset, monkeypatch)
    active_id = dataset.active_id
    enqueue = Mock()
    monkeypatch.setattr(tasks.turn, "apply_async", enqueue)
    dispatch.discard_cell(dataset, proposal, None)
    dataset.refresh_from_db()
    assert dataset.active_id == active_id
    assert not dataset.cells.filter(state=Cell.State.PROPOSED).exists()
    assert dataset.state == Dataset.State.DIAGNOSING
    assert dataset.chat[-1]["status"] == "resolved"
    assert "denied" in enqueue.call_args.kwargs["kwargs"]["message"]
    assert (
        "does not authorize a different semantic change"
        in enqueue.call_args.kwargs["kwargs"]["message"]
    )


@pytest.mark.parametrize("generation", [False, True])
def test_pending_decision_is_waiting_not_generation_failure(dataset, monkeypatch, generation):
    propose(dataset, monkeypatch, generation=generation)
    latest = dataset.chat[-1]
    assert latest["status"] == "awaiting_approval"
    assert latest["progress"]["stage"] == "awaiting_approval"
    assert not latest["error"]
    snapshot = dataset_run_job_payload(dataset, "test")
    assert snapshot["status"] == "awaiting_approval"
    assert snapshot["progress"]["stage"] == "awaiting_approval"
    ChatTurn.model_validate(snapshot["latest_turn"])
    assert dataset.state == Dataset.State.IDLE


def test_provider_failure_is_not_hidden_by_pending_approval(dataset, monkeypatch):
    propose(dataset, monkeypatch, error="Provider disconnected")
    assert dataset.chat[-1]["status"] == "error"
    assert dataset.chat[-1]["error"] == "Provider disconnected"


def test_stale_approval_never_activates_or_resumes(dataset, monkeypatch):
    proposal = propose(dataset, monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(tasks.turn, "apply_async", enqueue)
    Cell.objects.filter(pk=dataset.source.id).update(fingerprint="changed")
    with pytest.raises(lifecycle.DatasetError, match="stale"):
        dispatch.run_dataset(dataset, None, proposal=proposal)
    dataset.refresh_from_db()
    assert dataset.active_id == dataset.source.id
    assert dataset.state == Dataset.State.IDLE
    enqueue.assert_not_called()


def test_changed_preview_after_approval_fails_without_resuming(dataset, monkeypatch):
    proposal = propose(dataset, monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(tasks.turn, "apply_async", enqueue)
    monkeypatch.setattr(tasks.run, "apply_async", Mock())
    dispatch.run_dataset(dataset, None, proposal=proposal)
    store.write_frame(
        paths.cell_path(dataset.id, proposal.id),
        store.read_frame(paths.cell_path(dataset.id, dataset.source.id)),
    )
    tasks.run(dataset_id=str(dataset.id), proposal_id=str(proposal.id))
    dataset.refresh_from_db()
    assert dataset.state == Dataset.State.ERROR
    assert dataset.active_id == dataset.source.id
    enqueue.assert_not_called()


def test_generation_cannot_replace_new_examples_with_a_replication_script(dataset):
    tools = agent.Tools(dataset.id, None, lambda _: None)
    tools.seed_examples({"target_rows": 5000, "instruction": "Expand scenario coverage"})
    result = tools.add_cell(
        {
            "title": "Duplicate rows",
            "kind": "semantic",
            "run": False,
            "script": "df = pd.concat([df, df])",
        }
    )
    assert result["ok"] is False
    assert "add_synthetic_rows" in result["error"]
    assert not dataset.cells.filter(state=Cell.State.PROPOSED).exists()
    assert dataset.active_cell.rows == 2


def test_followup_repairs_advance_a_pinned_active_version(dataset):
    source = dataset.active_id
    result = agent.Tools(dataset.id, None, lambda _: None).add_cell(
        {"title": "Add coverage", "script": "df['coverage'] = 'reviewed'"}
    )
    dataset.refresh_from_db()
    assert result["ok"]
    assert str(dataset.active_id) == result["id"]
    assert dataset.active_id != source


def test_all_decisions_arrive_before_one_continuation(dataset, monkeypatch):
    first = propose(dataset, monkeypatch)
    tools = agent.Tools(dataset.id, None, lambda _: None)
    second = dataset.cells.get(
        pk=tools.add_cell(
            {
                "title": "Second option",
                "script": "df['expected_output'] = 'maybe'",
                "kind": "semantic",
            }
        )["id"]
    )
    dataset.chat[-1]["cells"].append({"id": str(second.id), "action": "proposed"})
    dataset.save(update_fields=["chat"])
    enqueue = Mock()
    monkeypatch.setattr(tasks.turn, "apply_async", enqueue)
    dispatch.discard_cell(dataset, first, None)
    dataset.refresh_from_db()
    assert dataset.state == Dataset.State.IDLE
    assert dataset.chat[-1]["status"] == "awaiting_approval"
    enqueue.assert_not_called()
    dispatch.run_dataset(dataset, None, proposal=second)
    assert enqueue.call_count == 1
    message = enqueue.call_args.kwargs["kwargs"]["message"]
    assert "denied" in message and "approved" in message


def test_resumed_agent_reviews_active_version_and_redelivery_does_not_run_again(
    dataset, monkeypatch
):
    proposal = propose(dataset, monkeypatch)
    enqueue = Mock()
    monkeypatch.setattr(tasks.turn, "apply_async", enqueue)
    dispatch.run_dataset(dataset, None, proposal=proposal)

    class ReviewingEngine:
        name = "test"

        def run(self, dataset, message, tools, pending):
            assert dataset.active_id == proposal.id
            result = tools.record_quality_review(
                {
                    "script": "df = pd.DataFrame({name: [True] * len(df) for name in ('task_alignment', 'input_evidence', 'answer_support', 'output_schema')})",
                    "checks": [
                        {
                            "name": name,
                            "result": "pass",
                            "rows_checked": 2,
                            "evidence": "Checked both rows",
                        }
                        for name in (
                            "task_alignment",
                            "input_evidence",
                            "answer_support",
                            "output_schema",
                        )
                    ],
                }
            )
            assert result["ok"]
            yield from pending
            return engines.Outcome(text="Quality checks recorded.")

    monkeypatch.setattr(engines, "select", lambda: ReviewingEngine())
    queued = enqueue.call_args.kwargs
    result = tasks.turn.apply(kwargs=queued["kwargs"], task_id=queued["task_id"])
    assert result.result == {"status": "ok"}
    dataset.refresh_from_db()
    proposal.refresh_from_db()
    assert proposal.quality_report
    assert dataset.chat[-1]["status"] == "complete"
    assert dataset.state == Dataset.State.IDLE
    count = len(dataset.chat)
    Dataset.objects.filter(pk=dataset.id).update(agent_turn_key="another-turn")
    tasks.turn.apply(kwargs=queued["kwargs"], task_id=queued["task_id"])
    dataset.refresh_from_db()
    assert len(dataset.chat) == count
