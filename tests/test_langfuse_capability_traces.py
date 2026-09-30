"""Capability-rooted trace mapping against the real ledgerline trace shapes.

Shape 1 (batch scan): scan-inbox CAPABILITY > {triage-invoices CAPABILITY > analyze-email
SPAN > classify-invoice GENERATION} + {plan-payments CAPABILITY > rank-invoices
GENERATION}. Shape 2 (single email): triage-email CAPABILITY > classify-invoice
GENERATION.
"""

from types import SimpleNamespace

from overbae.services.connectors.langfuse.client import LangFuseObservation
from overbae.services.connectors.langfuse.mapping import LANGFUSE
from overbae.services.connectors.mapping import observations_to_span_dicts as _to_span_dicts
from overbae.services.connectors.schema import CONNECTOR_CAPABILITY_KEY_ATTR
from overbae.services.connectors.spans import span_id_for, trace_id_for

# The fixture's capabilities, named the way the wizard now marks them.
_CAPABILITY_NAMES = ["scan-inbox", "triage-invoices", "plan-payments", "triage-email"]


def observations_to_span_dicts(observations, **kwargs):
    """Use the provider vocabulary for these observed trace fixtures."""
    return _to_span_dicts(observations, conventions=LANGFUSE, **kwargs)


def _cred():
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        name="ledgerline",
        project=SimpleNamespace(id="p", slug="p"),
        capability_mapping={},
    )


def _obs(oid, *, type, name, parent=None, minute=0, **kw):
    return LangFuseObservation(
        id=oid,
        trace_id="lf-trace",
        parent_observation_id=parent,
        type=type,
        name=name,
        start_time=f"2026-01-01T00:{minute:02d}:00Z",
        end_time=f"2026-01-01T00:{minute:02d}:01Z",
        is_root_observation=parent is None,
        tags=["ledgerline", "scan-inbox", "mode:demo"],  # uniform across the trace
        **kw,
    )


def scan_inbox_trace(n_emails: int, *, invoices: int = 1, plan_level: str | None = None):
    """Shape 1. Produces 2N + 3 observations, +1 for rank-invoices when invoices > 0."""
    obs = [
        _obs("scan", type="CAPABILITY", name="scan-inbox", metadata={"email_count": n_emails}),
        _obs("triage", type="CAPABILITY", name="triage-invoices", parent="scan"),
    ]
    for i in range(n_emails):
        obs.append(
            _obs(
                f"email-{i}",
                type="SPAN",
                name="analyze-email",
                parent="triage",
                minute=i,
                metadata={"email_id": f"e{i}"},
            )
        )
        obs.append(
            _obs(
                f"gen-{i}",
                type="GENERATION",
                name="classify-invoice",
                parent=f"email-{i}",
                minute=i,
                usage_details={"input": 100, "output": 50, "total": 150},
                total_cost=0.001,
            )
        )
    plan_source = "llm" if invoices else "empty"
    if plan_level == "WARNING":
        plan_source = "fallback"
    obs.append(
        _obs(
            "plan",
            type="CAPABILITY",
            name="plan-payments",
            parent="scan",
            metadata={"invoice_count": invoices, "plan_source": plan_source},
            level=plan_level,
            status_message="planner LLM returned invalid JSON" if plan_level else None,
        )
    )
    if invoices and plan_level is None:
        obs.append(
            _obs(
                "rank",
                type="GENERATION",
                name="rank-invoices",
                parent="plan",
                usage_details={"input": 400, "output": 200, "total": 600},
                total_cost=0.002,
            )
        )
    return obs


def triage_email_trace():
    """Shape 2: a GENERATION directly under the root CAPABILITY, no intermediate SPAN."""
    return [
        _obs("root", type="CAPABILITY", name="triage-email"),
        _obs(
            "gen",
            type="GENERATION",
            name="classify-invoice",
            parent="root",
            usage_details={"input": 100, "output": 50, "total": 150},
            total_cost=0.001,
        ),
    ]


def _by_trace(spans):
    grouped = {}
    for span in spans:
        grouped.setdefault(span["trace_id"], []).append(span)
    return grouped


def test_a_multi_agent_provider_trace_keeps_its_original_tree():
    observations = scan_inbox_trace(20, invoices=10)
    spans = observations_to_span_dicts(observations, credential=_cred())
    assert len(spans) == len(observations)
    assert len(_by_trace(spans)) == 1
    assert [s["name"] for s in spans if s["parent_span_id"] is None] == ["scan-inbox"]
    assert all(s["capability"] is None for s in spans)


def test_structure_is_preserved_verbatim():
    """Nothing is duplicated or re-parented: span ids stay 1:1 with observation ids."""
    cred = _cred()
    obs = scan_inbox_trace(20, invoices=10)
    spans = observations_to_span_dicts(obs, credential=cred)

    assert [s["span_id"] for s in spans].count(span_id_for(str(cred.id), "triage")) == 1
    assert {s["span_id"] for s in spans} == {span_id_for(str(cred.id), o.id) for o in obs}

    by_obs = {s["attributes"]["langfuse.observation_id"]: s for s in spans}
    for o in obs:
        parent = by_obs[o.id]["parent_span_id"]
        assert parent == (
            span_id_for(str(cred.id), o.parent_observation_id) if o.parent_observation_id else None
        )


def test_childless_agent_step_stays_in_the_provider_trace():
    spans = observations_to_span_dicts(scan_inbox_trace(1, invoices=0), credential=_cred())
    plan = [s for s in spans if s["name"] == "plan-payments"]
    assert len(plan) == 1
    assert plan[0]["parent_span_id"] is not None
    assert plan[0]["attributes"]["langfuse.metadata.plan_source"] == "empty"
    assert len(_by_trace(spans)) == 1


def test_warning_fallback_is_not_reported_as_success():
    spans = observations_to_span_dicts(
        scan_inbox_trace(1, invoices=2, plan_level="WARNING"), credential=_cred()
    )
    plan = next(s for s in spans if s["name"] == "plan-payments")
    # OTel 1 is OK and 2 stops scoring, so a degraded run stays UNSET but visible.
    assert plan["status_code"] == 0
    assert plan["attributes"]["langfuse.level"] == "WARNING"
    assert plan["status_message"] == "planner LLM returned invalid JSON"
    assert plan["attributes"]["langfuse.metadata.plan_source"] == "fallback"


def test_error_level_still_marks_the_span_failed():
    obs = triage_email_trace()
    obs[0].level = "ERROR"
    spans = observations_to_span_dicts(obs, credential=_cred())
    assert next(s for s in spans if s["name"] == "triage-email")["status_code"] == 2


def test_single_capability_trace_keeps_original_ids():
    """Shape 2 yields one capability, so ids must not churn."""
    cred = _cred()
    spans = observations_to_span_dicts(triage_email_trace(), credential=cred)
    assert len(_by_trace(spans)) == 1
    root = next(s for s in spans if s["parent_span_id"] is None)
    assert root["name"] == "triage-email"
    assert root["span_id"] == span_id_for(str(cred.id), "root")
    assert root["trace_id"] == trace_id_for(str(cred.id), "lf-trace")
    gen = next(s for s in spans if s["name"] == "classify-invoice")
    assert gen["span_id"] == span_id_for(str(cred.id), "gen")
    assert gen["parent_span_id"] == root["span_id"]


def test_import_keeps_the_upstream_trace_id_without_inventing_a_session():
    spans = observations_to_span_dicts(scan_inbox_trace(3), credential=_cred())
    assert all("conversation.id" not in s["attributes"] for s in spans)
    assert {s["attributes"]["langfuse.trace_id"] for s in spans} == {"lf-trace"}


def test_unsplit_trace_does_not_invent_a_session():
    spans = observations_to_span_dicts(triage_email_trace(), credential=_cred())
    assert all("conversation.id" not in s["attributes"] for s in spans)


def test_span_ids_are_unique_and_deterministic():
    obs = scan_inbox_trace(20, invoices=10)
    first = observations_to_span_dicts(obs, credential=_cred())
    again = observations_to_span_dicts(obs, credential=_cred())
    ids = [s["span_id"] for s in first]
    assert len(ids) == len(set(ids))  # span_id is a global PK
    assert ids == [s["span_id"] for s in again]


def test_cost_is_not_double_counted_across_capability_traces():
    """Rolled-up ancestors must not report usage next to the children they contain."""
    spans = observations_to_span_dicts(scan_inbox_trace(20, invoices=10), credential=_cred())
    total = sum(s["attributes"].get("genai.cost", 0) for s in spans)
    assert total == 20 * 0.001 + 0.002
    capabilities = [
        s for s in spans if s["attributes"]["langfuse.observation_type"] == "CAPABILITY"
    ]
    assert all("genai.cost" not in s["attributes"] for s in capabilities)


def test_real_cost_on_a_non_generation_is_kept():
    """Cost does not roll up, so a non-zero total on a span is its own, not an aggregate."""
    obs = scan_inbox_trace(2, invoices=1)
    next(o for o in obs if o.id == "triage").total_cost = 0.5
    spans = observations_to_span_dicts(obs, credential=_cred())
    assert (
        next(s for s in spans if s["name"] == "triage-invoices")["attributes"]["genai.cost"] == 0.5
    )


def test_metadata_is_carried_and_sdk_noise_stripped():
    obs = triage_email_trace()
    obs[1].metadata = {
        "provider": "openai",
        "scope": {"name": "langfuse"},
        "resourceAttributes": {"service.name": "ledgerline"},
        "langfuse_tags": ["inert-in-v4"],
    }
    gen = next(
        s
        for s in observations_to_span_dicts(obs, credential=_cred())
        if s["name"] == "classify-invoice"
    )
    assert gen["attributes"]["langfuse.metadata.provider"] == "openai"
    assert not [k for k in gen["attributes"] if "scope" in k or "resourceAttributes" in k]
    assert "langfuse.metadata.langfuse_tags" not in gen["attributes"]


def test_uniform_tags_do_not_create_capability_assignments():
    spans = observations_to_span_dicts(scan_inbox_trace(3), credential=_cred())
    assert all(CONNECTOR_CAPABILITY_KEY_ATTR not in s["attributes"] for s in spans)
    assert all(s["capability"] is None for s in spans)


def test_nested_agent_steps_keep_their_provider_parents():
    observations = [
        _obs("root", type="AGENT", name="outer"),
        _obs("step", type="SPAN", name="step", parent="root"),
        _obs("sub", type="AGENT", name="inner", parent="step"),
        _obs("gen", type="GENERATION", name="call", parent="sub"),
    ]
    spans = observations_to_span_dicts(observations, credential=_cred())
    assert len(_by_trace(spans)) == 1
    by_name = {s["name"]: s for s in spans}
    assert by_name["inner"]["parent_span_id"] == by_name["step"]["span_id"]
    assert by_name["call"]["parent_span_id"] == by_name["inner"]["span_id"]


def test_parent_cycle_does_not_hang():
    obs = triage_email_trace()
    obs[0].parent_observation_id = "gen"  # malformed provider data
    obs[0].is_root_observation = False
    assert observations_to_span_dicts(obs, credential=_cred())


def _langchain_style_trace():
    """Nothing declares itself a capability — the shape most SDK integrations emit."""
    return [
        _obs("root", type="CHAIN", name="workflow"),
        _obs("retrieve", type="RETRIEVER", name="fetch-docs", parent="root"),
        _obs("answer", type="CHAIN", name="answer-question", parent="root"),
        _obs("gen", type="GENERATION", name="llm", parent="answer"),
    ]


def test_a_trace_with_no_matching_boundary_stays_whole():
    """No name in this trace is a mapped boundary, so nothing is regrouped."""
    spans = observations_to_span_dicts(_langchain_style_trace(), credential=_cred())
    assert len(_by_trace(spans)) == 1
    root = next(s for s in spans if s["parent_span_id"] is None)
    assert root["name"] == "workflow"


def test_observation_names_cannot_re_root_a_trace():
    spans = observations_to_span_dicts(_langchain_style_trace(), credential=_cred())
    assert len(_by_trace(spans)) == 1
    assert [s["name"] for s in spans if s["parent_span_id"] is None] == ["workflow"]


def test_provider_metadata_is_evidence_not_an_assignment():
    observations = _langchain_style_trace()
    observations[2].metadata = {"capability": "answerer"}
    spans = observations_to_span_dicts(observations, credential=_cred())
    answer = next(s for s in spans if s["name"] == "answer-question")
    assert answer["attributes"]["langfuse.metadata.capability"] == "answerer"
    assert answer["parent_span_id"] is not None
    assert answer["capability"] is None


def test_named_boundary_prunes_the_parent_subtree():
    """A promoted node must not appear in both traces."""
    spans = observations_to_span_dicts(
        _langchain_style_trace(),
        credential=_cred(),
    )
    ids = [s["span_id"] for s in spans]
    assert len(ids) == len(set(ids)) == 4


def test_generation_directly_under_capability_needs_no_intermediate_span():
    spans = observations_to_span_dicts(triage_email_trace(), credential=_cred())
    gen = next(s for s in spans if s["name"] == "classify-invoice")
    assert gen["span_type"] == "llm_call"
    assert gen["attributes"]["genai.total_tokens"] == 150


def test_zero_filled_usage_is_not_stored():
    """Non-generation rows return model="", usageDetails={}, totalCost=0 — not null."""
    obs = triage_email_trace()
    obs[0].model = ""
    obs[0].usage_details = {"input": 0, "output": 0, "total": 0}
    obs[0].total_cost = 0
    root = next(
        s
        for s in observations_to_span_dicts(obs, credential=_cred())
        if s["name"] == "triage-email"
    )
    assert not [k for k in root["attributes"] if k.startswith("genai.")]


def test_import_preserves_cost_across_the_whole_provider_trace():
    """Cost does not roll up in Langfuse, so a capability's total is its descendants'."""
    spans = observations_to_span_dicts(scan_inbox_trace(20, invoices=10), credential=_cred())
    triage_trace = next(
        t for t in _by_trace(spans).values() if any(s["name"] == "analyze-email" for s in t)
    )
    assert sum(s["attributes"].get("genai.cost", 0) for s in triage_trace) == 20 * 0.001 + 0.002


def test_cost_survives_null_model_name():
    obs = triage_email_trace()
    assert obs[1].model is None
    gen = next(
        s
        for s in observations_to_span_dicts(obs, credential=_cred())
        if s["name"] == "classify-invoice"
    )
    assert gen["attributes"]["genai.cost"] == 0.001


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
