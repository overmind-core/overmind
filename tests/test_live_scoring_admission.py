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
