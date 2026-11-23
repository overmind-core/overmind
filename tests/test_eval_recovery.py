"""An evaluation outlives the worker that runs it: a lost scorer is replaced and keeps
the scores already saved, and a run whose start was lost is sent again."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from conftest import frozen_dataset
from django.utils import timezone
from factories import make_matrix_row, make_project

from overbae.innermind import dataset, generation, runs
from overbae.models import EvalMatrix, EvalRun, EvalScore
from overbae.tasks import innermind as tasks

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.covers("recovery")]

ROWS = [{"input": f"q{index}", "expected_output": f"a{index}"} for index in range(3)]


def scored_run():
    project = make_project()
    matrix = make_matrix_row(
        project,
        type=EvalMatrix.Type.DETERMINISTIC,
        source="def evaluate(span):\n    return 1.0\n",
    )
    run = runs.create_run(
        project=project,
        name="recorded",
        cell=frozen_dataset(project, ROWS).active_cell,
        mode=EvalRun.Mode.RECORDED,
        matrix=[matrix.snapshot()],
    )
    assert runs.start(run.pk) == "score"
    return EvalRun.objects.get(pk=run.pk)


def handed(run):
    published = []
    generation.dispatch(lambda *_: None, lambda run_id, token: published.append(token))
    return published[-1]


def silence(run):
    EvalRun.objects.filter(pk=run.pk).update(
        updated_at=timezone.now() - runs.SCORER_STALE - timedelta(seconds=1)
    )


def test_a_lost_scorer_is_replaced_and_keeps_the_scores_it_saved():
    run = scored_run()
    lost = handed(run)
    assert generation.begin_scoring(run.pk, lost)
    first = run.rows.order_by("row_index").first()
    dataset._save(
        EvalScore(project=run.project, run=run, source_id=f"{run.cell_id}:{first.row_index}"),
        lost,
    )
    silence(run)

    runs.reap_stalled()

    with pytest.raises(dataset.ScoringLostError):
        dataset._save(EvalScore(project=run.project, run=run, source_id="late"), lost)
    replacement = handed(run)
    assert replacement != lost
    assert runs.score(run.pk, replacement)
    run.refresh_from_db()
    assert run.status == EvalRun.Status.COMPLETED
    assert run.scoring_attempts == 2
    assert sorted(run.scores.values_list("source_id", flat=True)) == sorted(
        f"{run.cell_id}:{index}" for index in range(len(ROWS))
    )


def test_a_run_whose_start_was_lost_is_sent_again_then_failed(monkeypatch):
    sent = []
    monkeypatch.setattr(
        tasks.run_eval_run,
        "delay",
        lambda **kwargs: sent.append(kwargs) or SimpleNamespace(id=str(uuid.uuid4())),
    )
    run = EvalRun.objects.create(project=make_project(), name="lost start")
    EvalRun.objects.filter(pk=run.pk).update(
        updated_at=timezone.now() - runs.PENDING_RESEND - timedelta(seconds=1)
    )
    runs.reap_stalled()
    assert sent == [{"eval_run_id": str(run.pk)}]
    assert EvalRun.objects.get(pk=run.pk).status == EvalRun.Status.PENDING

    EvalRun.objects.filter(pk=run.pk).update(
        updated_at=timezone.now() - runs.PENDING_LIMIT - timedelta(seconds=1)
    )
    runs.reap_stalled()
    run.refresh_from_db()
    assert run.status == EvalRun.Status.FAILED
    assert "never started" in run.error
