import multiprocessing
import os
import time
import uuid
from datetime import timedelta

import pytest
import redis
from django.utils import timezone

from overbae.celery import app
from overbae.models import Activity, DatasetPreparation
from overbae.tasks.datasets import reap_stuck_runs
from overbae.tasks.utils.task_lock import acquire_task_lock, with_task_lock

from .conftest import GOOD_REPLY
from .otlp import SWEEP_SPANS, SWEEP_TRAJECTORIES, SWEEP_WAIT, Trace, score_history, trace_spans
from .stack import drain

pytestmark = pytest.mark.covers("recovery")


def test_evidence_collection_for_a_missing_training_job_stops(worker, fake_modal):
    app.send_task("overbae.tasks.finetuning.collect_training_evidence", args=[str(uuid.uuid4())])
    drain(worker)
    assert not fake_modal.log


def test_a_task_lock_expires_on_its_timeout_and_releases_after_the_task(settings):
    settings.CELERY_BROKER_URL = os.environ["TEST_REDIS_URL"]
    client = redis.from_url(os.environ["TEST_REDIS_URL"])
    key = "celery:lock:journey_expiring_lock"
    client.delete(key)

    @with_task_lock(lock_name="journey_expiring_lock", timeout=7)
    def held_ttl():
        return client.ttl(key)

    assert 0 < held_ttl() <= 7
    assert client.ttl(key) == -2


def _hold_and_die(name: str) -> None:
    with acquire_task_lock(name, timeout=1, renew=True):
        os._exit(0)


def test_a_renewed_lock_holds_while_its_holder_runs_and_frees_soon_after_it_dies(settings):
    settings.CELERY_BROKER_URL = os.environ["TEST_REDIS_URL"]
    name = "journey_renewed_lock"
    redis.from_url(os.environ["TEST_REDIS_URL"]).delete(f"celery:lock:{name}")

    with acquire_task_lock(name, timeout=1, renew=True) as held:
        assert held
        time.sleep(2.5)
        with acquire_task_lock(name, timeout=1) as rival:
            assert not rival
    with acquire_task_lock(name, timeout=1) as after_release:
        assert after_release

    killed = multiprocessing.get_context("fork").Process(target=_hold_and_die, args=(name,))
    killed.start()
    killed.join(10)
    with acquire_task_lock(name, timeout=1) as while_dead_lease_runs:
        assert not while_dead_lease_runs
    time.sleep(1.5)
    with acquire_task_lock(name, timeout=1) as after_lease:
        assert after_lease


@pytest.fixture
def project(cli, mcp_for, rest_for, sample_agent, worker, judges):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    key = cli.project_key(sample_agent)
    return mcp_for(key), rest_for(key), key


def test_a_failing_judge_is_tried_three_times_then_scored_with_errors(
    project, live_api, worker, fake_llm, beat, clock
):
    mcp, rest, key = project
    project_id = mcp.project()["id"]
    fake_llm.fail(lambda request: request.url.rstrip("/").endswith("/systemone"))

    def judge_calls() -> int:
        return sum(r.url.rstrip("/").endswith("/systemone") for r in fake_llm.requests)

    trace = Trace(mcp.capability("answer")["id"], GOOD_REPLY, tools=[True])
    trace.send(live_api.url, key)
    drain(worker)
    per_pass = judge_calls()
    assert per_pass > 0

    for _ in range(5):
        clock.shift(SWEEP_WAIT)
        beat(SWEEP_SPANS, SWEEP_TRAJECTORIES)
    # Three span passes over the root's rows, then three trajectory passes of path_judge.
    assert judge_calls() == 3 * per_pass + 3

    root = trace_spans(rest, project_id, trace)[trace.root_id.hex()]
    attributes = root["eval_attributes"]
    assert {m["error"] for m in attributes["metrics"].values() if m["type"] == "llm"} == {
        "scoring failed after 3 attempts"
    }
    path_judge = attributes["trajectory"]["metrics"]["path_judge"]
    assert path_judge["error"] == "scoring failed after 3 attempts"
    assert {row["source_type"] for row in score_history(rest, project_id, trace)} == {
        "span",
        "trajectory",
    }

    clock.shift(SWEEP_WAIT)
    beat(SWEEP_SPANS, SWEEP_TRAJECTORIES)
    assert judge_calls() == 3 * per_pass + 3


def test_console_ends_an_open_preparation_and_idle_preparations_expire(console, workshop):
    project = workshop.project()["id"]
    datasets = [
        workshop.call(
            "start_dataset", {"name": name, "brief": "Prepare support rows", "intent": "train"}
        )["dataset"]["id"]
        for name in ("Abandoned preparation", "Idle preparation", "Recently active preparation")
    ]
    for dataset in datasets:
        workshop.call(
            "start_dataset_preparation", {"dataset": dataset, "request_key": f"open-{dataset}"}
        )
    console.goto(f"/datasets/{datasets[0]}?projectId={project}")
    console.sees("Preparing")
    console.press("End preparation")
    console.wait_until("() => !document.body.textContent.includes('End preparation')")
    ended = workshop.call("inspect_dataset_workbench", {"dataset": datasets[0]})
    assert ended["preparation_workflow"]["state"] == "cancelled"

    idle = DatasetPreparation.objects.get(dataset_id=datasets[1])
    active = DatasetPreparation.objects.get(dataset_id=datasets[2])
    stale = timezone.now() - timedelta(hours=25)
    DatasetPreparation.objects.filter(pk__in=[idle.pk, active.pk]).update(updated_at=stale)
    Activity.objects.filter(key=f"preparation:{idle.pk}").update(updated_at=stale)
    assert reap_stuck_runs()["preparations_expired"] == 1
    kept = workshop.call("inspect_dataset_workbench", {"dataset": datasets[2]})
    assert kept["preparation_workflow"]["state"] == "preparing"
    expired = workshop.call("inspect_dataset_workbench", {"dataset": datasets[1]})
    assert expired["preparation_workflow"]["state"] == "cancelled"
    restarted = workshop.call(
        "start_dataset_preparation", {"dataset": datasets[1], "request_key": "after-expiry"}
    )
    assert restarted["preparation_workflow"]["state"] == "preparing"
    console.healthy()
