"""Ledgerline trace shapes as Langfuse returns them.

Batch scan: scan-inbox CAPABILITY > {triage-invoices CAPABILITY > analyze-email SPAN >
classify-invoice GENERATION} + {plan-payments CAPABILITY > rank-invoices GENERATION}.
Single email: triage-email CAPABILITY > classify-invoice GENERATION.
"""

from overbae.services.connectors.langfuse.client import LangFuseObservation


def observation(oid, *, type, name, parent=None, minute=0, **kw):
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
        observation(
            "scan", type="CAPABILITY", name="scan-inbox", metadata={"email_count": n_emails}
        ),
        observation("triage", type="CAPABILITY", name="triage-invoices", parent="scan"),
    ]
    for i in range(n_emails):
        obs.append(
            observation(
                f"email-{i}",
                type="SPAN",
                name="analyze-email",
                parent="triage",
                minute=i,
                metadata={"email_id": f"e{i}"},
            )
        )
        obs.append(
            observation(
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
        observation(
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
            observation(
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
        observation("root", type="CAPABILITY", name="triage-email"),
        observation(
            "gen",
            type="GENERATION",
            name="classify-invoice",
            parent="root",
            usage_details={"input": 100, "output": 50, "total": 150},
            total_cost=0.001,
        ),
    ]


def langchain_style_trace():
    """Nothing declares itself a capability — the shape most SDK integrations emit."""
    return [
        observation("root", type="CHAIN", name="workflow"),
        observation("retrieve", type="RETRIEVER", name="fetch-docs", parent="root"),
        observation("answer", type="CHAIN", name="answer-question", parent="root"),
        observation("gen", type="GENERATION", name="llm", parent="answer"),
    ]
