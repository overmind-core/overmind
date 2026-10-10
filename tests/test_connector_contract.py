from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from django.utils import timezone
from factories import make_connector
from fakes.vendors import (
    BraintrustAPI,
    GalileoAPI,
    LangfuseAPI,
    LangSmithAPI,
    support_desk_trace,
)

from overbae.services.connectors import get_adapter
from overbae.services.connectors.braintrust.client import (
    BraintrustAuthError,
    BraintrustClient,
    BraintrustError,
    BraintrustTimeoutError,
)
from overbae.services.connectors.galileo.client import (
    GalileoAuthError,
    GalileoClient,
    GalileoError,
    GalileoTimeoutError,
)
from overbae.services.connectors.langfuse.client import LangFuseClient, LangFuseError
from overbae.services.connectors.langsmith.client import (
    LangSmithAuthError,
    LangSmithClient,
    LangSmithError,
    LangSmithTimeoutError,
)

_SINCE = datetime(2026, 1, 2, tzinfo=UTC)
_LS_PROJECT = "11111111-1111-1111-1111-111111111111"


@dataclass(frozen=True)
class Transport:
    host: str
    make: Any
    call: Any
    ok: Any
    error: type[Exception]
    auth_error: type[Exception]
    timeout_error: type[Exception]
    auth_header: tuple[str, str]
    paced: bool = True


TRANSPORTS = {
    "langfuse": Transport(
        host="https://cloud.langfuse.com",
        make=lambda **kw: LangFuseClient("pk", "sk", **kw),
        call=lambda c: c.list_projects(),
        ok={"data": []},
        error=LangFuseError,
        auth_error=LangFuseError,
        timeout_error=LangFuseError,
        auth_header=("authorization", "Basic cGs6c2s="),
        paced=False,
    ),
    "langsmith": Transport(
        host="https://api.smith.langchain.com",
        make=lambda **kw: LangSmithClient("  Bearer lsv2_sk_key \n", **kw),
        call=lambda c: c.query_runs([_LS_PROJECT], min_start_time=_SINCE),
        ok={"items": []},
        error=LangSmithError,
        auth_error=LangSmithAuthError,
        timeout_error=LangSmithTimeoutError,
        auth_header=("x-api-key", "lsv2_sk_key"),
    ),
    "braintrust": Transport(
        host="https://api.braintrust.dev",
        make=lambda **kw: BraintrustClient("bt-key", **kw),
        call=lambda c: c.query("SELECT 1"),
        ok={"data": []},
        error=BraintrustError,
        auth_error=BraintrustAuthError,
        timeout_error=BraintrustTimeoutError,
        auth_header=("authorization", "Bearer bt-key"),
    ),
    "galileo": Transport(
        host="https://api.galileo.ai",
        make=lambda **kw: GalileoClient("gal-key", **kw),
        call=lambda c: c.list_log_streams(),
        ok={"projects": [], "next_starting_token": None},
        error=GalileoError,
        auth_error=GalileoAuthError,
        timeout_error=GalileoTimeoutError,
        auth_header=("galileo-api-key", "gal-key"),
    ),
}
VENDORS = sorted(TRANSPORTS)


@pytest.mark.parametrize("vendor", VENDORS)
def test_the_client_authenticates_every_request(vendor, scripted, slept):
    spec = TRANSPORTS[vendor]
    api = scripted(spec.host).reply(json_body=spec.ok)
    spec.call(spec.make())
    name, value = spec.auth_header
    assert api.calls[0].headers[name] == value


@pytest.mark.parametrize("vendor", VENDORS)
def test_a_rate_limit_waits_as_long_as_the_vendor_asks_then_succeeds(vendor, scripted, slept):
    spec = TRANSPORTS[vendor]
    api = scripted(spec.host)
    api.reply(429, text="Too many requests", headers={"Retry-After": "3"}).reply(json_body=spec.ok)
    spec.call(spec.make())
    assert len(api.calls) == 2
    assert 3.0 in slept


@pytest.mark.parametrize("vendor", VENDORS)
def test_a_rejected_key_fails_at_once_and_says_so(vendor, scripted, slept):
    spec = TRANSPORTS[vendor]
    api = scripted(spec.host).reply(401, text="bad key")
    with pytest.raises(spec.auth_error) as caught:
        spec.call(spec.make())
    assert len(api.calls) == 1
    status = getattr(caught.value, "status_code", None) or caught.value.response.status_code
    assert status == 401


@pytest.mark.parametrize("vendor", VENDORS)
def test_a_gateway_timeout_is_not_retried(vendor, scripted, slept):
    spec = TRANSPORTS[vendor]
    api = scripted(spec.host).reply(504, text="gateway timeout")
    with pytest.raises(spec.timeout_error):
        spec.call(spec.make())
    assert len(api.calls) == 1


@pytest.mark.parametrize("vendor", VENDORS)
def test_a_plain_text_error_body_reaches_the_message(vendor, scripted, slept):
    spec = TRANSPORTS[vendor]
    scripted(spec.host).reply(400, text="plain text failure")
    with pytest.raises(spec.error, match="plain text failure"):
        spec.call(spec.make())


@pytest.mark.parametrize("vendor", VENDORS)
def test_the_base_url_points_at_a_regional_or_self_hosted_deployment(vendor, scripted, slept):
    spec = TRANSPORTS[vendor]
    api = scripted("https://self-hosted.example.com").reply(json_body=spec.ok)
    spec.call(spec.make(base_url="https://self-hosted.example.com/"))
    assert api.calls[0].url.startswith("https://self-hosted.example.com/")
    assert "//" not in api.calls[0].path


@pytest.mark.parametrize("vendor", [v for v in VENDORS if TRANSPORTS[v].paced])
def test_requests_are_spaced_to_the_configured_rate(vendor, scripted, slept, monkeypatch):
    spec = TRANSPORTS[vendor]
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    scripted(spec.host).reply(json_body=spec.ok)
    client = spec.make(requests_per_minute=12)
    spec.call(client)
    spec.call(client)
    assert slept[-1] == pytest.approx(5.0)


@pytest.mark.parametrize("vendor", ["langsmith", "braintrust", "galileo"])
def test_a_network_timeout_is_reported_as_the_vendor_error(vendor, scripted, slept):
    spec = TRANSPORTS[vendor]
    scripted(spec.host).fail(httpx.ConnectError("unreachable"))
    with pytest.raises(spec.error):
        spec.call(spec.make())


VENDOR_APIS = {
    "langfuse": LangfuseAPI,
    "langsmith": LangSmithAPI,
    "braintrust": BraintrustAPI,
    "galileo": GalileoAPI,
}


def _source(api) -> str:
    if isinstance(api, GalileoAPI):
        return f"{api.project}:{api.stream}"
    if isinstance(api, LangSmithAPI):
        return api.project
    if isinstance(api, BraintrustAPI):
        return "bt-project"
    return "lf-project"


@pytest.fixture
def connected(fake_llm, db):
    def connect(vendor: str, *, source: str | None = None, **config):
        api = VENDOR_APIS[vendor]()
        trace_id, steps = support_desk_trace(timezone.now() - timedelta(hours=1))
        api.record(trace_id, steps)
        fake_llm.network.vendors.append(api)
        credential = make_connector(
            vendor,
            source_project_id=_source(api) if source is None else source,
            base_url=api.host,
            api_secret="secret" if vendor == "langfuse" else "",
            **config,
        )
        return api, steps, get_adapter(credential)

    return connect


@pytest.mark.parametrize("vendor", VENDORS)
def test_verify_lists_the_source_projects_through_the_real_client(vendor, connected, slept):
    api, _steps, adapter = connected(vendor)
    result = adapter.verify()
    assert result.ok is True, result.detail
    assert _source(api) in [project.id for project in result.projects]


@pytest.mark.parametrize("vendor", VENDORS)
def test_a_live_poll_imports_each_trace_whole_as_one_unit(vendor, connected, slept):
    _api, steps, adapter = connected(vendor)
    units, state = [], {"mode": "live"}
    for _ in range(10):
        page = adapter.fetch_page(state)
        units += page.units
        state = page.next_state
        if page.done:
            break
    assert page.done is True
    assert state["mode"] == "live"
    assert len(units) == 1
    assert len(units[0].records) == len(steps)


@pytest.mark.parametrize("vendor", VENDORS)
def test_a_backfill_walks_its_windows_then_turns_to_live_polling(vendor, connected, slept):
    _api, _steps, adapter = connected(vendor, lookback_days=2)
    state: dict = {}
    for _ in range(50):
        page = adapter.fetch_page(state)
        state = page.next_state
        if page.done:
            break
    assert page.done is True
    assert state["mode"] == "live"


@pytest.mark.parametrize("vendor", ["langsmith", "braintrust", "galileo"])
def test_a_fetch_without_a_source_project_is_refused(vendor, connected, slept):
    _api, _steps, adapter = connected(vendor, source="")
    with pytest.raises(Exception, match="source project"):
        adapter.fetch_page({"mode": "live"})


@pytest.mark.parametrize("vendor", ["langsmith", "braintrust", "galileo"])
def test_discovery_reads_the_project_the_wizard_offers_not_the_saved_one(vendor, connected, slept):
    api, _steps, adapter = connected(vendor, source="")
    offered = _source(api)
    units = adapter.sample_units(lookback_days=7, source_project_id=offered)
    assert units
    bodies = " ".join(f"{c.url} {c.body or ''}" for c in api.requests)
    assert offered.split(":")[0] in bodies


@pytest.mark.parametrize("vendor", VENDORS)
def test_a_backfill_reaches_back_only_as_far_as_the_configured_lookback(vendor, connected, slept):
    _api, _steps, adapter = connected(vendor, lookback_days=2)
    earliest, state = timezone.now(), {}
    for _ in range(50):
        page = adapter.fetch_page(state)
        earliest = min(earliest, page.window_from or earliest)
        state = page.next_state
        if page.done:
            break
    assert earliest >= timezone.now() - timedelta(days=2, minutes=1)


@pytest.mark.parametrize("vendor", ["langsmith", "braintrust"])
def test_a_vendor_without_a_cheap_count_spends_no_request_on_one(vendor, connected, slept):
    api, _steps, adapter = connected(vendor)
    before = len(api.requests)
    assert adapter.count(lookback_days=7) is None
    assert len(api.requests) == before
