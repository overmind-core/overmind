import uuid

import pytest
from django.db import transaction

from overbae.models import Cell, Dataset, Project
from overbae.services.datasets import land, lifecycle, paths, review, store
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.notebook import agent

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def dataset():
    project = Project.objects.create(name="Workflow", slug=f"workflow-{uuid.uuid4()}")
    dataset = Dataset.objects.create(project=project, name="Examples", intent="eval")
    land.land_rows(
        dataset,
        [{"input": "one", "expected_output": "yes"}, {"input": "two", "expected_output": "no"}],
    )
    dataset.refresh_from_db()
    return dataset


def test_approval_can_move_past_multiple_pending_proposals(dataset):
    tools = agent.Tools(dataset.id, None, lambda _: None)
    proposals = [
        tools.add_cell(
            {"title": label, "script": f"df['expected_output'] = {label!r}", "kind": "semantic"}
        )
        for label in ("first", "second", "third")
    ]
    selected = dataset.cells.get(pk=proposals[-1]["id"])
    lifecycle.accept_proposal(dataset, selected)
    assert list(dataset.cells.values_list("id", flat=True)) == [
        dataset.source.id,
        selected.id,
        uuid.UUID(proposals[0]["id"]),
        uuid.UUID(proposals[1]["id"]),
    ]


def test_proposal_cleanup_cannot_delete_an_applied_version(dataset):
    tools = agent.Tools(dataset.id, None, lambda _: None)
    applied = tools.add_cell({"title": "Coverage", "script": "df['coverage'] = 'reviewed'"})
    dataset.refresh_from_db()
    active_id = dataset.active_cell.id
    fingerprint = store.file_sha256(paths.cell_path(dataset.id, active_id))
    result = tools.remove_cell({"id": applied["id"]})
    assert not result["ok"]
    dataset.refresh_from_db()
    assert dataset.active_cell.id == active_id
    assert store.file_sha256(paths.cell_path(dataset.id, active_id)) == fingerprint


def test_repeated_proposal_returns_the_existing_id(dataset):
    arguments = {
        "title": "Alternative",
        "script": "df['expected_output'] = 'unknown'",
        "kind": "semantic",
    }
    first = agent.Tools(dataset.id, None, lambda _: None).add_cell(arguments)
    repeated = agent.Tools(dataset.id, None, lambda _: None).add_cell(arguments)
    assert first["id"] == repeated["id"]
    assert dataset.cells.filter(state=Cell.State.PROPOSED).count() == 1


def test_quality_claims_cannot_pass_without_a_successful_audit(dataset):
    result = agent.Tools(dataset.id, None, lambda _: None).record_quality_review(
        {
            "script": "raise ValueError('audit did not execute')",
            "checks": [
                {"name": name, "evidence": "All rows checked", "rows_checked": 2, "result": "pass"}
                for name in review.REQUIRED_CHECKS
            ],
        }
    )
    assert not result["ok"]
    dataset.refresh_from_db()
    assert not dataset.active_cell.quality_report


@pytest.mark.parametrize("arguments", [{}, {"version": "1.1"}, {"id": "1"}, {"id": "proposed"}])
def test_cleanup_never_resolves_ambiguous_references(dataset, arguments):
    tools = agent.Tools(dataset.id, None, lambda _: None)
    proposal = tools.add_cell({"title": "Alternative", "script": "df = df.iloc[:1]"})
    assert not tools.remove_cell(arguments)["ok"]
    assert dataset.cells.filter(pk=proposal["id"]).exists()


def test_discard_rollback_keeps_the_preview_file(dataset):
    proposal = agent.Tools(dataset.id, None, lambda _: None).add_cell(
        {"title": "Alternative", "script": "df = df.iloc[:1]"}
    )
    path = paths.cell_path(dataset.id, proposal["id"])
    with pytest.raises(ValueError), transaction.atomic():
        lifecycle.discard_proposal(dataset, proposal["id"])
        raise ValueError("rollback")
    assert path.exists()
    assert dataset.cells.filter(pk=proposal["id"]).exists()
    lifecycle.discard_proposal(dataset, proposal["id"])
    assert not path.exists()


def test_noop_does_not_create_a_cell_or_proposal(dataset):
    result = agent.Tools(dataset.id, None, lambda _: None).add_cell(
        {"title": "Probe", "script": "df = df", "run": False}
    )
    assert result["unchanged"] and not result["proposed"]
    assert result["id"] == str(dataset.source.id)
    assert dataset.cells.count() == 1


def test_executed_quality_results_override_claimed_counts_and_do_not_change_rows(dataset):
    tools = agent.Tools(dataset.id, None, lambda _: None)
    original_id = dataset.active_cell.id
    original_fingerprint = dataset.active_cell.fingerprint
    result = tools.record_quality_review(
        {
            "script": "df = pd.DataFrame({'source_row': df.source_row, 'exact_prompt': df.input.eq('one'), 'answer_support': [True, None]})",
            "checks": [
                {
                    "name": name,
                    "evidence": "Compared row values",
                    "rows_checked": 2000,
                    "result": "pass",
                }
                for name in ("exact_prompt", "answer_support")
            ],
        }
    )
    assert result["ok"], result
    checks = {check["name"]: check for check in result["quality_report"]["checks"]}
    assert checks["exact_prompt"]["result"] == "fail"
    assert checks["exact_prompt"]["failed_source_rows"] == [1]
    assert checks["exact_prompt"]["rows_checked"] == 2
    assert checks["answer_support"]["result"] == "unknown"
    assert checks["answer_support"]["rows_checked"] == 1
    assert checks["answer_support"]["unknown_source_rows"] == [1]
    dataset.refresh_from_db()
    assert dataset.active_cell.id == original_id
    assert store.file_sha256(paths.cell_path(dataset.id, original_id)) == original_fingerprint
    assert dataset.cells.count() == 1


@pytest.mark.parametrize(
    "script",
    [
        "df = pd.DataFrame({'check': [True]})",
        "df = pd.DataFrame({'source_row': [0, 0], 'check': [True, True]})",
        "df = pd.DataFrame({'check': ['true', 'true']})",
    ],
)
def test_audit_rejects_missing_rows_duplicate_identities_and_non_booleans(dataset, script):
    result = agent.Tools(dataset.id, None, lambda _: None).record_quality_review(
        {"checks": [{"name": "check", "evidence": "Compared"}], "script": script}
    )
    assert not result["ok"]
    assert not dataset.active_cell.quality_report


def test_old_unexecuted_claims_do_not_count_as_a_quality_pass(dataset):
    cell = dataset.active_cell
    cell.quality_report = {
        "fingerprint": cell.fingerprint,
        "context_fingerprint": context_fingerprint(dataset.capability),
        "intent": dataset.intent,
        "checks": [
            {"name": name, "result": "pass", "rows_checked": cell.rows}
            for name in review.REQUIRED_CHECKS
        ],
    }
    assert not review.readiness(dataset, cell)["quality_passed"]


def test_projection_preserves_existing_human_review_status(dataset):
    before = store.read_frame(paths.cell_path(dataset.id, dataset.source.id))
    before["human_reviewed"] = False
    after = before[["source_row", "input", "expected_output"]].copy()
    preserved = review.preserve_provenance(before, after)
    assert preserved["human_reviewed"].tolist() == [False, False]
