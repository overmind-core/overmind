import uuid

from django.utils import timezone
from factories import make_span

from overbae.models import Project

from ..stack import drain


def test_trace_scores_reveal_the_recorded_reason_and_span_context(console, workshop, worker):
    project = Project.objects.get(pk=workshop.project()["id"])
    trace_id = uuid.uuid4().hex
    now = int(timezone.now().timestamp() * 1_000_000_000)
    root = make_span(
        project,
        trace_id=trace_id,
        name="Answer with sources",
        start_ns=now,
        attributes={"input": "Which cities are served?", "output": "San Francisco and Chicago."},
        eval_attributes={
            "score": 0.5,
            "metrics": {
                "Cites sources": {
                    "type": "llm",
                    "expectation": "Every city is supported by a source.",
                    "score": 0.5,
                    "verdict": "partial",
                    "reason": "Chicago has no supporting citation.",
                },
                "Tool selection": {
                    "type": "deterministic",
                    "score": None,
                    "verdict": "abstain",
                    "reason": "No tool calls were captured.",
                },
                "Provider check": {"type": "llm", "score": None, "error": "Judge unavailable"},
            },
            "trajectory": {
                "score": 0.5,
                "span_count": 2,
                "passed": False,
                "metrics": {
                    "outcome": {
                        "score": 0.5,
                        "expectation": "The agent achieves the intent.",
                        "reason": "The final answer is incomplete.",
                    }
                },
            },
        },
    )
    make_span(
        project,
        trace_id=trace_id,
        parent_span_id=root.span_id,
        name="Uncaptured answer",
        start_ns=now + 1_000,
        eval_attributes={
            "score": 0,
            "metrics": {"Empty result": {"type": "deterministic", "score": 0}},
        },
    )
    drain(worker)
    console.goto(f"/observability/{trace_id}?projectId={project.pk}&timeRange=all")
    console._browser.call(lambda: console._page.get_by_role("tab", name="Scores (4)").click())
    console._browser.call(
        lambda: console._page.get_by_role("button", name="Inspect score: Cites sources").press(
            "Enter"
        )
    )
    console.sees("Chicago has no supporting citation.")
    console.sees("Every city is supported by a source.")
    console.sees("San Francisco and Chicago.")
    console.sees("Which cities are served?")
    console.save_evidence("trace-score-evidence")
    console.press("Inspect score: Cites sources")
    console._browser.call(
        lambda: console._page.get_by_text("Chicago has no supporting citation.").wait_for(
            state="hidden"
        )
    )
    console.press("Inspect score: Trajectory")
    console.sees("The final answer is incomplete.")
    console.press("Inspect score: Tool selection")
    console.sees("No tool calls were captured.")
    console.press("Inspect score: Provider check")
    console.sees("Judge unavailable")
    console.press("Expand Trace spans")
    console.press("Uncaptured answer")
    console._browser.call(lambda: console._page.get_by_role("tab", name="Scores (1)").click())
    console.press("Inspect score: Empty result")
    console.sees("0%")
    console.sees("No explanation recorded")
    console.sees("Not captured")
    console.healthy()


def test_trace_payloads_and_events_expand_without_losing_content(console, workshop, worker):
    project = Project.objects.get(pk=workshop.project()["id"])
    trace_id = uuid.uuid4().hex
    now = int(timezone.now().timestamp() * 1_000_000_000)
    long_text = "Keep the full instruction.\n" * 80 + "Final instruction marker"
    stack = (
        "Traceback (most recent call last):\n"
        + "  provider_request()\n" * 60
        + "Final stack marker"
    )
    make_span(
        project,
        trace_id=trace_id,
        name="Inspect structured evidence",
        start_ns=now,
        attributes={
            "input": [{"role": "system", "content": long_text}],
            "output": {"answer": False, "count": 0, "results": [{"city": "Chicago"}]},
        },
        events=[{"name": "provider.debug", "attributes": {"stacktrace": stack, "escaped": False}}],
        eval_attributes={"score": None},
    )
    drain(worker)
    console._browser.call(
        lambda: console._page.add_init_script("localStorage.setItem('vite-ui-theme', 'light')")
    )
    console.goto(f"/observability/{trace_id}?projectId={project.pk}&timeRange=all")
    console.press("Collapse Input")
    console._browser.call(
        lambda: console._page.get_by_text("Final instruction marker", exact=False).wait_for(
            state="hidden"
        )
    )
    console.press("Expand Input")
    console.press("Expand System content")
    console.sees(long_text)
    assert console._browser.call(
        lambda: console._page.get_by_role("button", name="Collapse System content").evaluate(
            "button => button.getBoundingClientRect().bottom <= "
            "document.getElementById(button.getAttribute('aria-controls')).getBoundingClientRect().top"
        )
    )
    console.press("Collapse System content")
    console.press("Expand System content")
    console.sees("false")
    console.sees("0")
    console.sees('"Chicago"')
    console._browser.call(
        lambda: console._page.get_by_role("combobox", name="View format").nth(1).click()
    )
    console._browser.call(
        lambda: console._page.get_by_role("option", name="Formatted", exact=True).click()
    )
    console.sees("Chicago")
    assert (
        console._browser.call(lambda: console._page.get_by_text("Item 1", exact=True).count()) == 0
    )
    console._browser.call(lambda: console._page.get_by_role("tab", name="Events (1)").click())
    console.press("Expand stacktrace")
    console.sees("Final stack marker")
    console.press("Collapse provider.debug")
    console._browser.call(
        lambda: console._page.get_by_text("Final stack marker").wait_for(state="hidden")
    )
    console.press("Expand provider.debug")
    console.press("Expand stacktrace")
    console.save_evidence("trace-expandable-event")
    console.healthy()
