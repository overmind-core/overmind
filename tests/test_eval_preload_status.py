from __future__ import annotations

import uuid

import pytest

from overbae.models import Capability, Project
from overbae.services.eval.preload_status import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_READY,
    STATUS_RUNNING,
    is_active_status,
    read_eval_preload,
    write_eval_preload,
)


@pytest.fixture
def capability(db):
    project = Project.objects.create(name="preload-status", slug=f"preload-{uuid.uuid4().hex[:8]}")
    return Capability.objects.create(
        project=project,
        name="capability",
        slug=f"capability-{uuid.uuid4().hex[:8]}",
        improvement_metadata={
            "capability_card": {"task": "classify invoices"},
            "github_repo_id": "repo-1",
        },
    )


@pytest.mark.django_db
def test_write_preserves_unrelated_metadata(capability):
    write_eval_preload(capability, status=STATUS_PENDING)

    capability.refresh_from_db()
    assert capability.improvement_metadata["capability_card"]["task"] == "classify invoices"
    assert capability.improvement_metadata["github_repo_id"] == "repo-1"
    assert capability.improvement_metadata["eval_preload"]["status"] == STATUS_PENDING


@pytest.mark.django_db
def test_read_round_trip(capability):
    write_eval_preload(capability, status=STATUS_RUNNING)
    write_eval_preload(
        capability,
        status=STATUS_READY,
        counts={"generated": 3, "added": 2},
    )

    capability.refresh_from_db()
    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["status"] == STATUS_READY
    assert blob["counts"] == {"generated": 3, "added": 2}
    assert blob["started_at"]
    assert blob["finished_at"]


@pytest.mark.django_db
def test_active_status_sets_started_not_finished(capability):
    write_eval_preload(capability, status=STATUS_RUNNING, save=False)

    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["status"] == STATUS_RUNNING
    assert blob["started_at"]
    assert "finished_at" not in blob


@pytest.mark.django_db
def test_terminal_status_sets_finished(capability):
    write_eval_preload(capability, status=STATUS_EMPTY)

    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["finished_at"]


@pytest.mark.django_db
def test_failed_status_keeps_error(capability):
    write_eval_preload(capability, status=STATUS_FAILED, error="tier1 timeout")

    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["error"] == "tier1 timeout"


@pytest.mark.django_db
def test_ready_clears_prior_error(capability):
    write_eval_preload(capability, status=STATUS_FAILED, error="boom")
    write_eval_preload(capability, status=STATUS_READY, counts={"added": 1})

    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["status"] == STATUS_READY
    assert "error" not in blob


@pytest.mark.django_db
def test_read_returns_none_for_missing_or_invalid(capability):
    assert read_eval_preload(capability) is None

    capability.improvement_metadata = {"eval_preload": {"status": "bogus"}}
    assert read_eval_preload(capability) is None

    capability.improvement_metadata = "not-a-dict"
    assert read_eval_preload(capability) is None


@pytest.mark.django_db
def test_started_at_preserved_across_active_transitions(capability):
    write_eval_preload(capability, status=STATUS_PENDING)
    first_started = read_eval_preload(capability)["started_at"]

    write_eval_preload(capability, status=STATUS_RUNNING)
    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["started_at"] == first_started


def test_is_active_status():
    assert is_active_status(STATUS_PENDING)
    assert is_active_status(STATUS_RUNNING)
    assert not is_active_status(STATUS_READY)
    assert not is_active_status(None)


def test_write_rejects_invalid_status(capability):
    with pytest.raises(ValueError, match="invalid eval_preload status"):
        write_eval_preload(capability, status="nope", save=False)


def test_terminal_status_from_result():
    from overbae.services.eval.preload_status import (
        preload_counts_from_result,
        terminal_status_from_result,
    )

    assert terminal_status_from_result({"generated": 0, "added": 0}) == STATUS_EMPTY
    assert terminal_status_from_result({"generated": 3, "added": 0}) == STATUS_READY
    assert preload_counts_from_result({"generated": 2, "added": 1, "eval_set_id": "x"}) == {
        "generated": 2,
        "added": 1,
    }


@pytest.mark.django_db
def test_preload_task_success_writes_ready(capability, monkeypatch):
    from overbae.tasks.eval import preload_capability_eval_set

    monkeypatch.setattr(
        "overbae.services.eval.eval_set.generate_and_preload_default_set",
        lambda _capability, **_: {
            "eval_set_id": "set-1",
            "generated": 5,
            "created": 2,
            "added": 3,
        },
    )

    result = preload_capability_eval_set.run(capability_id=str(capability.id))

    assert result["added"] == 3
    capability.refresh_from_db()
    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["status"] == STATUS_READY
    assert blob["counts"] == {"generated": 5, "created": 2, "added": 3}
    assert blob["finished_at"]


@pytest.mark.django_db
def test_preload_task_empty_writes_empty(capability, monkeypatch):
    from overbae.tasks.eval import preload_capability_eval_set

    monkeypatch.setattr(
        "overbae.services.eval.eval_set.generate_and_preload_default_set",
        lambda _capability, **_: {
            "eval_set_id": "set-1",
            "generated": 0,
            "created": 0,
            "added": 0,
        },
    )

    preload_capability_eval_set.run(capability_id=str(capability.id))

    capability.refresh_from_db()
    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["status"] == STATUS_EMPTY


@pytest.mark.django_db
def test_preload_task_failure_not_terminal_while_retries_remain(capability, monkeypatch):
    from overbae.tasks.eval import preload_capability_eval_set

    def boom(_capability, **_kwargs):
        raise RuntimeError("tier1 timeout")

    monkeypatch.setattr(
        "overbae.services.eval.eval_set.generate_and_preload_default_set",
        boom,
    )

    preload_capability_eval_set.push_request(retries=0)
    try:
        with pytest.raises(RuntimeError, match="tier1 timeout"):
            preload_capability_eval_set.run(capability_id=str(capability.id))
    finally:
        preload_capability_eval_set.pop_request()

    capability.refresh_from_db()
    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["status"] == STATUS_RUNNING
    assert "error" not in blob


@pytest.mark.django_db
def test_preload_task_failure_writes_failed_when_retries_exhausted(capability, monkeypatch):
    from overbae.tasks.eval import preload_capability_eval_set

    def boom(_capability, **_kwargs):
        raise RuntimeError("tier1 timeout")

    monkeypatch.setattr(
        "overbae.services.eval.eval_set.generate_and_preload_default_set",
        boom,
    )

    preload_capability_eval_set.push_request(retries=preload_capability_eval_set.max_retries)
    try:
        with pytest.raises(RuntimeError, match="tier1 timeout"):
            preload_capability_eval_set.run(capability_id=str(capability.id))
    finally:
        preload_capability_eval_set.pop_request()

    capability.refresh_from_db()
    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["status"] == STATUS_FAILED
    assert blob["error"] == "tier1 timeout"
    assert blob["finished_at"]


@pytest.mark.django_db
def test_enqueue_capability_eval_preload_writes_pending(capability, monkeypatch):
    from overbae.tasks.eval import enqueue_capability_eval_preload

    queued: list[str] = []

    def fake_delay(*, capability_id: str):
        queued.append(capability_id)
        row = Capability.objects.get(pk=capability_id)
        blob = read_eval_preload(row)
        assert blob is not None
        assert blob["status"] == STATUS_PENDING

    monkeypatch.setattr(
        "overbae.tasks.eval.preload_capability_eval_set.delay",
        fake_delay,
    )

    enqueue_capability_eval_preload(capability)

    assert queued == [str(capability.id)]
    capability.refresh_from_db()
    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["status"] == STATUS_PENDING


@pytest.mark.django_db
def test_sync_then_preload_populates_default_set(monkeypatch, django_capture_on_commit_callbacks):
    from overbae.models import EvalSet, EvalSetMember
    from overbae.services.eval import card_compiler, semantic_recommender
    from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance
    from overbae.services.sync import apply_snapshot
    from overbae.tasks.eval import preload_capability_eval_set

    project = Project.objects.create(name="sync-preload", slug=f"sync-{uuid.uuid4().hex[:8]}")
    prov = SpecProvenance(
        source="codebase_card.output_fields",
        generator="card_compiler",
        surface_area="output_contract",
    )
    specs = [
        EvaluatorSpec(
            name="output-quality",
            kind="llm_judge",
            scope="final_output",
            rubric_md="grade quality",
            provenance=prov,
        ),
        EvaluatorSpec(
            name="tool-trajectory",
            kind="trajectory",
            scope="trajectory",
            config={"check": "tool_was_called"},
            provenance=prov,
        ),
    ]
    monkeypatch.setattr(card_compiler, "compile_card_evaluators", lambda _grounding: list(specs))
    monkeypatch.setattr(card_compiler, "compile_managed_card_evaluators", lambda _grounding: [])
    monkeypatch.setattr(
        semantic_recommender,
        "author_tier1_suites",
        lambda _grounding, _tier0, **kw: ([], [], None),
    )

    card = {
        "task": "Answer product questions",
        "trajectory_map": [
            {
                "id": "happy-path",
                "name": "Happy path",
                "claim": "code_path",
                "anchors": [],
                "terminal": {"kind": "emits_record"},
            }
        ],
    }
    snapshot = {
        "repo_summary": "demo",
        "version": "0.2.1",
        "capabilities": [
            {
                "slug": "support-agent",
                "name": "Support Agent",
                "capability_card": card,
                "eval_matrix": [{"name": "Answer quality", "type": "custom_judge", "rubric": "x"}],
            }
        ],
    }

    with django_capture_on_commit_callbacks(execute=True):
        apply_snapshot(project, snapshot)

    capability = Capability.objects.get(project=project, slug="support-agent")
    preload_capability_eval_set.run(capability_id=str(capability.id))

    capability.refresh_from_db()
    eval_set = EvalSet.objects.get(capability=capability, name="Default")
    assert capability.active_eval_set_id == eval_set.id
    gen = set(
        eval_set.members.filter(role=EvalSetMember.Role.GENERATIVE).values_list(
            "evaluator__name", flat=True
        )
    )
    trace = set(
        eval_set.members.filter(role=EvalSetMember.Role.TRACE_SCORING).values_list(
            "evaluator__name", flat=True
        )
    )
    assert gen == {"output-quality", "tool-trajectory"}
    assert trace == {"output-quality", "tool-trajectory"}
    blob = read_eval_preload(capability)
    assert blob is not None
    assert blob["status"] == STATUS_READY
