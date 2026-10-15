import pytest

from overbae.models import Dataset
from overbae.services.datasets import land

from ..stack import drain, settle


@pytest.mark.parametrize("selection", ["selected", "all"])
def test_selected_traces_open_the_landed_workshop_source(
    console, workshop, sample_agent, live_api, support_desk_llm, worker, selection
):
    project = workshop.project()["id"]
    sample_agent.run(
        "Refund order 42 please",
        "Where is order 42?",
        api_url=live_api.url,
        llm_url=support_desk_llm,
    )
    drain(worker)
    console.goto(f"/observability?projectId={project}")
    console.click("Traces")
    console.sees("handle_ticket")
    console._browser.call(
        lambda: console._page.get_by_role("checkbox", name="Select row", exact=True).first.click()
    )
    if selection == "all":
        console.press("Select all 2")
    console.press("Add to dataset")
    console.fill("#dataset-name", "Selected trace source")
    assert console._browser.call(lambda: console._page.locator("#dataset-purpose").count()) == 0
    assert console._browser.call(lambda: console._page.locator("#dataset-brief").count()) == 0
    capability = workshop.capability("answer") if selection == "all" else None
    if capability:
        console.choose("#dataset-capability", capability["name"])
    else:
        console.sees("None")
    console.wait_until(
        """() => {
            const dialog = document.querySelector('[role=dialog]');
            return dialog && getComputedStyle(dialog).opacity === '1'
                && !document.querySelector('[role=listbox]');
        }"""
    )
    console.save_evidence(f"trace-create-{selection}")
    if selection == "selected":
        console._browser.call(
            lambda: console._page.set_viewport_size({"width": 390, "height": 844})
        )
        console.wait_until(
            """() => {
                const dialog = document.querySelector('[role=dialog]').getBoundingClientRect();
                return dialog.left >= 0 && dialog.right <= innerWidth;
            }"""
        )
        console.save_evidence("trace-create-mobile")
        console._browser.call(
            lambda: console._page.set_viewport_size({"width": 1280, "height": 720})
        )

    def create():
        with console._page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.rstrip("/").endswith("/api/datasets")
            )
        ) as response:
            console._page.get_by_role("button", name="Create dataset", exact=True).click()
        return response.value.status, response.value.json()

    status, result = console._browser.call(create)
    assert status == 201, result

    def landed():
        datasets = workshop.call("list_datasets", {"search": "Selected trace source"})["datasets"]
        if not datasets:
            return None
        dataset = workshop.call("inspect_dataset", {"dataset": datasets[0]["id"]})
        return dataset if dataset["active"] else None

    dataset = settle(worker, landed)
    count = 2 if selection == "all" else 1
    assert dataset["active"]["rows"] == count
    assert dataset["source_kind"] == "traces"
    console.wait_until(
        f"() => location.pathname === '/datasets/{dataset['id']}' "
        "&& !!document.querySelector('article [data-cell-frame]')"
    )
    console.sees(f"{count} rows", exact=False)
    assert dataset["intent"] == "pending"
    assert dataset["brief"] == ""
    assert (
        dataset["capability"] is None
        if capability is None
        else dataset["capability"]["id"] == capability["id"]
    )
    console.sees("Intent pending", exact=False)
    console.press("Prepare with agent")
    prompt = console._browser.call(
        lambda: console._page.get_by_role("textbox", name="Preparation prompt").input_value()
    )
    assert dataset["id"] in prompt
    assert project in prompt
    assert dataset["active"]["id"] in prompt
    assert dataset["active"]["fingerprint"] in prompt
    assert live_api.url in prompt
    console._browser.call(
        lambda: console._page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    )
    console.press("Copy preparation prompt")
    console.sees("Prompt copied")
    assert (
        console._browser.call(lambda: console._page.evaluate("navigator.clipboard.readText()"))
        == prompt
    )
    console._browser.call(lambda: console._page.set_viewport_size({"width": 390, "height": 844}))
    console.wait_until(
        """() => {
            const dialog = document.querySelector('[role=dialog]').getBoundingClientRect();
            return dialog.left >= 0 && dialog.right <= innerWidth;
        }"""
    )
    console.save_evidence("trace-workshop-mobile")
    console._browser.call(lambda: console._page.set_viewport_size({"width": 1280, "height": 720}))


def test_trace_landing_refreshes_the_canvas_without_live_events(
    console, workshop, sample_agent, live_api, support_desk_llm, worker
):
    project = workshop.project()["id"]
    sample_agent.run("Refund order 42 please", api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    [trace] = workshop.call("query_traces", {})["traces"]
    dataset = Dataset.objects.create(
        project_id=project,
        name="Trace landing without live events",
        source_kind="traces",
        state="landing",
    )
    console._browser.call(
        lambda: console._page.route("**/api/datasets/*/events/", lambda route: route.abort())
    )
    console.goto(f"/datasets/{dataset.id}?projectId={project}")
    console.sees("Reading source data")
    land.land_traces(dataset, {"trace_ids": [trace["trace_id"]]})
    console.wait_until("() => !!document.querySelector('article [data-cell-frame]')")
    console.sees("1 rows", exact=False)
