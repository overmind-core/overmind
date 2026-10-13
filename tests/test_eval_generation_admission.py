import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.innermind import generation
from overbae.models import EvalRun, EvalRunRow, Project

pytestmark = pytest.mark.django_db


def make_run(count=8, project=None):
    project = project or Project.objects.create(name="admission", slug=uuid.uuid4().hex)
    run = EvalRun.objects.create(
        project=project,
        name="run",
        status=EvalRun.Status.RUNNING,
        mode=EvalRun.Mode.GENERATE,
        model_name="openai/gpt-5-mini",
    )
    EvalRunRow.objects.bulk_create([EvalRunRow(run=run, row_index=i) for i in range(count)])
    return run


class Published:
    def __init__(self):
        self.rows: list[tuple[str, str]] = []
        self.scoring: list[tuple[str, str]] = []

    def tick(self):
        return generation.dispatch(
            lambda row_id, task_id: self.rows.append((row_id, task_id)),
            lambda run_id, task_id: self.scoring.append((run_id, task_id)),
        )


@pytest.fixture
def limits(settings):
    settings.EVAL_MAX_IN_FLIGHT = 3
    settings.EVAL_MAX_IN_FLIGHT_PER_RUN = 2


def test_admission_is_bounded_globally_and_per_run(limits):
    first, second = make_run(), make_run()
    published = Published()

    assert published.tick()["admitted"] == 3
    in_flight = EvalRunRow.objects.filter(state=EvalRunRow.State.QUEUED)
    assert in_flight.count() == 3
    assert max(in_flight.filter(run=r).count() for r in (first, second)) == 2
    assert published.tick()["admitted"] == 0


def test_one_project_with_many_runs_does_not_starve_another(limits):
    busy = Project.objects.create(name="busy", slug=uuid.uuid4().hex)
    for _ in range(3):
        make_run(project=busy)
    quiet = make_run()

    Published().tick()

    assert EvalRunRow.objects.filter(run=quiet, state=EvalRunRow.State.QUEUED).exists()


def test_terminal_run_is_not_admitted_and_its_queued_rows_are_cancelled(limits):
    run = make_run(count=2)
    Published().tick()
    EvalRun.objects.filter(pk=run.pk).update(status=EvalRun.Status.CANCELLED)
    stale = EvalRunRow.objects.filter(run=run, state=EvalRunRow.State.QUEUED).first()
    EvalRunRow.objects.filter(run=run).exclude(pk=stale.pk).update(state=EvalRunRow.State.WAITING)

    assert Published().tick()["admitted"] == 0
    assert set(EvalRunRow.objects.filter(run=run).values_list("state", flat=True)) == {
        EvalRunRow.State.CANCELLED
    }
    assert generation.claim(stale.pk, stale.task_id) is False


def test_a_stale_task_id_cannot_claim_the_row(limits):
    make_run(count=1)
    published = Published()
    published.tick()
    row_id, task_id = published.rows[0]

    assert generation.claim(row_id, str(uuid.uuid4())) is False
    assert generation.claim(row_id, task_id) is True
    assert generation.claim(row_id, task_id) is False


def test_lost_running_work_is_recorded_unknown_and_not_replayed(limits):
    make_run(count=1)
    published = Published()
    published.tick()
    row_id, task_id = published.rows[0]
    generation.claim(row_id, task_id)
    EvalRunRow.objects.filter(pk=row_id).update(expires_at=timezone.now() - timedelta(seconds=1))

    assert published.tick()["expired"] == 1
    row = EvalRunRow.objects.get(pk=row_id)
    assert row.state == EvalRunRow.State.UNKNOWN
    assert "not replayed" in row.error
    assert len(published.rows) == 1


def test_a_saved_result_survives_worker_loss_without_replay(limits):
    make_run(count=1)
    published = Published()
    published.tick()
    row_id, task_id = published.rows[0]
    generation.claim(row_id, task_id)
    EvalRunRow.objects.filter(pk=row_id).update(
        output="answer", expires_at=timezone.now() - timedelta(seconds=1)
    )

    published.tick()

    row = EvalRunRow.objects.get(pk=row_id)
    assert (row.state, row.output, row.error) == (EvalRunRow.State.DONE, "answer", "")
    assert len(published.rows) == 1


def test_last_completion_hands_the_run_to_scoring_exactly_once(limits):
    run = make_run(count=1)
    published = Published()
    published.tick()
    row_id, task_id = published.rows[0]
    generation.claim(row_id, task_id)
    generation.finish(row_id, task_id)

    published.tick()
    published.tick()

    assert len(published.scoring) == 1
    run_id, scoring_id = published.scoring[0]
    assert run_id == str(run.pk)
    assert generation.begin_scoring(run_id, scoring_id) is True
    assert generation.begin_scoring(run_id, scoring_id) is False
