import uuid

from django.utils import timezone
from factories import make_span

from overbae.models import Project


def test_trace_errors_expose_captured_context_and_failed_children(console, workshop):
    project = Project.objects.get(pk=workshop.project()["id"])
    now = int(timezone.now().timestamp() * 1_000_000_000)
    trace_id = uuid.uuid4().hex
    root = make_span(
        project,
        trace_id=trace_id,
        name="Recovered navigation",
        start_ns=now,
        end_time_ns=now + 3_000_000_000,
        duration_ns=3_000_000_000,
        eval_attributes={"score": None, "trajectory": {"score": None}},
    )
    message = "The requested model is unavailable"
    stack = "Traceback (most recent call last):\n  request()\nNotFoundError: unavailable"
    make_span(
        project,
        trace_id=trace_id,
        parent_span_id=root.span_id,
        name="Provider request",
        status_code=2,
        start_ns=now + 1_000_000,
        end_time_ns=now + 1_000_000_000,
        duration_ns=999_000_000,
        attributes={
            "error.type": "not_found_error",
            "http.response.status_code": 404,
            "anthropic.request.id": "req_browser_fixture",
        },
        events=[
            {
                "name": "exception",
                "attributes": {
                    "exception.type": "NotFoundError",
                    "exception.message": message,
                    "exception.stacktrace": stack,
                },
            }
        ],
        eval_attributes={"score": None},
    )
    console.goto(f"/observability/{trace_id}?projectId={project.pk}&view=roots&timeRange=all")
    console.sees("1 failed span")
    console._browser.call(lambda: console._page.get_by_role("tab", name="Tree", exact=True).click())
    console._browser.call(lambda: console._page.keyboard.press("/"))
    console.wait_until(
        "() => document.activeElement?.getAttribute('aria-label') === 'Search spans'"
    )
    console.fill('input[aria-label="Search spans"]', "Provider")
    console._browser.call(lambda: console._page.keyboard.press("/"))
    assert (
        console._browser.call(
            lambda: console._page.get_by_role("searchbox", name="Search spans").input_value()
        )
        == "Provider/"
    )
    console.press("Clear search")
    console.click("Provider request")
    console.sees(message, exact=False)
    console.click("Technical details")
    console.sees("req_browser_fixture")
    console.click("Stack trace")
    console.sees(stack)
    console._browser.call(
        lambda: console._page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    )
    console.press("Copy error details")
    copied = console._browser.call(lambda: console._page.evaluate("navigator.clipboard.readText()"))
    assert message in copied and stack in copied and "req_browser_fixture" in copied
    console.save_evidence("trace-error-captured-details")

    for label, attrs in [
        (
            "Attribute-only failure",
            {"error.type": "not_found_error", "http.response.status_code": 404},
        ),
        ("Failure without details", {}),
    ]:
        trace_id = uuid.uuid4().hex
        make_span(
            project,
            trace_id=trace_id,
            name=label,
            status_code=2,
            start_ns=now,
            attributes=attrs,
            eval_attributes={"score": None, "trajectory": {"score": None}},
        )
        console.goto(f"/observability?projectId={project.pk}&view=roots&timeRange=all")
        console.click(label)
        console.sees("No error message captured")
        console.save_evidence(f"trace-error-{label.lower().replace(' ', '-')}")
    trace_id = uuid.uuid4().hex
    long_message = "Navigation failed: " + "The previous frame was detached. " * 50
    make_span(
        project,
        trace_id=trace_id,
        name="Long captured error",
        status_code=2,
        start_ns=now,
        attributes={"error.message": long_message},
        eval_attributes={"score": None, "trajectory": {"score": None}},
    )
    console.goto(f"/observability/{trace_id}?projectId={project.pk}&view=roots&timeRange=all")
    console.click("Full message")
    console.sees(long_message, exact=False)
    console.wait_until("() => document.documentElement.scrollWidth <= innerWidth")
    console.save_evidence("trace-error-long-message")
    console.healthy()
