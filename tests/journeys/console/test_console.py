import hashlib
import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from modal_shared.training_monitoring import fingerprint
from overbae.models import (
    Activity,
    BillingTelemetry,
    Cell,
    DataPartitionMember,
    DataPartitionPlan,
    Dataset,
    DatasetContext,
    DatasetPipeline,
    DatasetPipelineRun,
    DatasetPreparation,
    DedicatedDeployment,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    NativeEvaluationPlan,
    OperationalRun,
    Subscription,
)
from overbae.services import activity as activity_service
from overbae.services import training_monitoring
from overbae.tasks.datasets import reap_stuck_runs
from tests.fakes.clerk import PENDING_BROWSER_FORM_SCRIPT

from ..datasets import tickets, upload
from ..stack import drain, settle


def test_project_navigation_keeps_the_destination_without_a_project(console):
    console.goto("/inference?inf_search=needle")
    console.sees("Copy onboarding prompt")
    console.wait_until(
        "() => location.pathname === '/inference' && "
        "new URLSearchParams(location.search).get('inf_search') === 'needle'"
    )
    console.save_evidence("project-navigation-without-project")


def test_translated_onboarding_prompt_can_be_copied(console):
    console.goto("/inference")
    console.sees("Copy onboarding prompt")
    console._browser.call(
        lambda: console._page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    )
    console._browser.call(
        console._page.evaluate,
        """() => {
            const button = [...document.querySelectorAll('button')].find(
                element => element.textContent.includes('Copy onboarding prompt'));
            const texts = document.createTreeWalker(button, NodeFilter.SHOW_TEXT);
            const nodes = [];
            while (texts.nextNode()) nodes.push(texts.currentNode);
            for (const text of nodes) {
                if (!text.textContent.includes('Copy onboarding prompt')) continue;
                const translated = document.createElement('font');
                translated.textContent = 'Copiar instrucciones';
                text.replaceWith(translated);
            }
        }""",
    )
    console.press("Copiar instrucciones")
    console.sees("Paste the prompt into your coding agent to start setup.")
    assert "/setup.md" in console._browser.call(
        lambda: console._page.evaluate("navigator.clipboard.readText()")
    )
    console.save_evidence("translated-onboarding-prompt-copied")


def test_project_navigation_selects_the_only_project_without_leaving(console, workshop):
    project = workshop.project()["id"]
    console._browser.call(console._page.evaluate, "localStorage.removeItem('__selectedProject')")
    console.goto("/inference?inf_search=needle")
    console.wait_until(
        f"() => location.pathname === '/inference' && "
        f"new URLSearchParams(location.search).get('projectId') === '{project}' && "
        "new URLSearchParams(location.search).get('inf_search') === 'needle'"
    )
    console.save_evidence("project-navigation-destination")


def test_onboarding_prompt_retains_the_selected_project(console, rest_for, account_key):
    project = rest_for(account_key).request(
        "POST", "/api/projects/", json={"name": "Existing setup", "slug": "existing-setup"}
    )["id"]
    console.goto(f"/?projectId={project}")
    console._browser.call(
        console._page.context.grant_permissions, ["clipboard-read", "clipboard-write"]
    )
    console.press("Copy onboarding prompt")
    console.sees("Prompt copied")
    prompt = console._browser.call(console._page.evaluate, "navigator.clipboard.readText()")
    assert f"project_id: {project}" in prompt
    assert "dataset_id" not in prompt
    console.save_evidence("onboarding-selected-project")


@pytest.mark.parametrize("console_url", ["clerk"], indirect=True)
def test_sign_in_script_failure_offers_a_document_reload(browser, console_url):
    console = browser.page(console_url)
    attempts = []

    def external_request(route):
        if route.request.url.startswith(console_url):
            route.continue_()
        else:
            attempts.append(route.request.url)
            route.abort()

    console._browser.call(console._page.route, "**/*", external_request)
    try:
        console.goto("/login")
        console.sees("Sign in unavailable")
        before = len(attempts)
        console.press("Reload sign in")
        console.sees("Sign in unavailable")
        assert len(attempts) > before
        assert any("clerk" in url for url in attempts)
        console.save_evidence("sign-in-script-failure")
    finally:
        console.close()


@pytest.mark.parametrize("console_url", ["clerk"], indirect=True)
def test_pending_sign_in_offers_a_document_reload(browser, console_url):
    console = browser.page(console_url)
    pending = []

    def external_request(route):
        if route.request.url.startswith(console_url):
            route.continue_()
        elif "clerk" in route.request.url:
            pending.append(route)
        else:
            route.abort()

    console._browser.call(console._page.route, "**/*", external_request)
    try:
        console._browser.call(lambda: console._page.goto("/login", wait_until="domcontentloaded"))
        console.sees("Loading sign in")
        console.save_evidence("sign-in-pending")
        console._browser.call(
            lambda: console._page.get_by_role("button", name="Reload sign in").wait_for(
                state="visible", timeout=15000
            )
        )
        before = len(pending)

        def reload_sign_in():
            with console._page.expect_request(lambda request: "clerk" in request.url):
                console._page.get_by_role("button", name="Reload sign in").click()

        console._browser.call(reload_sign_in)
        console.sees("Loading sign in")
        assert len(pending) > before
        console.save_evidence("sign-in-pending-recovery")
    finally:
        console.close()


@pytest.mark.parametrize("console_url", ["clerk"], indirect=True)
@pytest.mark.parametrize("mode", ["signin", "signup"])
def test_pending_authentication_form_offers_recovery(browser, console_url, mode):
    console = browser.page(console_url)
    pending = []

    def external_request(route):
        if route.request.url.startswith(console_url):
            route.continue_()
        elif "clerk.browser.js" in route.request.url:
            route.fulfill(content_type="application/javascript", body=PENDING_BROWSER_FORM_SCRIPT)
        elif route.request.url.endswith("/pending-form"):
            pending.append(route)
        else:
            route.abort()

    console._browser.call(console._page.route, "**/*", external_request)
    try:

        def open_form():
            with console._page.expect_request("**/pending-form"):
                console._page.goto("/login?mode=signup" if mode == "signup" else "/login")

        console._browser.call(open_form)
        console.sees("Set up locally")
        assert pending
        console.save_evidence(f"authentication-form-pending-{mode}")
        console.sees("Loading sign in")

        def reload_form():
            with console._page.expect_request("**/pending-form"):
                console._page.get_by_role("button", name="Reload sign in").click()

        console._browser.call(reload_form)
        console.sees("Loading sign in")
        console.save_evidence(f"authentication-form-recovery-{mode}")
    finally:
        console._browser.call(lambda: console._page.unroute_all(behavior="ignoreErrors"))
        console.close()


def test_route_error_home_action_reloads_the_document(console):
    console.goto("/inference?page=broken")
    console.sees("Invalid link")
    console._browser.call(console._page.evaluate, "window.failedDocument = true")
    console._browser.call(lambda: console._page.get_by_role("link", name="Go to home").click())
    console.sees("Copy onboarding prompt")
    console.wait_until("() => location.pathname === '/' && !window.failedDocument")
    console.save_evidence("route-error-home-recovery")


def test_cancelled_plan_checkout_returns_to_account_settings(
    console, console_url, settings, stripe_api, rest_for, account_key
):
    settings.STRIPE_PRO_PRICE_ID = "price_journey"
    settings.FRONTEND_URL = console_url
    rest_for(account_key).request("POST", "/api/billing/checkout/")
    cancel_url = stripe_api.sessions[-1]["cancel_url"]
    console.goto(cancel_url)
    console.save_evidence("plan-checkout-cancel-return")
    console.sees("Settings")
    console.sees("Buy Pro")


@pytest.mark.parametrize("linked_to_stripe", [False, True])
def test_account_settings_only_offer_renewal_controls_for_managed_plans(
    console, account, linked_to_stripe
):
    user, _ = account
    Subscription.objects.create(
        user=user,
        status="active",
        stripe_subscription_id="sub_console" if linked_to_stripe else "",
        end_date=timezone.now() + timedelta(days=30),
    )
    console.goto("/settings")
    console.sees("Plan")
    console.sees("Pro")
    console.save_evidence(f"plan-renewal-controls-{linked_to_stripe}")
    assert console._browser.call(
        lambda: console._page.get_by_role("button", name="Cancel plan", exact=True).count()
    ) == int(linked_to_stripe)


def test_usage_history_keeps_small_charges_visible(console, account):
    user, _ = account
    BillingTelemetry.objects.create(user=user, service="inference", amount="-0.0000312")
    console.goto("/settings")
    console.sees("Usage history")
    console.sees("0.00312")
    console._browser.call(
        lambda: console._page.get_by_label("0.00312 credits", exact=True).wait_for()
    )
    console.save_evidence("usage-small-charge")


def test_dataset_pagination_waits_for_the_requested_rows(
    console, workshop, cli, sample_agent, tmp_path
):
    project = workshop.project()["id"]
    dataset = upload(cli, sample_agent, tickets(tmp_path, n=25))
    console.goto(f"/datasets/{dataset}?projectId={project}")
    console.sees("1–10 of 25 rows")
    pending = []
    console._browser.call(
        console._page.route, "**/rows/?*offset=10*", lambda route: pending.append(route)
    )
    try:
        console.press("Go to next page")
        console.sees("Loading page 2")
        assert console._browser.call(
            lambda: console._page.get_by_role("button", name="Go to next page").is_disabled()
        )
        assert pending
        console._browser.call(lambda: pending.pop().continue_())
        console.sees("11–20 of 25 rows")
        console.sees("Refund order 10 please", exact=False)
        console.save_evidence("dataset-pagination-next-page")
    finally:
        for route in pending:
            console._browser.call(route.continue_)
        console._browser.call(console._page.unroute, "**/rows/?*offset=10*")


def test_dataset_navigation_keeps_rows_with_their_source(
    console, workshop, cli, sample_agent, tmp_path, worker
):
    project = workshop.project()["id"]
    first_path = tickets(tmp_path, n=25).rename(tmp_path / "large-source.jsonl")
    first = upload(cli, sample_agent, first_path)
    second_path = tickets(tmp_path, n=3).rename(tmp_path / "small-source.jsonl")
    second = upload(cli, sample_agent, second_path)
    drain(worker)
    console.goto(f"/datasets/{first}?projectId={project}")
    console.sees("1–10 of 25 rows")
    console._browser.call(
        lambda: console._page.get_by_role("link", name="Datasets", exact=True).first.click()
    )
    console.click("small-source.jsonl")
    console.wait_until(f"() => location.pathname === '/datasets/{second}'")
    console.sees("3 rows × 2")
    console.wait_until("() => !document.body.innerText.includes('of 25 rows')")
    console.sees("Refund order 2 please", exact=False)
    console.save_evidence("dataset-navigation-matching-source")


def test_dataset_pagination_starts_at_the_first_row(console, workshop, cli, sample_agent, tmp_path):
    project = workshop.project()["id"]
    dataset = upload(cli, sample_agent, tickets(tmp_path, n=225))
    console.goto(f"/datasets/{dataset}?projectId={project}")
    console.sees("1–10 of 225 rows")
    console._browser.call(lambda: console._page.get_by_role("combobox").click())
    console._browser.call(
        lambda: console._page.get_by_role("option", name="100", exact=True).click()
    )
    console.sees("1–100 of 225 rows")
    console._browser.call(
        lambda: console._page.get_by_role(
            "button", name="Inspect row 99", exact=True
        ).scroll_into_view_if_needed()
    )
    console.save_evidence("dataset-pagination-before-next")
    console.press("Go to next page")
    console.sees("101–200 of 225 rows")
    console._browser.call(
        lambda: console._page.get_by_role("button", name="Inspect row 100", exact=True).wait_for()
    )
    console.save_evidence("dataset-pagination-first-row")
    console.wait_until("""() => {
        const row = document.querySelector('button[aria-label="Inspect row 100"]');
        const rect = row.getBoundingClientRect();
        return rect.top >= 0 && rect.bottom <= innerHeight;
    }""")


def test_evaluation_dataset_picker_waits_for_its_options(
    console, workshop, cli, sample_agent, tmp_path, fake_llm
):
    fake_llm.catalog_payload = [{"id": "qwen/qwen3-8b", "name": "Qwen: Qwen3 8B"}]
    project = workshop.project()["id"]
    upload(cli, sample_agent, tickets(tmp_path), "--intent", "eval")
    pending = []

    def hold_eval_datasets(route):
        if "intent=eval" in route.request.url:
            pending.append(route)
        else:
            route.continue_()

    console._browser.call(console._page.route, "**/datasets/?*", hold_eval_datasets)
    try:
        console.goto(f"/evaluations?projectId={project}")
        console.press("New evaluation")
        console._browser.call(
            lambda: console._page.get_by_role("button", name="Eval dataset", exact=True).click()
        )
        assert pending
        console.sees("Loading options")
        console.save_evidence("evaluation-dataset-picker-pending")
        console._browser.call(lambda: pending.pop().continue_())
        console._browser.call(
            lambda: console._page.get_by_role("radio", name="tickets.jsonl", exact=True).click()
        )
        console._browser.call(
            lambda: (
                console._page.get_by_role("button", name="Eval dataset", exact=True)
                .get_by_text("tickets.jsonl", exact=True)
                .wait_for()
            )
        )
        console.save_evidence("evaluation-dataset-picker-selected")
        console._browser.call(
            lambda: console._page.get_by_role(
                "button", name="Model being tested", exact=True
            ).click()
        )
        model_picker_id = console._browser.call(
            lambda: console._page.get_by_role(
                "button", name="Model being tested", exact=True
            ).get_attribute("aria-controls")
        )
        console._browser.call(
            lambda: (
                console._page.locator(f'[id="{model_picker_id}"]')
                .get_by_role("textbox", name="Search…", exact=True)
                .fill("qwen")
            )
        )
        console._browser.call(
            lambda: console._page.get_by_role("radio", name="Qwen3 8B", exact=True).click()
        )
        console._browser.call(
            lambda: (
                console._page.get_by_role("button", name="Model being tested", exact=True)
                .get_by_text("Qwen3 8B", exact=True)
                .wait_for()
            )
        )
        console.save_evidence("evaluation-model-search-selected")
    finally:
        for route in pending:
            console._browser.call(route.continue_)
        console._browser.call(console._page.unroute, "**/datasets/?*")


@pytest.mark.parametrize("clear_action", ["Clear search", "Clear filters"])
def test_inference_search_clear_restores_models(console, workshop, clear_action):
    project = workshop.project()["id"]
    dataset = Dataset.objects.create(project_id=project, name="Search fixture")
    job = FinetuningJob.objects.create(
        project_id=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-8B",
        status="succeeded",
        output_model_name="ft-search-result",
    )
    DeployedModel.objects.create(
        project_id=project, finetuning_job=job, model_id="ft-search-result", status="ready"
    )
    console.goto(f"/inference?projectId={project}&inf_search=missing-model")
    console.sees("No results")
    console.press(clear_action)
    console.sees("ft-search-result")
    console.wait_until("() => !new URLSearchParams(location.search).get('inf_search')")
    console.save_evidence(f"inference-{clear_action.lower().replace(' ', '-')}")


def test_embedded_onboarding_copies_setup_and_switches_to_data(console, console_url, live_api):
    console._browser.call(
        console._page.context.grant_permissions, ["clipboard-read", "clipboard-write"]
    )
    console.press("Copy onboarding prompt")
    console.sees("Prompt copied")
    prompt = console._browser.call(console._page.evaluate, "navigator.clipboard.readText()")
    assert prompt == (
        f"Set me up on Overmind\n\nusing the API at {live_api.url.rstrip('/')}: "
        f"{console_url.rstrip('/')}/setup.md"
    )
    console.wait_until("""() => {
        const panel = document.querySelector('section[aria-label="Get started"]');
        return panel.querySelector('a[href$="/setup.md"]') &&
            [...panel.querySelectorAll('img')].every(img => img.complete && img.naturalWidth > 0);
    }""")
    console.save_evidence("embedded-onboarding-desktop")
    console._browser.call(console._page.set_viewport_size, {"width": 390, "height": 844})
    console.wait_until("""() => {
        const panel = document.querySelector('section[aria-label="Get started"]');
        return panel.scrollWidth <= panel.clientWidth && panel.getBoundingClientRect().right <= innerWidth;
    }""")
    console.save_evidence("embedded-onboarding-mobile")
    console._browser.call(lambda: console._page.get_by_role("tab", name="Data", exact=True).click())
    console.sees("Create dataset")
    console.wait_until("() => !document.querySelector('[role=dialog]')")
    console._browser.call(
        lambda: console._page.get_by_role("tab", name="Agent", exact=True).click()
    )
    console.sees("Copy onboarding prompt")
    console.healthy()


def test_activity_outcomes_and_setup_progress_survive_console_reload(
    console, workshop, worker, rest_for
):
    project = workshop.project()["id"]
    workshop.call("list_datasets")
    activity = workshop.read("overmind://activity")
    assert {item["kind"] for item in activity["results"]} >= {
        "agent_connected",
        "repository_synced",
        "capabilities_available",
    }
    before = activity["count"]
    for _ in range(2):
        workshop.call("list_datasets")
    assert workshop.read("overmind://activity")["count"] == before

    console.goto(f"/?projectId={project}")
    console.press("unread notification")
    console.sees("connected to Overmind", exact=False)
    workshop.call(
        "create_eval_matrix_row",
        {"name": "Visible activity check", "type": "llm", "source": "The answer is grounded."},
    )
    console.sees("added", exact=False)
    console.sees("Visible activity check")
    console.press("Mark read")
    settle(worker, lambda: workshop.read("overmind://activity")["unseen_count"] == 0)

    console.goto(f"/?projectId={project}")
    console.press("Notifications")
    console.sees("Visible activity check")
    console.click("Visible activity check")
    console.wait_until("() => location.pathname.startsWith('/evaluations')")
    console.press("Notifications")
    console.wait_until("() => !document.body.textContent.includes('Setup progress')")
    onboarding_run = str(uuid.uuid4())
    progress = workshop.call(
        "report_onboarding_progress", {"run_id": onboarding_run, "completed_steps": 2}
    )
    reporter = progress["milestones"][2]["actor_name"]
    assert reporter
    console.goto(f"/?projectId={project}")
    console.sees("Onboarding · Inspect repository")
    console.sees("2 of 10")
    console._browser.call(
        lambda: (
            console._page.get_by_label("Onboarding status", exact=True)
            .get_by_text(reporter, exact=True)
            .wait_for()
        )
    )
    workshop.call("report_onboarding_progress", {"run_id": onboarding_run, "completed_steps": 9})
    console.sees("Onboarding · Instrument capabilities")
    rest_for(workshop.api_key).request(
        "POST",
        "/api/v1/sync",
        json={
            "project_id": project,
            "version": "1",
            "capabilities": [
                {
                    "slug": "onboarding-capability",
                    "name": "Onboarding capability",
                    "entrypoint_fn": "support.run",
                }
            ],
        },
    )
    console.wait_until("""() => {
        return [...document.querySelectorAll('h3')].some(node => node.textContent.includes('Onboarding capability')) && document.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow') === '9' && !document.body.textContent.includes('Copy onboarding prompt');
    }""")
    console.save_evidence("activity-and-onboarding")
    console._browser.call(console._page.set_viewport_size, {"width": 390, "height": 844})
    console.wait_until("""() => {
        const strip = document.querySelector('[aria-label="Onboarding status"]');
        if (!strip) return false;
        const rect = strip.getBoundingClientRect();
        return rect.left >= 0 && rect.right <= innerWidth && strip.scrollWidth <= strip.clientWidth;
    }""")
    console.save_evidence("onboarding-mobile")
    workshop.call("report_onboarding_progress", {"run_id": onboarding_run, "completed_steps": 10})
    console.wait_until(
        "() => !document.querySelector('[aria-label=\"Onboarding status\"]') && [...document.querySelectorAll('h3')].some(node => node.textContent.includes('Onboarding capability'))"
    )
    console.save_evidence("onboarding-complete-capabilities")


def test_agent_changes_update_visible_assets_without_reload(console, workshop):
    project = workshop.project()["id"]
    console.goto(f"/datasets?projectId={project}")
    console.sees("Datasets", exact=False)
    console._browser.call(console._page.evaluate, "window.activityDocument = 'unchanged'")
    dataset = workshop.call(
        "start_dataset",
        {"name": "Live agent source", "brief": "Review live agent changes", "intent": "explore"},
    )["dataset"]["id"]
    console.sees("Live agent source")
    console.wait_until(
        "() => [...document.querySelectorAll('.activity-highlight')].some(row => row.textContent.includes('Live agent source'))"
    )
    workshop.call("update_dataset", {"dataset": dataset, "name": "Renamed live source"})
    console.sees("Renamed live source")
    console.wait_until(
        "() => !document.body.textContent.includes('Live agent source') && window.activityDocument === 'unchanged'"
    )
    console.save_evidence("agent-live-asset-update")
    console._browser.call(lambda: console._page.context.set_offline(True))
    workshop.call("update_dataset", {"dataset": dataset, "name": "Reconnected live source"})
    console._browser.call(lambda: console._page.context.set_offline(False))
    console.sees("Reconnected live source")
    console.wait_until("() => window.activityDocument === 'unchanged'")
    console.press("unread notification")
    console.sees("Reconnected live source", exact=False)
    console.save_evidence("agent-live-notifications")


def test_dataset_inventory_follows_upload_bytes_and_publication(
    console, workshop, rest_for, worker
):
    project = workshop.project()["id"]
    dataset = workshop.call(
        "start_dataset", {"name": "Live upload", "brief": "Inspect uploaded rows"}
    )["dataset"]["id"]
    rest = rest_for(workshop.api_key)
    console.goto(f"/datasets?projectId={project}")
    console.sees("Waiting for source")
    data = b'[{"value":1},{"value":2}]'
    receipt = rest.request(
        "POST",
        "/api/dataset-transfers/",
        json={
            "project": project,
            "dataset": dataset,
            "request_key": "live-upload",
            "filename": "rows.json",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    console.sees("Uploading", exact=False)
    rest.request(
        "PUT",
        f"/api/dataset-transfers/{receipt['id']}/chunk/?offset=0",
        data=data[:10],
        headers={"Content-Type": "application/octet-stream"},
    )
    console.wait_until(
        "() => Number(document.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow')) > 0"
    )
    console.save_evidence("dataset-upload-progress")
    rest.request(
        "PUT",
        f"/api/dataset-transfers/{receipt['id']}/chunk/?offset=10",
        data=data[10:],
        headers={"Content-Type": "application/octet-stream"},
    )
    rest.request("POST", f"/api/dataset-transfers/{receipt['id']}/complete/", json={})
    drain(worker)
    console.sees("Ready")
    console.wait_until(
        "() => !document.body.textContent.includes('Uploading') && [...document.querySelectorAll('td')].some(cell => cell.textContent === '2')"
    )
    console.save_evidence("dataset-upload-ready")


def test_evaluations_share_runs_navigation_and_probability_metrics(console, workshop):
    project = workshop.project()["id"]
    dataset = Dataset.objects.create(
        project_id=project, name="Evaluation navigation fixture", intent="eval"
    )
    cell = Cell.objects.create(
        dataset=dataset, state="ok", position=0, title="Final suite", rows=10
    )
    Cell.objects.create(dataset=dataset, state="ok", position=1, title="Later data", rows=999)
    calibration_dataset = Dataset.objects.create(
        project_id=project, name="Calibration navigation fixture", intent="eval"
    )
    calibration_cell = Cell.objects.create(
        dataset=calibration_dataset, state="ok", position=0, title="Calibration suite", rows=3
    )
    EvalRun.objects.create(
        project_id=project, dataset=dataset, name="Response evaluation fixture", status="completed"
    )
    metric = {"mean": 1.8, "decisions": 7, "groups": 5}
    coverage = {
        "expected": 10,
        "scored": 7,
        "missing_predictions": 1,
        "invalid_predictions": 1,
        "incompatible_inputs": 1,
        "metrics": {"cross_entropy": metric},
    }
    plan = NativeEvaluationPlan.objects.create(
        project_id=project,
        final_cell=cell,
        calibration_cell=calibration_cell,
        name="Probability evaluation fixture",
        state="completed",
        config={
            "baseline": "base",
            "suites": {
                "final": {"cell": str(cell.pk), "rows": 10},
                "calibration": {"cell": str(calibration_cell.pk), "rows": 3},
            },
            "participants": [
                {"key": "base", "name": "Base", "model": "Qwen/Qwen3-0.6B", "kind": "external"},
                {
                    "key": "trained",
                    "name": "Trained",
                    "model": "Qwen/Qwen3-0.6B",
                    "kind": "external",
                },
            ],
        },
        calls={"score": {"state": "completed", "observed_at": timezone.now().isoformat()}},
        results={
            "comparisons": {
                "trained": {
                    "raw": {
                        "baseline": {"benchmarks": {"Soft targets": coverage}},
                        "candidate": {"benchmarks": {"Soft targets": coverage}},
                        "benchmarks": {
                            "Soft targets": {
                                "metrics": {
                                    "cross_entropy": {
                                        "candidate_minus_baseline": -0.1,
                                        "decisions": 7,
                                        "groups": 5,
                                    }
                                }
                            }
                        },
                    }
                }
            }
        },
    )
    console.goto(f"/evaluations?projectId={project}&view=decisions")
    console.sees("Response evaluation fixture")
    console.sees("Probability evaluation fixture")
    console.wait_until(
        """() => ![...document.querySelectorAll('[role=tab]')].some(el => el.textContent === 'Decisions')"""
    )
    console.click("Response evaluation fixture")
    console.sees("Re-run")
    console.sees("Optimise")
    console.goto(f"/evaluations?projectId={project}")
    console.click("Probability evaluation fixture")
    console.sees("Evaluation navigation fixture")
    console.sees("Calibration navigation fixture")
    console.wait_until(f"""() => {{
        const details = document.querySelector('[aria-label="Run details"]');
        return details && details.innerText.includes('Models')
            && details.innerText.includes('Baseline')
            && details.innerText.includes('10 rows')
            && details.innerText.includes('3 rows')
            && details.innerText.includes('Created')
            && details.innerText.includes('Completed')
            && !details.innerText.includes('999')
            && !details.innerText.includes('Judge')
            && [...details.querySelectorAll('a')].some(a =>
                a.href.includes('/datasets/{dataset.id}') && a.href.includes('cell={cell.id}'));
    }}""")
    console.sees("Cross entropy")
    console.sees("1.8000")
    console.sees("7 / 10")
    console._browser.call(
        lambda: console._page.get_by_role("button", name="Trained: 7 / 10 scored").focus()
    )
    console.sees("1 missing · 1 invalid · 1 incompatible")
    console.wait_until(f"() => location.pathname.endsWith('/runs/{plan.id}')")
    console.wait_until("""() => document.querySelectorAll('table:not([aria-busy=true])').length === 1
        && document.querySelector('[aria-label="Benchmark datapoints"]')
        && !document.body.innerText.includes('Download all metrics')
        && !document.body.innerText.includes('Frozen inputs, model identities and execution receipts')
        && !document.body.innerText.includes('Calibration, class and slice diagnostics')
        && !document.body.innerText.includes('Recorded costs and unreported components')
        && !document.body.innerText.includes('Measure workload')
        && getComputedStyle(document.querySelector('h1')).fontFamily.includes('Mondwest')
        && getComputedStyle(document.querySelector('h2')).fontFamily.includes('Geist Pixel')""")
    console.save_evidence("evaluation-probability-metrics")
    console.goto(f"/evaluations?projectId={project}")
    console.press("New evaluation")
    console.click("Model probabilities")
    console.sees("Final suite", exact=False)
    console.sees("Calibration suite", exact=False)
    console.sees("Baseline", exact=False)
    console.sees("Save draft")
    console.wait_until(
        """() => getComputedStyle(document.querySelector('[role=dialog] h2')).fontFamily.includes('Geist Pixel')"""
    )
    assert not console.page_errors
    assert not console.server_errors


def test_the_console_shows_the_synced_agent_and_its_traces(
    console, cli, mcp_for, sample_agent, live_api, support_desk_llm, worker
):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    project = mcp_for(cli.project_key(sample_agent)).project()["id"]
    sample_agent.run("Refund order 42 please", api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)

    console.goto(f"/capabilities?projectId={project}")
    for truth in sample_agent.truth["capabilities"].values():
        console.sees(truth["name"])

    console.goto(f"/observability?projectId={project}")
    console.click("Signals")
    console.sees("Signal checks")
    console.click("Sessions")
    console.click("Traces")
    console.sees("handle_ticket")


def test_a_file_uploaded_in_the_console_becomes_a_usable_dataset(
    console, cli, mcp_for, sample_agent, worker, tmp_path
):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    mcp = mcp_for(cli.project_key(sample_agent))
    project = mcp.project()["id"]

    console.goto(f"/datasets?projectId={project}")
    console.command("New dataset")
    console.fill("#dataset-name", "Console upload")
    console.upload('[role="dialog"] input[type="file"]', tickets(tmp_path))
    console.sees("3 rows")
    console.choose("#dataset-purpose", "Evaluation")
    console.press("Create dataset")

    def landed():
        for dataset in mcp.call("list_datasets", {})["datasets"]:
            if dataset["name"] == "Console upload":
                inspected = mcp.call("inspect_dataset", {"dataset": dataset["id"]})
                return inspected if inspected["active"] else None
        return None

    inspected = settle(worker, landed)
    assert inspected["active"]["rows"] == 3
    assert inspected["intent"] == "eval"


def test_a_console_brief_survives_its_first_cli_attachment(
    console, workshop, cli, sample_agent, worker, tmp_path
):
    project = workshop.project()["id"]
    brief = "Classify refund requests; retain uncertain labels for review."
    console.goto(f"/datasets?projectId={project}")
    console.command("New dataset")
    console.fill("#dataset-name", "Brief before data")
    console.fill("#dataset-brief", brief)
    console.press("Create dataset")

    def draft():
        return next(
            (
                row
                for row in workshop.call("list_datasets", {})["datasets"]
                if row["name"] == "Brief before data"
            ),
            None,
        )

    dataset = settle(worker, draft)["id"]
    before = workshop.call("inspect_dataset", {"dataset": dataset})
    assert before["brief"] == brief and before["active"] is None
    assert upload(cli, sample_agent, tickets(tmp_path), "--dataset", dataset) == dataset
    drain(worker)
    after = workshop.call("inspect_dataset", {"dataset": dataset})
    assert after["brief"] == brief
    assert after["active"]["rows"] == 3
    assert after["intent"] == before["intent"]
    console.goto(f"/datasets/{dataset}?projectId={project}")
    console.sees("Brief before data")
    console.sees("Refund order 0 please", exact=False)


def test_decision_data_selects_native_training_controls(
    console, workshop, cli, sample_agent, worker, tmp_path, rest_for
):
    project = workshop.project()["id"]
    path = tmp_path / "decision.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "decision": {
                        "state": f"Refund request {i}",
                        "question": "Approve refund?",
                        "kind": "noul",
                        "options": ["No", "Yes"],
                        "target_probabilities": [0.25, 0.75],
                        "target_semantics": "annotator_distribution",
                    }
                }
            )
            + "\n"
            for i in range(12)
        )
    )
    dataset = upload(cli, sample_agent, path, "--intent", "train")
    drain(worker)
    recommendation = rest_for(cli.project_key(sample_agent)).request(
        "POST", "/api/finetuning-jobs/recommend/", json={"dataset_id": dataset}
    )
    readiness = workshop.call("check_finetune_readiness", {"dataset": dataset})
    assert recommendation["task_type"] == readiness["task_type"] == "unknown"
    assert recommendation["task_type_source"] == readiness["task_type_source"] == "unknown"
    assert recommendation["dataset"]["rows"] == 12
    assert recommendation["benchmark_snapshot"]["generated_at"] is None
    assert recommendation["candidates"] == readiness["recommendations"] == []
    assert readiness["dataset"]["validation"]["valid"] and readiness["n_candidates"] > 0
    DatasetContext.objects.update_or_create(
        dataset_id=dataset,
        defaults={
            "project_id": project,
            "task_type": "classification",
            "task_type_source": "semantic",
            "extracted_at": timezone.now(),
        },
    )
    recommendation = rest_for(cli.project_key(sample_agent)).request(
        "POST", "/api/finetuning-jobs/recommend/", json={"dataset_id": dataset}
    )
    readiness = workshop.call("check_finetune_readiness", {"dataset": dataset})
    assert recommendation["task_type"] == readiness["task_type"] == "classification"
    assert readiness["training_contract"]["inference_contract"] == "decision"
    assert "Qwen/Qwen3-0.6B" in {model for tier in readiness["catalog"].values() for model in tier}
    assert "google/gemma-4-31B-it" not in {
        model for tier in readiness["catalog"].values() for model in tier
    }
    console.goto(f"/training?projectId={project}&train=true&datasetId={dataset}")
    console.sees("Choose training data, model and evaluations.")
    console.wait_until("""() => document.querySelector(
        '[aria-label="Run pre-training baseline evaluation"]')?.getAttribute('data-state') === 'checked'""")
    console.sees("Measure base-model performance before training.")
    console.sees("Native probability training · Modal LoRA")
    console.sees("Training mode")
    console.sees("Decision", exact=True)
    console.sees("Classification", exact=True)
    console.sees("No benchmark data", exact=False)
    console.wait_until("""() => !document.body.innerText.includes("Can't be trained on yet")
        && !document.body.innerText.includes("Validating")""")
    assert not FinetuningJob.objects.filter(project_id=project).exists()


def test_api_key_remains_private_in_session_replay(console, workshop, replay_recorder):
    project = workshop.project()["id"]
    console.goto(f"/projects/{project}?projectId={project}")
    console.press("New API key")
    console._browser.call(lambda: console._page.add_script_tag(path=str(replay_recorder)))
    console._browser.call(
        console._page.evaluate,
        """() => {
            window.replayEvents = [];
            window.stopReplay = ReplayRecorder.record({
                emit: event => window.replayEvents.push(event),
                blockClass: 'ph-no-capture', maskTextClass: 'ph-mask', maskAllInputs: true
            });
        }""",
    )
    console.press("Create API key")
    console.sees("Your new API key:")
    console.wait_until("() => window.replayEvents.some(event => event.type === 3)")
    leaked = console._browser.call(
        console._page.evaluate,
        """() => {
            ReplayRecorder.record.takeFullSnapshot();
            const key = document.querySelector('[role="dialog"] code').textContent;
            const leaked = JSON.stringify(window.replayEvents).includes(key);
            window.stopReplay();
            return leaked;
        }""",
    )
    console.press("Done")
    console.wait_until("() => !document.querySelector('[role=\"dialog\"]')")
    console.save_evidence("api-key-replay-privacy")
    assert leaked is False, "Session replay captured the API key"


def test_native_monitoring_evidence_remains_readable_after_cancellation(console, workshop):
    project = workshop.project()["id"]
    group = uuid.uuid4()
    job = FinetuningJob.objects.create(
        project_id=project,
        dataset=Dataset.objects.create(project_id=project, name="Decision receipts"),
        base_model="Qwen/Qwen3-0.6B",
        status="running",
        group_id=group,
        hyperparameters={"objective": "decision_cross_entropy"},
        progress={"activity": [{"ts": 1, "message": "Booting and verifying"}]},
    )
    examples = [
        {
            "row": 0,
            "input": "Refund order 42",
            "reference": [0.25, 0.75],
            "output": [0.2, 0.8],
            "status": "completed",
        }
    ]
    training_monitoring.ingest(
        job,
        {
            "checks": [
                {
                    "key": "1:development:4",
                    "attempt": 1,
                    "stream": "development",
                    "step": 4,
                    "state": "completed",
                    "policy_fingerprint": "a" * 64,
                    "sample_fingerprint": "b" * 64,
                    "coverage": {"scored": 4, "expected": 4},
                    "metrics": {
                        "eval_loss": 0.625,
                        "hard_label_accuracy": 0.75,
                        "distribution_decisions": 4,
                        "mean_decisions": 0,
                        "cross_entropy": 0.625,
                        "brier": 0.125,
                    },
                    "artifact": {"sha256": fingerprint(examples), "examples": examples},
                }
            ]
        },
    )
    console.goto(f"/training?projectId={project}&groupId={group}&jobId={job.pk}")
    console.sees("Development loss")
    console.sees("0.6250")
    console.sees("Running — Booting and verifying")
    check = job.validation_runs.get()
    evidence = workshop.call(
        "inspect_training_progress", {"job": str(job.pk), "check": str(check.pk)}
    )
    assert evidence["progress"]["items"] == examples
    console.press("Cancel")
    console.sees("Requests cancellation. Completed checks and saved checkpoints remain available.")
    console.press("Cancel Job")
    console.sees("Cancelled")
    console.wait_until("() => !document.body.innerText.includes('Booting and verifying')")
    job.refresh_from_db()
    assert job.status == "cancelled"
    console.goto(f"/training?projectId={project}&groupId={group}&jobId={job.pk}")
    console.sees("Development loss")
    console.sees("0.6250")
    assert (
        workshop.call("inspect_training_progress", {"job": str(job.pk), "check": str(check.pk)})[
            "progress"
        ]["items"]
        == examples
    )


def test_project_surfaces_render_without_browser_or_server_errors(console, workshop):
    project = workshop.project()["id"]
    for route, text in [
        ("datasets", "Datasets"),
        ("training", "Train model"),
        ("evaluations", "Evaluations"),
        ("inference", "Inference"),
        ("optimiser", "Optimiser"),
    ]:
        console.goto(f"/{route}?projectId={project}")
        console.sees(text)
        console.healthy()
        console.save_evidence(f"project-surface-{route}")


def test_decision_deployment_without_training_data_never_invents_an_example(console, workshop):
    project = workshop.project()["id"]
    deployed = DeployedModel.objects.create(
        project_id=project,
        model_id="ft-titanic-decision",
        base_model_id="Qwen/Qwen3.5-0.8B",
        status="ready",
        inference_contract="decision",
        max_model_len=4096,
        gpu_type="H100",
        quantization="bf16",
    )
    console.goto(f"/inference/{deployed.pk}?projectId={project}")
    console.sees(deployed.model_id)
    console.press("Serverless API snippet")
    console.sees("TypeSafe systemone. Point the SDK base URL at this API.")
    console.sees(
        "A complete example input is unavailable for this model’s pinned training version."
    )
    console.wait_until(
        "() => !document.querySelector('[role=dialog]').innerText.includes('/chat/completions')"
    )
    console.wait_until(
        "() => !document.querySelector('[role=dialog]').innerText.includes('Use live alias')"
    )
    console.wait_until(
        "() => getComputedStyle(document.querySelector('[role=dialog]')).opacity === '1'"
    )
    console.healthy()
    console.save_evidence("decision-inference-snippet")


def test_data_onboarding_uploads_once_and_hands_off_training_preparation(console, tmp_path):
    console.goto("/projects")
    console.press("New project")
    console._browser.call(
        lambda: console._page.get_by_label("Project name", exact=True).fill(
            "Training onboarding fixture"
        )
    )
    console.upload('input[type="file"]', tickets(tmp_path))
    console.sees("3 rows")
    console.wait_until(
        "() => document.querySelector('[role=dialog]').getAnimations().every(a => a.playState !== 'running')"
    )
    console.save_evidence("data-onboarding-upload")
    console.press("Create dataset")
    console.sees("Prepare your training data")
    dataset = Dataset.objects.get(name="Training onboarding fixture")
    assert dataset.intent == "train"
    assert dataset.brief == (
        "Explore the uploaded data and determine how best to prepare it for training."
    )
    assert dataset.capability_id is None
    console.wait_until(f"() => location.pathname.includes('/datasets/{dataset.id}')")
    console._browser.call(
        lambda: console._page.evaluate("""() => {
        window.copiedOnboardingPrompt = '';
        navigator.clipboard.write = async items => {
            window.copiedOnboardingPrompt = await (await items[0].getType('text/plain')).text();
        };
        navigator.clipboard.writeText = async text => { window.copiedOnboardingPrompt = text; };
    }""")
    )
    console.press("Copy onboarding prompt")
    console.wait_until("() => window.copiedOnboardingPrompt.length > 0")
    prompt = console._browser.call(lambda: console._page.evaluate("window.copiedOnboardingPrompt"))
    assert prompt.startswith(
        f"Set me up on Overmind with my uploaded data\n(project_id: {dataset.project_id},\ndataset_id: {dataset.id})"
    )
    assert prompt.endswith("/setup.md")
    assert "api-key" not in prompt.lower()
    console.save_evidence("data-onboarding-handoff")
    console._browser.call(lambda: console._page.set_viewport_size({"width": 390, "height": 844}))
    console.wait_until(
        "() => document.querySelector('[role=dialog]').getAnimations().every(a => a.playState !== 'running')"
    )
    bounds = console._browser.call(
        lambda: console._page.evaluate("""() => {
        const d = document.querySelector('[role=dialog]'); const r = d.getBoundingClientRect();
        return {left:r.left, right:r.right, viewport:innerWidth, width:getComputedStyle(d).width, maxWidth:getComputedStyle(d).maxWidth, translate:getComputedStyle(d).translate, transform:getComputedStyle(d).transform};
    }""")
    )
    assert bounds["left"] >= 0 and bounds["right"] <= bounds["viewport"], bounds
    console.save_evidence("data-onboarding-handoff-mobile")
    console._browser.call(
        lambda: (
            console._page.get_by_role("dialog")
            .get_by_role("button", name="Close", exact=True)
            .last.click()
        )
    )
    console.wait_until("() => !location.search.includes('prepare=')")
    console.sees("Training onboarding fixture")
    console.healthy()


def test_empty_dataset_inventory_onboards_into_the_selected_project(console, workshop, tmp_path):
    project = workshop.project()["id"]
    console.goto(f"/datasets?projectId={project}")
    console.sees("Get started with data")
    console.upload('input[type="file"]', tickets(tmp_path))
    console.sees("3 rows")
    console.save_evidence("dataset-empty-onboarding")
    console.press("Create dataset")
    console.sees("Prepare your training data")
    dataset = Dataset.objects.get(project_id=project)
    console.wait_until(f"() => location.pathname.includes('/datasets/{dataset.id}')")
    console.goto(f"/datasets?projectId={project}&ds_search=no-matching-dataset")
    console.wait_until("() => !document.body.textContent.includes('Get started with data')")
    console.healthy()


def test_dataset_role_filter_uses_recorded_outputs(console, workshop):
    project = workshop.project()["id"]
    source = Dataset.objects.create(project_id=project, name="Raw evidence", intent="explore")
    source_cell = Cell.objects.create(dataset=source, position=0, state="ok", fingerprint="a" * 64)
    raw = Dataset.objects.create(project_id=project, name="Unused source", intent="explore")
    Cell.objects.create(dataset=raw, position=0, state="ok", fingerprint="c" * 64)
    output = Dataset.objects.create(project_id=project, name="Shared output", intent="explore")
    output_cell = Cell.objects.create(dataset=output, position=1, state="ok", fingerprint="b" * 64)
    Dataset.objects.create(project_id=project, name="Training by name only", intent="train")
    plan = DataPartitionPlan.objects.create(
        project_id=project,
        name="Recorded roles",
        request_key="inventory-roles",
        source_cell=source_cell,
        source_fingerprint=source_cell.fingerprint,
        state="completed",
    )
    for role in ("train", "calibration", "final", "development"):
        DataPartitionMember.objects.create(plan=plan, role=role, cell=output_cell)
    console.goto(f"/datasets?projectId={project}")
    console.sees("Raw evidence")
    console.wait_until("() => !document.body.textContent.includes('Shared output')")
    for label in ("Training", "Calibration", "Evaluation", "Development"):
        console.choose('[aria-label="Filter by role"]', label)
        console.sees("Raw evidence")
        console.wait_until(
            "() => !document.body.textContent.includes('Training by name only') && !document.body.textContent.includes('Unused source')"
        )
    console.save_evidence("dataset-multiple-roles")
    console.choose('[aria-label="Filter by role"]', "Source")
    console.sees("Unused source")
    console.wait_until("() => !document.body.textContent.includes('Shared output')")
    console.choose('[aria-label="Filter by role"]', "Unassigned")
    console.sees("Training by name only")
    console.wait_until(
        "() => !document.body.textContent.includes('Shared output') && !document.body.textContent.includes('Raw evidence')"
    )
    console.save_evidence("dataset-unassigned-role")
    console.goto(f"/datasets/{output.pk}?projectId={project}")
    console.sees("Shared output", exact=False)


def test_activity_connects_pipeline_sequence(console, workshop):
    project = workshop.project()["id"]
    dataset = Dataset.objects.create(project_id=project, name="Connected work")
    source = Cell.objects.create(dataset=dataset, position=0, state="ok", fingerprint="a" * 64)
    pipeline = DatasetPipeline.objects.create(
        project_id=project, name="Features", request_key="features"
    )
    for key, kind, action in (
        ("upload", "source_uploaded", "Uploaded source"),
        ("import", "source_ready", "Source ready"),
        ("save", "asset_changed", "Saved a transformation for"),
    ):
        activity_service.record(
            key=key,
            project_id=project,
            kind=kind,
            action=action,
            assets=[activity_service.asset("dataset", dataset.pk, dataset.name)],
        )
    activity_service.record(
        key="unrelated-edit",
        project_id=project,
        kind="asset_changed",
        action="Updated dataset",
        assets=[activity_service.asset("dataset", dataset.pk, dataset.name)],
    )
    for index in range(2):
        run = DatasetPipelineRun.objects.create(
            dataset=dataset,
            source=source,
            pipeline=pipeline,
            request_key=f"sequence-{index}",
            state="completed",
        )
        activity_service.record(
            key=f"sequence-{index}",
            project_id=project,
            kind="task_requested",
            action="Requested a transformation of",
            actor_kind="codex",
            actor_name="Codex",
            task_kind="dataset_pipeline",
            task_id=run.pk,
            assets=[activity_service.asset("dataset", dataset.pk, dataset.name)],
        )
    records = workshop.read("overmind://activity")["results"]
    linked = [item for item in records if item["sequence_key"]]
    assert len(linked) == 5
    assert len({item["sequence_key"] for item in linked}) == 1
    assert linked[0]["sequence_key"]
    assert all(not item["sequence_key"] for item in records if item["action"] == "Updated dataset")
    console.goto(f"/datasets?projectId={project}")
    console.press("unread notification")
    console.wait_until("() => document.querySelectorAll('[data-sequence-key]').length === 5")
    console.save_evidence("connected-pipeline-events")


def test_dataset_preparation_persists_between_jobs_until_agent_finishes(
    console, workshop, cli, sample_agent, worker, tmp_path
):
    project = workshop.project()["id"]
    dataset = workshop.call(
        "start_dataset",
        {"name": "Continuous preparation", "brief": "Prepare support rows", "intent": "train"},
    )["dataset"]["id"]
    console.goto(f"/datasets?projectId={project}")
    console.sees("Continuous preparation")
    args = {"dataset": dataset, "request_key": "continuous-preparation"}
    started = workshop.call("start_dataset_preparation", args)["preparation_workflow"]
    assert (
        workshop.call("start_dataset_preparation", args)["preparation_workflow"]["id"]
        == started["id"]
    )
    console.sees("Preparing")
    workflow = {"dataset": dataset, "preparation_id": started["id"]}
    assert workshop.call_result(
        "update_dataset_preparation", {**workflow, "state": "completed"}
    ).isError
    assert workshop.call_result(
        "start_dataset_preparation", {**args, "request_key": "another"}
    ).isError
    upload(cli, sample_agent, tickets(tmp_path), "--dataset", dataset)
    drain(worker)
    source = workshop.call("inspect_dataset", {"dataset": dataset})["active"]
    console.sees("Preparing")
    result = workshop.call(
        "import_dataset_version",
        {
            "dataset": dataset,
            "source_cell": source["id"],
            "source_fingerprint": source["fingerprint"],
            "name": "Prepared support",
            "request_key": "continuous-import",
            "provenance": "Journey creates source-bound support messages",
            "imported_rows": [
                {
                    "source_row": i,
                    "messages": [
                        {"role": "user", "content": f"Refund order {i}"},
                        {"role": "assistant", "content": "Your refund is ready."},
                    ],
                }
                for i in range(3)
            ],
        },
    )
    drain(worker)
    receipt = workshop.call("get_job", {"kind": "dataset_pipeline", "id": result["job"]["id"]})
    assert receipt["status"] == "completed", receipt
    console.sees("Preparing")
    current = workshop.call("inspect_dataset_workbench", {"dataset": dataset})[
        "preparation_workflow"
    ]
    assert current["state"] == "preparing"
    events = workshop.read("overmind://activity")["results"]
    preparation_events = [e for e in events if e["kind"] == "dataset_preparation"]
    assert len(preparation_events) == 1
    event_id = preparation_events[0]["id"]
    assert not [e for e in events if e["task_kind"] == "dataset_pipeline"]
    workshop.call("update_dataset_preparation", {**workflow, "state": "paused"})
    console.sees("Preparation paused")
    workshop.call("update_dataset_preparation", {**workflow, "state": "preparing"})
    console.sees("Preparing")
    console.press("unread notification")
    console.wait_until(
        f"() => document.querySelector('[data-activity-id=\"{event_id}\"]')?.dataset.activityStatus === 'running'"
    )
    console.wait_until(
        "() => document.getAnimations().every(a => a.effect?.getTiming().iterations === Infinity || a.playState !== 'running')"
    )
    console.save_evidence("dataset-preparation-active")
    output = workshop.call("inspect_dataset", {"dataset": dataset})["active"]["id"]
    completion = {**workflow, "state": "completed", "outputs": [output]}
    workshop.call("update_dataset_preparation", completion)
    workshop.call("update_dataset_preparation", completion)
    console.wait_until(
        f"() => document.querySelector('[data-activity-id=\"{event_id}\"]')?.dataset.activityStatus === 'completed'"
    )
    console.sees("Ready")
    console.save_evidence("dataset-preparation-completed")
    assert workshop.call_result(
        "update_dataset_preparation", {**workflow, "state": "preparing"}
    ).isError
    console.healthy()


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


def test_evaluation_scope_link_settles_on_trajectory_and_supports_repeat_clicks(console, workshop):
    project = workshop.project()["id"]
    console.goto(f"/evaluations?projectId={project}&view=matrix")
    link = console._page.locator('a[href*="trajectoryNode="]').first
    console._browser.call(link.wait_for)
    href = console._browser.call(link.get_attribute, "href")
    console._browser.call(link.click)
    focused = """() => {
        const target = new URL(location.href).searchParams.get('trajectoryNode');
        const node = [...document.querySelectorAll('.react-flow__node.selected')].find(n => n.dataset.id === target);
        const canvas = document.querySelector('[aria-label="Capability trajectory map"]');
        if (!node || !canvas) return false;
        const n = node.getBoundingClientRect(), c = canvas.getBoundingClientRect();
        return n.width >= 180 && n.left >= c.left && n.right <= c.right &&
            n.top >= Math.max(c.top, 64) && n.bottom <= Math.min(c.bottom, innerHeight);
    }"""
    console.wait_until(focused)
    console._browser.call(console._page.locator(f'a[href="{href}"]').first.click)
    console.wait_until(focused)
    stable = console._browser.call(
        console._page.evaluate,
        """async () => {
        const positions = [];
        for (let i = 0; i < 60; i++) {
            await new Promise(requestAnimationFrame);
            const r = document.querySelector('.react-flow__node.selected').getBoundingClientRect();
            positions.push([r.x, r.y, r.width]);
        }
        const last = positions.slice(-10);
        return last.every(p => p.every((v, i) => Math.abs(v - last[0][i]) < 1));
    }""",
    )
    assert stable
    console.save_evidence("evaluation-scope-focus")
    console.healthy()


def test_dedicated_startup_updates_and_keeps_configuration_after_stop(console, workshop):
    project = workshop.project()["id"]
    deployed = DeployedModel.objects.create(
        project_id=project,
        model_id="ft-dedicated-startup",
        status="ready",
        base_model_id="meta-llama/Llama-3.2-3B-Instruct",
        gpu_type="H100",
    )
    row = DedicatedDeployment.objects.create(
        project_id=project,
        deployed_model=deployed,
        gpu_type="H100",
        status="warming",
        min_replicas=2,
        max_replicas=3,
        rate_usd_per_gpu_hour=4,
        credits_per_gpu_hour=400,
        probe_call_id="fc-console-startup",
    )
    run = OperationalRun.objects.create(
        project_id=project,
        kind="dedicated_deployment",
        reference=str(row.pk),
        attempt=row.probe_call_id,
        snapshot={"stage": "loading_weights"},
        provider_state={
            "available": True,
            "current": {"message": "Loading model weights"},
            "logs": [
                {
                    "stage": "loading_weights",
                    "message": "Loading model weights",
                    "at": timezone.now().isoformat(),
                    "worker": "ta-console",
                    "completed": 1,
                    "total": 2,
                    "unit": "tensors",
                }
            ],
        },
    )
    console.goto(f"/inference/{deployed.pk}?projectId={project}")
    console.sees("Loading model weights")
    console.wait_until("() => !document.body.innerText.includes('Startup logs')")
    console.wait_until("""() => {
        const bar = document.querySelector('[role=progressbar][aria-label="Startup stages"]');
        const title = document.querySelector('[aria-label="Dedicated endpoint"] h2');
        const controls = document.querySelector('[aria-label="GPU controls"]');
        if (!bar || bar.children.length < 2) return false;
        const b = bar.getBoundingClientRect(), t = title.getBoundingClientRect(), c = controls.getBoundingClientRect();
        return b.left > t.right && b.right < c.left && Math.abs(b.y - t.y) < 12;
    }""")
    console.save_evidence("dedicated-startup-header")
    run.snapshot = {"stage": "capturing_graphs"}
    run.provider_state["current"] = {"message": "Capturing CUDA graphs"}
    run.save()
    console.sees("Capturing CUDA graphs")
    DedicatedDeployment.objects.filter(pk=row.pk).update(status="online", running_replicas=2)
    console.sees("Online")
    console.wait_until(
        """() => !document.querySelector('[role=progressbar][aria-label="Startup stages"]') && !document.body.innerText.includes('Startup logs')"""
    )
    console.save_evidence("dedicated-online")
    DedicatedDeployment.objects.filter(pk=row.pk).update(status="offline", running_replicas=0)
    console.sees("Offline")
    console.sees("0 running · 2–3 configured")
    console.press("Dedicated API snippet")
    console.sees("Call this model")
    console.wait_until(
        "() => document.querySelector('[role=dialog]').innerText.includes('dedicated/ft-dedicated-startup')"
    )
    console.save_evidence("dedicated-offline-snippet")
    console.press("Close")
    console.press("Configure GPU")
    console.sees("Dedicated GPU")
    console.wait_until("""() => {
        const dialog = document.querySelector('[role=dialog]');
        return dialog && dialog.querySelector('#dedicated-min-replicas')?.value === '2';
    }""")
    console.save_evidence("dedicated-configuration-dialog")
    console.healthy()
