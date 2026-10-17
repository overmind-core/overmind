import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import close_old_connections
from django.utils import timezone

from overbae.innermind import admission
from overbae.models import LiveScoringWork, Project

pytestmark = pytest.mark.django_db(transaction=True)


def project():
    return Project.objects.create(name="load-test", slug=uuid.uuid4().hex)


def test_noisy_neighbour_cannot_fill_capacity_or_starve_late_arrivals(settings):
    settings.LIVE_SCORING_MAX_IN_FLIGHT = 8
    settings.LIVE_SCORING_MAX_PER_PROJECT = 2
    noisy = project()
    admission.enqueue(noisy.pk, "span", [str(i) for i in range(10000)])
    published = []

    def publish(pk, token):
        published.append((pk, token))

    assert admission.dispatch(publish)["admitted"] == 2
    quiet = [project() for _ in range(20)]
    for other in quiet:
        admission.enqueue(other.pk, "span", ["small"])
    assert admission.dispatch(publish)["admitted"] == 6
    # Leave the noisy project's slots occupied throughout the entire drain.
    for _ in range(20):
        for pk, token in published[:]:
            item = LiveScoringWork.objects.get(pk=pk)
            if item.project_id != noisy.pk:
                claimed = admission.claim(pk, token)
                if claimed:
                    admission.finish(pk, token)
        admission.dispatch(publish)
        assert LiveScoringWork.objects.filter(state__in=admission.ACTIVE).count() <= 8
        assert (
            LiveScoringWork.objects.filter(project=noisy, state__in=admission.ACTIVE).count() == 2
        )
    assert LiveScoringWork.objects.filter(project__in=quiet, state="done").count() == 20
    assert LiveScoringWork.objects.filter(project=noisy, state="waiting").count() == 9998


def test_concurrent_dispatchers_and_duplicate_delivery_cannot_exceed_project_limit(settings):
    settings.LIVE_SCORING_MAX_IN_FLIGHT = 8
    settings.LIVE_SCORING_MAX_PER_PROJECT = 2
    tenant = project()
    admission.enqueue(tenant.pk, "span", [str(i) for i in range(200)])
    published = []

    def tick(_):
        close_old_connections()
        try:
            return admission.dispatch(lambda pk, token: published.append((pk, token)))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(tick, range(8)))
    assert sum(result["admitted"] for result in results) == 2
    pk, token = published[0]
    assert admission.claim(pk, "wrong-token") is None
    assert admission.claim(pk, token)
    assert admission.claim(pk, token) is None
    admission.enqueue(tenant.pk, "span", ["0"])
    assert LiveScoringWork.objects.filter(project=tenant).count() == 200


def test_lost_delivery_and_worker_loss_are_fenced_and_recoverable():
    tenant = project()
    admission.enqueue(tenant.pk, "span", ["lost"])
    published = []
    admission.dispatch(lambda pk, token: published.append((pk, token)))
    pk, old_token = published[0]
    LiveScoringWork.objects.filter(pk=pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert admission.dispatch(lambda *_: None)["expired"] == 1
    assert admission.claim(pk, old_token) is None
    admission.enqueue(tenant.pk, "span", ["lost"])
    admission.dispatch(lambda pk, token: published.append((pk, token)))
    _, new_token = published[-1]
    assert new_token != old_token
    assert admission.claim(pk, new_token)
    admission.finish(pk, old_token, "stale completion")
    assert LiveScoringWork.objects.get(pk=pk).state == "running"
    admission.finish(pk, new_token)
    assert LiveScoringWork.objects.get(pk=pk).state == "done"


def test_broker_failure_does_not_lose_waiting_work():
    tenant = project()
    admission.enqueue(tenant.pk, "span", ["one"])

    def unavailable(*_):
        raise ConnectionError("broker unavailable")

    with pytest.raises(ConnectionError):
        admission.dispatch(unavailable)
    assert LiveScoringWork.objects.get(project=tenant).state == "waiting"


def test_project_rotates_work_kinds_despite_large_span_backlog(settings):
    settings.LIVE_SCORING_MAX_PER_PROJECT = 1
    tenant = project()
    admission.enqueue(tenant.pk, "span", [str(i) for i in range(1000)])
    admission.enqueue(tenant.pk, "trajectory", ["trace"])
    admission.enqueue(tenant.pk, "signals", ["signals"])
    kinds = []
    for _ in range(3):
        published = []
        admission.dispatch(lambda pk, token, published=published: published.append((pk, token)))
        pk, token = published[0]
        item = admission.claim(pk, token)
        kinds.append(item.kind)
        admission.finish(pk, token)
    assert kinds == ["span", "trajectory", "signals"]


def test_slow_dispatch_does_not_occupy_other_control_threads():
    import threading
    import time

    from overbae.models import LiveScoringScheduler

    tenant = project()
    admission.enqueue(tenant.pk, "span", ["one"])
    LiveScoringScheduler.objects.get_or_create(pk=1)
    publishing = threading.Event()
    release = threading.Event()

    def publish(*_):
        publishing.set()
        assert release.wait(5)

    def owner():
        close_old_connections()
        try:
            return admission.dispatch(publish)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(owner)
        try:
            assert publishing.wait(5)
            started = time.monotonic()
            result = admission.dispatch(lambda *_: pytest.fail("overlapping publication"))
            assert time.monotonic() - started < 0.5
            assert result == {"admitted": 0, "expired": 0, "busy": True}
        finally:
            release.set()
        assert pending.result(timeout=5)["admitted"] == 1


def test_selecting_next_kind_does_not_scan_the_project_backlog(settings):
    from django.db import connection

    from overbae.models import LiveScoringProject

    settings.LIVE_SCORING_MAX_IN_FLIGHT = 1
    tenant = project()
    admission.enqueue(tenant.pk, "span", [str(i) for i in range(10000)])
    admission.enqueue(tenant.pk, "signals", ["signals"])
    LiveScoringProject.objects.filter(project=tenant).update(last_kind="span")
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE overbae_livescoringwork")
    selections = []

    def capture(execute, sql, params, many, context):
        if (
            sql.startswith('SELECT "overbae_livescoringwork"."id"')
            and "ORDER BY" in sql
            and "LIMIT 1" in sql
        ):
            selections.append((sql, params))
        return execute(sql, params, many, context)

    published = []
    with connection.execute_wrapper(capture):
        admission.dispatch(lambda pk, token: published.append((pk, token)))
    assert LiveScoringWork.objects.get(pk=published[0][0]).kind == "signals"
    assert selections
    # Restore the selected row so the measured plan sees the same waiting backlog.
    LiveScoringWork.objects.filter(pk=published[0][0]).update(state="waiting")

    def nodes(plan):
        yield plan
        for child in plan.get("Plans", []):
            yield from nodes(child)

    with connection.cursor() as cursor:
        for sql, params in selections:
            cursor.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params)
            plan = cursor.fetchone()[0][0]["Plan"]
            assert all(node["Node Type"] != "Sort" for node in nodes(plan))
            assert all(
                node.get("Actual Rows", 0) + node.get("Rows Removed by Filter", 0) <= 1
                for node in nodes(plan)
            )
