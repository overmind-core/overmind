import uuid

from factories import make_project

from overbae.models import Cell, Dataset, FinetuningJob
from overbae.services.datasets import land, paths, store, use

from ..conftest import RUNS


def test_training_labels_follow_the_pinned_cell_across_console_rest_and_mcp(
    console, account, rest_for, mcp_for, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    user, key = account
    project = make_project("Prediction labels", member=user)
    dataset = Dataset.objects.create(project=project, name="Titanic training", intent="train")
    decision = {
        "state": "Passenger",
        "question": "Did this Titanic passenger survive?",
        "kind": "choice",
        "options": ["didn't survive", "survived"],
        "target_probabilities": [1, 0],
    }
    land.land_rows(dataset, [{"decision": decision}] * 625)
    dataset.refresh_from_db()
    cell = dataset.active_cell
    use.freeze(cell)
    group = uuid.uuid4()
    jobs = [
        FinetuningJob.objects.create(
            project=project,
            dataset=dataset,
            cell=cell,
            group_id=group,
            base_model=f"Qwen/Qwen3.5-{size}",
            name=f"Titanic {size}",
            status="succeeded",
            hyperparameters={"objective": "decision_cross_entropy"},
        )
        for size in ("0.8B", "2B", "4B")
    ]
    newer = Cell.objects.create(
        dataset=dataset, position=1, title="Later labels", state="ok", rows=1
    )
    path = paths.cell_path(dataset.pk, newer.pk)
    store.write_rows(path, [{"decision": {**decision, "options": ["New no", "New yes"]}}])
    Cell.objects.filter(pk=newer.pk).update(fingerprint=store.file_sha256(path))
    Dataset.objects.filter(pk=dataset.pk).update(active_id=newer.pk)
    dataset.refresh_from_db()
    assert dataset.active_cell.pk != cell.pk
    rest, mcp = rest_for(key), mcp_for(key)
    endpoint = f"/api/finetuning-jobs/{jobs[0].pk}/"
    summary = rest.request("GET", endpoint)["record"]["prediction_labels"]
    assert summary == {
        "state": "available",
        "cell": str(cell.pk),
        "fingerprint": cell.fingerprint,
        "total_sets": 1,
        "label_sets": [
            {
                "question": decision["question"],
                "kind": "choice",
                "options": decision["options"],
                "option_values": None,
            }
        ],
    }
    result = mcp.call(
        "get_job",
        {"project_id": str(project.pk), "kind": "finetune_job", "id": str(jobs[0].pk)},
    )
    resource = mcp.read(result["resource_links"][0]["uri"])
    assert resource["record"]["prediction_labels"] == summary
    assert rest.request("GET", endpoint)["record"]["prediction_labels"] == summary

    console._browser.call(console._page.set_viewport_size, {"width": 1440, "height": 1100})
    route = f"/training?projectId={project.pk}&groupId={group}"
    console.goto(route)
    console.sees("Prediction labels")

    def check_labels():
        region = console._page.get_by_role("group", name="Prediction labels", exact=True)
        region.wait_for()
        assert region.count() == 1
        popup = console._page.get_by_role("dialog", name="Prediction labels", exact=True)
        if not popup.is_visible():
            region.get_by_role("button", name="2 labels", exact=True).click()
        popup.wait_for()
        assert popup.get_by_role("listitem").all_text_contents() == decision["options"]
        assert "New yes" not in popup.inner_text()
        assert region.get_by_role("button", name="2 labels", exact=True).count() == 1
        assert region.locator("dt").inner_text() == "Prediction labels"
        assert region.evaluate("el => el.parentElement.tagName") == "DL"
        assert region.locator("h3").count() == 0
        labels = popup.get_by_role("listitem")
        for item in labels.all():
            assert item.evaluate("el => el.scrollWidth <= el.clientWidth")

    console._browser.call(check_labels)

    console.save_evidence("training-prediction-labels-desktop")
    console._browser.call(console._page.set_viewport_size, {"width": 390, "height": 844})
    console._browser.call(check_labels)
    console.save_evidence("training-prediction-labels-mobile")
    console.goto(f"{route}&jobId={jobs[1].pk}")
    console._browser.call(check_labels)

    FinetuningJob.objects.filter(pk=jobs[0].pk).update(group_id=None)
    console.goto(f"/training?projectId={project.pk}&groupId={jobs[0].pk}&jobId={jobs[0].pk}")
    console._browser.call(check_labels)
    console.save_evidence("training-prediction-labels-single-mobile")
    FinetuningJob.objects.filter(pk=jobs[0].pk).update(group_id=group)

    varied = Dataset.objects.create(project=project, name="Ordinal labels", intent="train")
    long_label = "A" * 150
    land.land_rows(
        varied,
        [
            {
                "decision": {
                    **decision,
                    "question": f"Score question {index}",
                    "kind": "score",
                    "options": [long_label, "High"],
                    "target_probabilities": [0.3, 0.7],
                }
            }
            for index in range(9)
        ]
        + [
            {
                "decision": {
                    "state": "Ordinal example",
                    "question": "Mean score",
                    "kind": "score",
                    "options": ["Low", "High"],
                    "target_mean": 7,
                    "option_values": [0, 10],
                    "target_semantics": "ordinal_mean",
                }
            }
        ],
    )
    varied.refresh_from_db()
    FinetuningJob.objects.filter(pk=jobs[1].pk).update(cell=varied.active_cell, dataset=varied)
    ordinal = rest.request("GET", f"/api/finetuning-jobs/{jobs[1].pk}/")["record"][
        "prediction_labels"
    ]
    assert ordinal["total_sets"] == 10
    assert len(ordinal["label_sets"]) == 8
    assert ordinal["label_sets"][0]["option_values"] == [0, 10]
    console.goto(route)
    console._browser.call(
        lambda: (
            console._page.get_by_role("group", name="Prediction labels", exact=True)
            .get_by_role("button", name="10 label sets", exact=True)
            .click()
        )
    )
    console.sees("Showing 8 of 10 label sets")
    console.sees(long_label)
    console.sees("Ordinal labels")
    console._browser.call(
        lambda: console._page.get_by_role(
            "dialog", name="Prediction labels", exact=True
        ).screenshot(path=str(RUNS / "training-prediction-labels-varied-mobile.png"))
    )
    console.wait_until("() => document.documentElement.scrollWidth <= innerWidth")

    FinetuningJob.objects.filter(pk=jobs[0].pk).update(
        requested_configuration={"selection": {"train_fingerprint": "f" * 64}}
    )
    assert rest.request("GET", endpoint)["record"]["prediction_labels"]["state"] == "unavailable"
    FinetuningJob.objects.filter(pk=jobs[0].pk).update(cell=None)
    assert rest.request("GET", endpoint)["record"]["prediction_labels"]["state"] == "unavailable"
    console.goto(f"{route}&jobId={jobs[0].pk}")
    console._browser.call(
        lambda: (
            console._page.get_by_role("group", name="Prediction labels")
            .get_by_text("Unavailable", exact=True)
            .wait_for()
        )
    )
    FinetuningJob.objects.filter(pk=jobs[0].pk).update(
        hyperparameters={"objective": "assistant_cross_entropy"}
    )
    assert rest.request("GET", endpoint)["record"]["prediction_labels"] is None
    console.goto(f"{route}&jobId={jobs[0].pk}")
    console.sees("Titanic 0.8B")
    console._browser.call(
        lambda: console._page.get_by_role("group", name="Prediction labels", exact=True).wait_for(
            state="detached"
        )
    )
