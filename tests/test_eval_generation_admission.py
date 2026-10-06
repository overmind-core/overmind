import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from conftest import frozen_dataset
from database_callbacks import database_callback
from django.utils import timezone

from overbae.models import EvalRun, EvalSample, EvalVariant, Project
from overbae.models.eval_generation import EvalGenerationRun, EvalGenerationWork
from overbae.services.datasets.rows import frame_path
from overbae.services.eval import generation_admission as admission
from overbae.tasks import eval as eval_tasks

pytestmark = pytest.mark.django_db


def make_run(count=8, project=None):
    project = project or Project.objects.create(name="admission", slug=uuid.uuid4().hex)
    run = EvalRun.objects.create(project=project, name="run", status="running")
    variant = EvalVariant.objects.create(run=run, label="v", mode="generate")
    samples = EvalSample.objects.bulk_create(
        [EvalSample(run=run, variant=variant, row_index=i) for i in range(count)]
    )
    admission.initialize_generation(run, samples)
    return run, samples


@pytest.fixture
def limits(settings):
    settings.EVAL_MAX_IN_FLIGHT = 3
    settings.EVAL_MAX_IN_FLIGHT_PER_RUN = 2


@pytest.mark.parametrize("status", ["failed", "cancelled", "completed"])
def test_terminal_legacy_task_never_reads_source_or_calls_provider(fake_llm, status):
    project = Project.objects.create(name="p", slug=uuid.uuid4().hex)
    dataset = frozen_dataset(project, [{"input": "question", "expected_output": "answer"}])
    run = EvalRun.objects.create(
        project=project, name="r", status=status, dataset=dataset, cell=dataset.active_cell
    )
    variant = EvalVariant.objects.create(
        run=run, label="v", mode="generate", model_name="gpt-5-mini"
    )
    sample = EvalSample.objects.create(run=run, variant=variant, row_index=0)
    frame_path(dataset.active_cell).unlink()
    previous_calls = len(fake_llm.requests)
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    sample.refresh_from_db()
    assert len(fake_llm.requests) == previous_calls
    assert sample.error == ""
    assert sample.trajectory == {}


def test_admission_is_globally_bounded_and_gives_new_waiter_the_next_slot(limits):
    first, _ = make_run(100)
    second, _ = make_run(100)
    publish = Mock()
    score = Mock()
    admission.dispatch_generation(publish, score)
    assert publish.call_count == 3
    for run in (first, second):
        assert EvalGenerationWork.objects.filter(sample__run=run, state="queued").count() <= 2
    third, _ = make_run(100)
    admission.dispatch_generation(publish, score)
    assert publish.call_count == 3
    work = EvalGenerationWork.objects.filter(state="queued").first()
    assert admission.claim_generation(work.sample_id, work.task_id)
    admission.finish_generation(work.sample_id, work.task_id)
    admission.dispatch_generation(publish, score)
    assert publish.call_count == 4
    assert EvalGenerationWork.objects.filter(sample__run=third, state="queued").count() == 1


def test_ambiguous_publish_rolls_back_fence_and_stale_message_cannot_claim(limits):
    _, samples = make_run(1)
    escaped = []

    def ambiguous(sample_id, task_id):
        escaped.append((sample_id, task_id))
        raise OSError("broker accepted but acknowledgment was lost")

    with pytest.raises(OSError):
        admission.dispatch_generation(ambiguous, Mock())
    assert not admission.claim_generation(*escaped[0])
    publish = Mock()
    admission.dispatch_generation(publish, Mock())
    new_id = publish.call_args.args[1]
    assert new_id != escaped[0][1]
    assert admission.claim_generation(samples[0].id, new_id)
    assert not admission.claim_generation(samples[0].id, new_id)


def test_terminal_parent_is_not_admitted_and_queued_message_is_fenced(limits):
    run, samples = make_run(6)
    publish = Mock()
    admission.dispatch_generation(publish, Mock())
    queued = list(EvalGenerationWork.objects.filter(state="queued"))
    EvalRun.objects.filter(pk=run.pk).update(status="failed")
    admission.dispatch_generation(publish, Mock())
    assert publish.call_count == 2
    assert all(not admission.claim_generation(work.sample_id, work.task_id) for work in queued)
    assert EvalGenerationWork.objects.filter(sample__in=samples, state="cancelled").count() == 6


def test_lost_started_work_is_recorded_unknown_and_not_replayed(limits):
    run, samples = make_run(1)
    publish, score = Mock(), Mock()
    admission.dispatch_generation(publish, score)
    work = EvalGenerationWork.objects.get(sample=samples[0])
    assert admission.claim_generation(work.sample_id, work.task_id)
    EvalGenerationWork.objects.filter(pk=work.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    admission.dispatch_generation(publish, score)
    work.refresh_from_db()
    samples[0].refresh_from_db()
    assert work.state == "unknown"
    assert "outcome unknown" in samples[0].error
    assert publish.call_count == 1
    assert score.call_count == 1
    assert score.call_args.args[0] == str(run.id)


def test_last_completion_dispatches_scoring_once_and_duplicate_delivery_is_noop(limits):
    run, _ = make_run(2)
    publish, score = Mock(), Mock()
    admission.dispatch_generation(publish, score)
    for sample_id, task_id in (call.args for call in publish.call_args_list):
        assert admission.claim_generation(sample_id, task_id)
        admission.finish_generation(sample_id, task_id)
        admission.finish_generation(sample_id, task_id)
    admission.dispatch_generation(publish, score)
    admission.dispatch_generation(publish, score)
    score.assert_called_once()
    run_id, task_id = score.call_args.args
    assert admission.begin_scoring(run_id, task_id)
    assert not admission.begin_scoring(run_id, task_id)
    assert not admission.claim_generation(*publish.call_args.args)
    assert EvalGenerationRun.objects.get(run=run).scoring_started_at is not None


@pytest.mark.django_db(transaction=True)
def test_running_task_stops_before_another_teacher_forced_decision(fake_llm):
    project = Project.objects.create(name="p", slug=uuid.uuid4().hex)
    messages = [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Second question"},
        {"role": "assistant", "content": "Second answer"},
    ]
    dataset = frozen_dataset(
        project, [{"input": {"messages": messages}, "expected_output": "Second answer"}]
    )
    run = EvalRun.objects.create(
        project=project, name="r", status="running", dataset=dataset, cell=dataset.active_cell
    )
    variant = EvalVariant.objects.create(
        run=run,
        label="v",
        mode="generate",
        model_name="gpt-5-mini",
        params={"generation_strategy": "per_assistant_turn"},
    )
    sample = EvalSample.objects.create(run=run, variant=variant, row_index=0)
    calls = []

    def completion(request):
        calls.append(request)
        database_callback(lambda: EvalRun.objects.filter(pk=run.id).update(status="failed"))
        return "decision"

    fake_llm.on(lambda request: request.model == "openai/gpt-5-mini", completion)
    eval_tasks.prepare_sample.apply(kwargs={"sample_id": str(sample.id)}).get()
    assert len(calls) == 1
    sample.refresh_from_db()
    assert sample.trajectory == {}


def test_many_runs_in_one_project_do_not_take_other_projects_slots(limits):
    first = Project.objects.create(name="many-runs", slug=uuid.uuid4().hex)
    second = Project.objects.create(name="one-run", slug=uuid.uuid4().hex)
    third = Project.objects.create(name="another-run", slug=uuid.uuid4().hex)
    for _ in range(5):
        make_run(10, project=first)
    make_run(10, project=second)
    make_run(10, project=third)
    admission.dispatch_generation(Mock(), Mock())
    admitted_projects = list(
        EvalGenerationWork.objects.filter(state="queued").values_list(
            "sample__run__project_id", flat=True
        )
    )
    assert sorted(admitted_projects) == sorted([first.id, second.id, third.id])


@pytest.mark.django_db(transaction=True)
def test_concurrent_dispatchers_share_one_global_admission_limit(limits):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connection

    from overbae.models.eval_generation import EvalGenerationScheduler

    assert connection.vendor == "postgresql", "This integration test requires real row locks"
    make_run(20)
    make_run(20)
    EvalGenerationScheduler.objects.create(pk=1)
    barrier = Barrier(2)
    published = []

    def dispatch():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            return admission.dispatch_generation(
                lambda sid, tid: published.append((sid, tid)), Mock()
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(dispatch) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sum(result["admitted"] for result in results) == 3
    assert len(published) == len(set(published)) == 3
    assert EvalGenerationWork.objects.filter(state__in=("queued", "running")).count() == 3


@pytest.mark.parametrize(
    "saved",
    [
        {"trajectory": {"messages": [{"role": "assistant", "content": "saved answer"}]}},
        {"error": "provider rejected the request"},
    ],
    ids=["saved-output", "saved-error"],
)
def test_worker_loss_after_persisting_result_recovers_without_provider_replay(limits, saved):
    _, samples = make_run(1)
    publish, score = Mock(), Mock()
    admission.dispatch_generation(publish, score)
    work = EvalGenerationWork.objects.get(sample=samples[0])
    assert admission.claim_generation(work.sample_id, work.task_id)
    EvalSample.objects.filter(pk=work.sample_id).update(**saved)
    EvalGenerationWork.objects.filter(pk=work.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    admission.dispatch_generation(publish, score)
    work.refresh_from_db()
    samples[0].refresh_from_db()
    assert work.state == "done"
    assert samples[0].error == saved.get("error", "")
    assert publish.call_count == 1
    assert score.call_count == 1
