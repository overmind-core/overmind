import hashlib
import json
import math
import time
from urllib.parse import urlencode

import pytest
from factories import make_project

from overbae.models import Dataset, NativeEvaluationPlan
from overbae.services import native_evaluation
from overbae.services.datasets import land
from overbae.services.decision_benchmark_scoring import records

from ..conftest import RUNS
from ..stack import drain


@pytest.mark.parametrize("participant_count", [1, 3, 6])
def test_native_benchmark_progress_samples_and_completed_backfill(
    console, account, rest_for, mcp_for, fake_modal, worker, settings, tmp_path, participant_count
):
    settings.MEDIA_ROOT = tmp_path
    user, key = account
    project = make_project("Titanic comparison", member=user)
    dataset = Dataset.objects.create(
        project=project, name="Frozen passenger final suite", intent="eval"
    )
    data = [
        {
            "input": {
                "decision": {
                    "state": f"Passenger {index}; age 32; class 2",
                    "question": "Will this passenger survive?",
                    "kind": "choice",
                    "options": ["Survived", "Did not survive"],
                }
            },
            "expected_output": {
                "probabilities": [0.7, 0.3] if index == 1 else ([0, 1] if index % 2 else [1, 0])
            },
            "benchmark": "Survival",
            "group": str(index),
        }
        for index in range(27)
    ]
    data.append(
        {
            "input": {
                "decision": {
                    "state": "Passenger 27",
                    "question": "Estimate the score",
                    "kind": "score",
                    "options": ["Low", "High"],
                }
            },
            "expected_output": {"mean": 7, "values": [0, 10]},
            "benchmark": "Ordinal score",
        }
    )
    land.land_rows(dataset, data)
    dataset.refresh_from_db()
    cell = dataset.active_cell
    arms = [
        {
            "key": arm,
            "name": name,
            "kind": "foundation",
            "model": model,
        }
        for arm, name, model in (
            ("small", "Qwen 3.5 0.8B", "Qwen/Qwen3.5-0.8B"),
            ("medium", "Qwen 3.5 2B", "Qwen/Qwen3.5-2B"),
            ("large", "Qwen 3.5 4B", "Qwen/Qwen3.5-4B"),
            ("input", "Titanic survival decision model foundation checkpoint", "Qwen/Qwen3-0.6B"),
            ("qwen3", "Qwen 3 1.7B foundation", "Qwen/Qwen3-1.7B"),
            ("llama", "Llama 3.2 1B Instruct", "meta-llama/Llama-3.2-1B-Instruct"),
        )[:participant_count]
    ]
    plan = NativeEvaluationPlan.objects.create(
        project=project,
        final_cell=cell,
        name="Titanic live benchmark",
        state="queued",
        config={
            "participants": arms,
            "baseline": "small",
            "suites": {
                "final": {
                    "cell": str(cell.pk),
                    "dataset": str(dataset.pk),
                    "fingerprint": cell.fingerprint,
                    "rows": cell.rows,
                }
            },
            "runtime": native_evaluation.runtime(),
            "seed": 123,
            "bootstrap_samples": 2,
            "logarithm_floor": 1e-12,
            "contamination_limits": "Fixture inputs only",
            "inference": {},
        },
    )
    native_evaluation.advance(plan.pk, stage="verify_inputs")
    plan.refresh_from_db()
    source = native_evaluation.directory(plan, "final")
    inputs = list(records(source / "inputs.jsonl"))
    assert len(inputs) == 28
    input_sha = hashlib.sha256((source / "inputs.jsonl").read_bytes()).hexdigest()
    calls = {}
    roots = {}
    manifests = {}
    payloads = {}
    for arm in arms:
        name = arm["key"]
        stage = f"final_{name}"
        _, _, _, group = native_evaluation.provider_stage(plan, stage)
        call = fake_modal.adopt(f"fc-monitor-{name}", "predict")
        calls[name] = call
        roots[name] = f"/decision-evaluations/{plan.pk}-final" + (
            "" if group == "prepare" else f"-{group}"
        )
        plan.calls[f"final_{group}"] = {
            "state": "completed",
            "receipt": {
                "artifact_identity": name,
                "input_sha256": input_sha,
                "ready_decisions": len(inputs),
            },
        }
        plan.calls[stage] = {"state": "running", "id": call.object_id}
        payloads[name] = [
            {
                "key": row["key"],
                "input_sha256": row["input_sha256"],
                "kind": row["decision"]["kind"],
                "model_identity": name,
                "probabilities": [0.8, 0.2],
                "log_probabilities": [math.log(0.8), math.log(0.2)],
            }
            for row in inputs
        ]
        for index in range(2, 27):
            probabilities = (
                [0.5, 0.5]
                if name == "small" and index == 2
                else [0.1, 0.9]
                if name == "medium"
                else [0.501, 0.499]
                if name == "large"
                else [0.8, 0.2]
            )
            payloads[name][index].update(
                probabilities=probabilities,
                log_probabilities=[math.log(value) for value in probabilities],
            )
        manifests[name] = {
            "call_id": call.object_id,
            "artifact_identity": name,
            "input_sha256": input_sha,
            "runtime_fingerprint": plan.config["runtime"]["training"],
            "expected": len(inputs),
            "completed": 0,
            "stage": "loading_model",
            "source_at": time.time(),
            "last_progress_at": time.time(),
            "chunks": [],
        }
        fake_modal.files[f"{roots[name]}/progress/{call.object_id}/manifest.json"] = json.dumps(
            manifests[name]
        ).encode()
    plan.save()
    for arm in arms:
        native_evaluation.advance(plan.pk, stage=f"final_{arm['key']}")

    rest = rest_for(key)
    mcp = mcp_for(key)
    endpoint = f"/api/native-evaluations/{plan.pk}/monitor/"
    initial = rest.request("GET", endpoint)
    assert initial["count"] == 28
    assert all(arm["scored"] == 0 for arm in initial["arms"])
    assert initial["samples"][0]["key"] == "0"
    console.goto(f"/evaluations/runs/{plan.pk}?projectId={project.pk}&format=decision")
    console.sees("Loading model")
    console.sees("0 / 28")

    def publish(arm, rows, *, corrupt=False):
        manifest = manifests[arm]
        index = len(manifest["chunks"])
        body = "".join(json.dumps(row) + "\n" for row in rows).encode()
        manifest["chunks"].append({"sha256": hashlib.sha256(body).hexdigest(), "rows": len(rows)})
        manifest.update(
            completed=manifest["completed"] + len(rows),
            stage="predicting",
            source_at=time.time(),
            last_progress_at=time.time(),
        )
        root = f"{roots[arm]}/progress/{calls[arm].object_id}"
        fake_modal.files[f"{root}/{index}.jsonl"] = b"corrupt" if corrupt else body
        fake_modal.files[root + "/manifest.json"] = json.dumps(manifest).encode()
        native_evaluation.advance(plan.pk, stage=f"final_{arm}")
        return root, body

    publish("small", payloads["small"][:2])
    partial = rest.request("GET", endpoint)
    assert partial["arms"][0]["scored"] == 2, [
        (s["key"], s["error"], s["predictions"].get("small", {}).get("error"))
        for s in partial["samples"][:2]
    ]
    assert partial["arms"][0]["benchmarks"][1]["raw"]["accuracy"]["decisions"] == 1
    assert "medium" not in partial["samples"][0]["predictions"]
    assert rest.request("GET", endpoint + "?outcome=missing&participant=small")["count"] == 26
    assert rest.request("GET", endpoint + "?outcome=missing")["count"] == (
        28 if participant_count > 1 else 26
    )
    native_evaluation.advance(plan.pk, stage="final_small")
    assert rest.request("GET", endpoint)["arms"][0]["scored"] == 2
    observed = mcp.call(
        "inspect_native_evaluation",
        {"project_id": str(project.pk), "evaluation": str(plan.pk), "key": "0", "limit": 1},
    )["monitoring"]
    assert observed["provisional"] and observed["samples"][0]["input"]["options"] == [
        "Survived",
        "Did not survive",
    ]
    assert observed["samples"][0]["predictions"]["small"]["raw"]["probabilities"] == [0.8, 0.2]
    console.sees("2 / 28")
    console._browser.call(console._page.set_viewport_size, {"width": 1440, "height": 1100})
    console.wait_until("""() => {
        const table = document.querySelector('[aria-label="Benchmark datapoints"] [role=grid]');
        const rows = [...table.querySelectorAll('tbody tr')];
        const heights = rows.map(row => row.getBoundingClientRect().height);
        return rows.length === 25 && rows.filter(row => row.tabIndex === 0).length === 1
            && Math.max(...heights) <= 40
            && Math.max(...heights) - Math.min(...heights) <= 1;
    }""")
    console.wait_until("""() => {
        const rows = document.querySelectorAll('[aria-label="Benchmark datapoints"] tbody tr');
        return rows[0].querySelector('[aria-label="Correct: Survived. Probability 80.0%"] [data-slot=badge]')
            ?.classList.contains('text-success')
            && rows[1].querySelector('[aria-label="Reference: Soft target"]')
            && rows[1].querySelector('[aria-label="Prediction: Survived. Probability 80.0%"] [data-slot=badge]')
                ?.classList.contains('text-muted-foreground')
            && !rows[1].querySelector('.text-success, .text-destructive');
    }""")
    console._browser.call(
        lambda: console._page.get_by_role(
            "region", name="Benchmark datapoints"
        ).scroll_into_view_if_needed()
    )
    console.save_evidence(f"native-datapoints-{participant_count}-desktop")
    console._browser.call(
        lambda: console._page.get_by_role("region", name="Benchmark datapoints").screenshot(
            path=str(RUNS / f"native-datapoints-{participant_count}-table-desktop.png")
        )
    )
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Benchmark datapoints")
            .get_by_role("grid")
            .locator("tbody tr")
            .first.press("ArrowDown")
        )
    )
    console.wait_until("""() => document.activeElement?.getAttribute('data-row-index') === '1'""")
    console._browser.call(lambda: console._page.keyboard.press("Enter"))
    console.sees("Datapoint 1")
    console.sees("70.0%")
    console.wait_until("""() => document.querySelector('[aria-label="Benchmark datapoints"] tr[aria-current="true"]')
        ?.getAttribute('data-row-index') === '1'""")
    console._browser.call(lambda: console._page.keyboard.press("Escape"))
    console.wait_until("""() => document.activeElement?.getAttribute('data-row-index') === '1'""")
    console.save_evidence(f"native-benchmark-{participant_count}-running-desktop")
    console._browser.call(console._page.set_viewport_size, {"width": 390, "height": 844})
    console.wait_until("() => document.documentElement.scrollWidth <= innerWidth")
    console.save_evidence(f"native-benchmark-{participant_count}-running-mobile")
    console._browser.call(
        lambda: console._page.get_by_role(
            "region", name="Benchmark datapoints"
        ).scroll_into_view_if_needed()
    )
    console.save_evidence(f"native-datapoints-{participant_count}-mobile")
    console._browser.call(
        lambda: console._page.get_by_role("region", name="Benchmark datapoints").screenshot(
            path=str(RUNS / f"native-datapoints-{participant_count}-table-mobile.png")
        )
    )
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Benchmark datapoints")
            .locator('[data-slot="table-container"]')
            .evaluate("(el) => { el.scrollLeft = el.scrollWidth; }")
        )
    )
    console.wait_until("""() => {
        const scroll = document.querySelector('[aria-label="Benchmark datapoints"] [data-slot="table-container"]');
        const last = scroll.querySelector('thead th:nth-last-child(2)').getBoundingClientRect();
        return last.right <= scroll.getBoundingClientRect().right + 1;
    }""")
    console._browser.call(console._page.set_viewport_size, {"width": 1440, "height": 1100})
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Benchmark datapoints")
            .get_by_role("grid")
            .locator("tbody tr")
            .first.press("Enter")
        )
    )
    console.sees("Datapoint 0")
    console.sees("80.0%")
    console.wait_until(
        """() => !document.querySelector('[role=dialog] [role=combobox], [role=dialog] [role=tablist]')"""
    )
    console.wait_until(
        f"""() => document.querySelectorAll('[aria-label="Model outputs"] > section').length === {participant_count}"""
    )
    console.wait_until("""() => [...document.querySelectorAll('[aria-label="Output for Qwen 3.5 0.8B"] tbody tr')]
        .map(row => [...row.cells].map(cell => cell.querySelector('div > span')?.textContent ?? cell.textContent).join('|')).join(';')
        === 'Survived|80.0%|100.0%;Did not survive|20.0%|0.0%'""")
    console.wait_until("""() => {
        const row = document.querySelector('[aria-label="Output for Qwen 3.5 0.8B"] tbody tr');
        return row.textContent.includes('Target') && row.textContent.includes('Predicted');
    }""")
    if participant_count > 1:
        console.sees("Waiting for this model's prediction.")
        console.wait_until("""() => [...document.querySelectorAll('[aria-label="Output for Qwen 3.5 2B"] tbody tr')]
            .map(row => [...row.cells].map(cell => cell.querySelector('div > span')?.textContent ?? cell.textContent).join('|')).join(';')
            === 'Survived|—|100.0%;Did not survive|—|0.0%'""")
        console.wait_until("""() => performance.getEntriesByType('resource')
            .filter(entry => entry.name.includes('/monitor/') && entry.name.includes('key=0')).length >= 2""")
    console.wait_until("""() => document.querySelector('[role=dialog]').getAnimations({subtree: true})
        .every(animation => animation.playState === 'finished')""")
    console.save_evidence(f"native-benchmark-{participant_count}-sample-desktop")
    if participant_count > 1:
        root, body = publish("medium", payloads["medium"][:1], corrupt=True)
        broken = rest.request("GET", endpoint)
        assert broken["calls"][1]["progress_error"]
        assert broken["arms"][1]["scored"] == 0
        fake_modal.files[root + "/0.jsonl"] = body
        native_evaluation.advance(plan.pk, stage="final_medium")
        assert rest.request("GET", endpoint)["arms"][1]["scored"] == 1
        console.wait_until("""() => document.querySelector('[aria-label="Output for Qwen 3.5 2B"] tbody tr td:nth-child(2)')
            ?.textContent === '80.0%'""")
        console.wait_until("""() => document.querySelector('[aria-label="Output for Qwen 3.5 4B"]').textContent
            .includes("Waiting for this model's prediction.")""")
    if participant_count == 6:
        input_top = console._browser.call(
            lambda: console._page.get_by_role("region", name="Evaluation input").bounding_box()["y"]
        )
        console._browser.call(
            lambda: console._page.get_by_role("region", name="Model outputs", exact=True).press(
                "End"
            )
        )
        console.wait_until("""() => {
            const outputs = document.querySelector('[aria-label="Model outputs"]');
            return outputs.scrollTop > 0 && outputs.lastElementChild.getBoundingClientRect().bottom
                <= outputs.getBoundingClientRect().bottom + 1;
        }""")
        console.wait_until(f"""() => Math.abs(document.querySelector('[aria-label="Evaluation input"]')
            .getBoundingClientRect().top - {input_top}) < 1""")
        console.save_evidence("native-benchmark-6-sample-desktop-last-model")
        console._browser.call(
            lambda: console._page.get_by_role("region", name="Model outputs", exact=True).press(
                "Home"
            )
        )
        console.wait_until(
            """() => document.querySelector('[aria-label="Model outputs"]').scrollTop === 0"""
        )
    console._browser.call(console._page.set_viewport_size, {"width": 390, "height": 844})
    console.wait_until("""() => {
        const rect = document.querySelector('[role=dialog]').getBoundingClientRect();
        return rect.left >= 0 && rect.right <= innerWidth && rect.width <= innerWidth * .76;
    }""")
    console.wait_until("""() => {
        const input = document.querySelector('[aria-label="Evaluation input"]').getBoundingClientRect();
        const output = document.querySelector('[aria-label="Output and evaluation"]').getBoundingClientRect();
        return output.top >= input.bottom;
    }""")
    console.wait_until("""() => {
        const scroll = document.querySelector('[aria-label="Option probabilities"]').parentElement;
        return scroll.scrollWidth <= scroll.clientWidth;
    }""")
    console.save_evidence(f"native-benchmark-{participant_count}-sample-mobile")
    console._browser.call(
        lambda: console._page.get_by_label(
            "Option probabilities"
        ).first.scroll_into_view_if_needed()
    )
    console.save_evidence(f"native-benchmark-{participant_count}-sample-mobile-output")
    if participant_count > 1:
        console._browser.call(
            lambda: console._page.get_by_role(
                "region", name=f"Output for {arms[-1]['name']}", exact=True
            ).scroll_into_view_if_needed()
        )
        console.wait_until("""() => {
            const dialog = document.querySelector('[role=dialog]').getBoundingClientRect();
            const last = document.querySelector('[aria-label="Model outputs"]').lastElementChild.getBoundingClientRect();
            return last.top >= dialog.top && last.bottom <= dialog.bottom;
        }""")
        console.save_evidence(f"native-benchmark-{participant_count}-sample-mobile-last-model")
    console._browser.call(lambda: console._page.keyboard.press("Escape"))

    publish("small", payloads["small"][2:])
    with pytest.raises(AssertionError, match="400"):
        rest.request("GET", endpoint + "?role=calibration")
    foreign = make_project("Other project")
    with pytest.raises(AssertionError, match="resource_not_found"):
        mcp.call(
            "inspect_native_evaluation",
            {"project_id": str(project.pk), "evaluation": str(foreign.pk)},
        )

    for arm in arms:
        name = arm["key"]
        body = "".join(json.dumps(row) + "\n" for row in payloads[name]).encode()
        fake_modal.files[roots[name] + "/base.jsonl"] = body
        calls[name].result = {
            "artifact_identity": name,
            "input_sha256": input_sha,
            "runtime_fingerprint": plan.config["runtime"]["training"],
            "predictions_sha256": hashlib.sha256(body).hexdigest(),
            "decisions": len(inputs),
        }
        calls[name].state = "done"
        native_evaluation.advance(plan.pk, stage=f"final_{name}")
    native_evaluation.advance(plan.pk, stage="score")
    plan.refresh_from_db()
    assert plan.state == "completed", plan.error
    finished = rest.request("GET", endpoint + "?key=27")
    assert not finished["provisional"]
    assert all(arm["scored"] == 28 for arm in finished["arms"])
    ordinal = finished["samples"][0]
    assert ordinal["reference"] == {"mean": 7, "values": [0, 10]}
    assert "cross_entropy" not in ordinal["predictions"]["small"]["metrics"]
    assert ordinal["predictions"]["small"]["metrics"]["expected_score"] == 2
    assert [
        row["key"] for row in rest.request("GET", endpoint + "?offset=25&limit=10")["samples"]
    ] == ["25", "26", "27"]
    before = list(fake_modal.spawns())
    (source / "monitor.sqlite").unlink()
    (source / "monitor.json").unlink()
    assert rest.request("GET", endpoint)["state"] == "pending"
    drain(worker)
    assert rest.request("GET", endpoint)["arms"][0]["scored"] == 28
    assert fake_modal.spawns() == before
    for query in ("?participant=unknown", "?outcome=unknown", "?key=0&search=Passenger"):
        with pytest.raises(AssertionError, match="400"):
            rest.request("GET", endpoint + query)
    for search in ("%", "_", "' OR 1=1 --"):
        assert rest.request("GET", endpoint + "?" + urlencode({"search": search}))["count"] == 0
    searched = rest.request(
        "GET", endpoint + "?" + urlencode({"search": "PASSENGER 2", "limit": 2, "offset": 2})
    )
    assert searched["count"] == 9
    assert [row["key"] for row in searched["samples"]] == ["21", "22"]
    assert rest.request("GET", endpoint + "?search=survival")["count"] == 27
    incorrect = rest.request("GET", endpoint + "?outcome=incorrect&participant=small")
    assert incorrect["count"] == 12
    assert all(
        row["predictions"]["small"]["metrics"]["accuracy"] == 0 for row in incorrect["samples"]
    )
    assert incorrect["arms"] == rest.request("GET", endpoint)["arms"]
    assert rest.request("GET", endpoint + "?outcome=correct&participant=small")["count"] == 14
    assert rest.request("GET", endpoint + "?outcome=incorrect")["count"] == (
        25 if participant_count > 1 else 12
    )
    assert rest.request("GET", endpoint + "?outcome=missing")["count"] == 0
    assert rest.request("GET", endpoint + "?outcome=unscored")["count"] == 0
    filtered = mcp.call(
        "inspect_native_evaluation",
        {
            "project_id": str(project.pk),
            "evaluation": str(plan.pk),
            "search": "Passenger 2",
            "outcome": "incorrect",
            "participant": "small",
            "limit": 2,
        },
    )["monitoring"]
    assert filtered["count"] == 3
    assert [row["key"] for row in filtered["samples"]] == ["21", "23"]
    assert fake_modal.spawns() == before
    console.goto(f"/evaluations/runs/{plan.pk}?projectId={project.pk}&format=decision")
    console.sees("Model comparison")
    console._browser.call(console._page.set_viewport_size, {"width": 1440, "height": 1100})
    console._browser.call(
        lambda: console._page.get_by_role("region", name="Model comparison").screenshot(
            path=str(RUNS / f"native-model-comparison-{participant_count}-desktop.png")
        )
    )
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Model comparison")
            .get_by_role("button", name="Sort by Model")
            .click()
        )
    )
    console.wait_until(
        """() => document.querySelector('[aria-label="Model comparison"] th').getAttribute('aria-sort') === 'ascending'"""
    )
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Model comparison")
            .get_by_role("button", name="Sort by Model")
            .click()
        )
    )
    console.wait_until(
        """() => document.querySelector('[aria-label="Model comparison"] th').getAttribute('aria-sort') === 'descending'"""
    )
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Model comparison")
            .get_by_role("button", name="Sort by Model")
            .click()
        )
    )
    console.wait_until(
        """() => document.querySelector('[aria-label="Model comparison"] th').getAttribute('aria-sort') === 'none'"""
    )
    console._browser.call(console._page.set_viewport_size, {"width": 390, "height": 844})
    console.wait_until("() => document.documentElement.scrollWidth <= innerWidth")
    console._browser.call(
        lambda: console._page.get_by_role("region", name="Model comparison").screenshot(
            path=str(RUNS / f"native-model-comparison-{participant_count}-mobile.png")
        )
    )
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Model comparison")
            .locator('[data-slot="table-container"]')
            .evaluate("(el) => { el.scrollLeft = el.scrollWidth; }")
        )
    )
    console.wait_until("""() => {
        const scroll = document.querySelector('[aria-label="Model comparison"] [data-slot="table-container"]');
        return scroll.querySelector('thead th:nth-last-child(2)').getBoundingClientRect().right <= scroll.getBoundingClientRect().right + 1;
    }""")
    console._browser.call(
        lambda: console._page.get_by_role("region", name="Model comparison").screenshot(
            path=str(RUNS / f"native-model-comparison-{participant_count}-mobile-metrics.png")
        )
    )
    console._browser.call(console._page.set_viewport_size, {"width": 1440, "height": 1100})
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Benchmark datapoints")
            .get_by_role("grid")
            .wait_for()
        )
    )
    console.wait_until("""() => {
        const rows = document.querySelectorAll('[aria-label="Benchmark datapoints"] tbody tr');
        return rows[2].querySelector('[aria-label="Correct: Survived. Probability 50.0%. Tied maximum"]')
            && rows[3].querySelector('[aria-label="Reference: Did not survive"]')
            && rows[3].querySelector('[aria-label="Incorrect: Survived. Probability 80.0%"] [data-slot=badge]')
                ?.classList.contains('text-destructive');
    }""")
    if participant_count > 1:
        console.wait_until("""() => {
            const row = document.querySelectorAll('[aria-label="Benchmark datapoints"] tbody tr')[2];
            return row.querySelector('[aria-label="Incorrect: Did not survive. Probability 90.0%"] [data-slot=badge]')
                ?.classList.contains('text-destructive')
                && row.querySelector('[aria-label="Correct: Survived. Probability 50.1%"] [data-slot=badge]')
                    ?.classList.contains('text-success');
        }""")
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Benchmark datapoints")
            .get_by_label("Reference: Soft target", exact=True)
            .hover()
        )
    )
    console.sees("70.0%")
    console.sees("30.0%")
    console._browser.call(lambda: console._page.mouse.move(0, 0))
    console._browser.call(
        lambda: console._page.get_by_role("region", name="Benchmark datapoints").screenshot(
            path=str(RUNS / f"native-datapoints-{participant_count}-completed-desktop.png")
        )
    )
    console._browser.call(console._page.set_viewport_size, {"width": 390, "height": 844})
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Benchmark datapoints")
            .get_by_role("group", name="Correct: Survived. Probability 80.0%", exact=True)
            .first.scroll_into_view_if_needed()
        )
    )
    console.wait_until("() => document.documentElement.scrollWidth <= innerWidth")
    console._browser.call(
        lambda: console._page.get_by_role("region", name="Benchmark datapoints").screenshot(
            path=str(
                RUNS / f"native-datapoints-{participant_count}-completed-mobile-predictions.png"
            )
        )
    )
    console._browser.call(console._page.set_viewport_size, {"width": 1440, "height": 1100})

    def resize_input():
        header = console._page.get_by_role("region", name="Benchmark datapoints").get_by_role(
            "columnheader", name="Input", exact=True
        )
        handle = header.locator('[class*="cursor-col-resize"]')
        handle.scroll_into_view_if_needed()
        rect = handle.bounding_box()
        console._page.mouse.move(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
        console._page.mouse.down()
        console._page.mouse.move(rect["x"] + rect["width"] / 2 + 40, rect["y"] + rect["height"] / 2)
        console._page.mouse.up()

    console._browser.call(resize_input)
    console.wait_until(
        f"""() => JSON.parse(localStorage.getItem('native-datapoints:{plan.pk}:columns')).input > 320"""
    )
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Benchmark datapoints")
            .get_by_role("button", name="Go to next page")
            .click()
        )
    )
    console.sees("26–28 of 28 rows")
    console.wait_until("""() => {
        const rows = document.querySelectorAll('[aria-label="Benchmark datapoints"] tbody tr');
        const ordinal = rows[rows.length - 1];
        return ordinal.querySelector('[aria-label="Reference: Mean 7"]')
            && !ordinal.querySelector('.text-success, .text-destructive');
    }""")
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Benchmark datapoints")
            .get_by_role("grid")
            .locator("tbody tr")
            .last.click()
        )
    )
    console.sees("Datapoint 27")
    console.sees("Reference mean", exact=False)
    console.wait_until(f"""() => [...document.querySelectorAll('[aria-label="Model outputs"] > section')]
        .filter(section => section.textContent.includes('Reference mean 7') && section.textContent.includes('Predicted mean 2.0000'))
        .length === {participant_count}""")
    console.wait_until("""() => document.querySelector('[role=dialog]').getAnimations({subtree: true})
        .every(animation => animation.playState === 'finished')""")
    console.save_evidence(f"native-benchmark-{participant_count}-sample-completed-desktop")
    console._browser.call(lambda: console._page.keyboard.press("Escape"))
    console._browser.call(
        lambda: console._page.get_by_role("searchbox", name="Search datapoints").fill(
            "Passenger 27"
        )
    )
    console.sees("1–1 of 1 row")
    console._browser.call(
        lambda: (
            console._page.get_by_role("region", name="Benchmark datapoints")
            .get_by_role("grid")
            .locator("tbody tr")
            .first.press("Enter")
        )
    )
    console.sees("Datapoint 27")
    console._browser.call(lambda: console._page.keyboard.press("Escape"))
    console.press("Clear search")
    console.sees("1–25 of 28 rows")
    console._browser.call(
        lambda: console._page.get_by_role("combobox", name="Filter by model").click()
    )
    console._browser.call(
        lambda: console._page.get_by_role("option", name="Qwen 3.5 0.8B", exact=True).click()
    )
    console._browser.call(
        lambda: console._page.get_by_role("combobox", name="Filter by outcome").click()
    )
    console._browser.call(
        lambda: console._page.get_by_role("option", name="Incorrect", exact=True).click()
    )
    console.sees("1–12 of 12 rows")
    console.wait_until("""() => {
        const table = document.querySelector('[aria-label="Benchmark datapoints"] [role=grid]');
        return table.querySelectorAll('tbody tr').length === 12
            && [...table.querySelectorAll('tbody tr')].every(row => row.querySelector('[aria-label^="Incorrect:"]'))
            && [...table.querySelectorAll('thead th')].filter(th => th.textContent.includes('Qwen')).length === 1;
    }""")
    console.save_evidence(f"native-datapoint-filters-{participant_count}-desktop")
    console._browser.call(console._page.set_viewport_size, {"width": 390, "height": 844})
    console._browser.call(
        lambda: console._page.get_by_role(
            "searchbox", name="Search datapoints"
        ).scroll_into_view_if_needed()
    )
    console.wait_until("() => document.documentElement.scrollWidth <= innerWidth")
    console.save_evidence(f"native-datapoint-filters-{participant_count}-mobile")
    console._browser.call(
        lambda: console._page.get_by_role("searchbox", name="Search datapoints").fill("Passenger 2")
    )
    console.sees("1–3 of 3 rows")
    console._browser.call(
        lambda: console._page.get_by_role("searchbox", name="Search datapoints").fill(
            "no matching passenger"
        )
    )
    console.sees("No results")
    console.press("Clear filters")
    console.sees("1–25 of 28 rows")
    console._browser.call(
        lambda: console._page.get_by_role("combobox", name="Filter by outcome").click()
    )
    console._browser.call(
        lambda: console._page.get_by_role("option", name="Missing output", exact=True).click()
    )
    console.sees("No results")
    console.press("Clear filters")
    console.sees("1–25 of 28 rows")
    console.wait_until(
        "() => ![...document.querySelectorAll('button')].some(el => /Pause new work|Start evaluation|Delete/.test(el.textContent))"
    )
    console.healthy()
