"""The work a dataset shows is the stage and counter the run or landing measured."""

import pytest
from conftest import frozen_dataset
from factories import make_project

from overbae.models import Dataset, DatasetPipelineRun
from overbae.services.datasets import land, list_activity

pytestmark = [pytest.mark.django_db, pytest.mark.covers("measured-facts")]


def test_a_run_shows_its_stage_and_only_the_batches_the_sandbox_counted():
    dataset = frozen_dataset(make_project())
    run = DatasetPipelineRun.objects.create(
        dataset=dataset, source=dataset.source, request_key="k", state="running"
    )

    def work(stage, steps):
        DatasetPipelineRun.objects.filter(pk=run.pk).update(result={"stage": stage, "steps": steps})
        return list_activity.current_work([dataset])[str(dataset.pk)]

    batched = work("step_1_executing", [{"batches": {"completed": 3, "total": 10}}])
    assert (batched["label"], batched["completed"], batched["total"], batched["unit"]) == (
        "Transforming",
        3,
        10,
        "batches",
    )
    unbatched = work("step_1_executing", [{}])
    assert (unbatched["completed"], unbatched["total"]) == (None, None)
    waiting = work("step_1_executing", [{"runtime": {"stage": "waiting_for_sandbox"}}])
    assert waiting["label"] == "Waiting for sandbox"
    assert work("step_1_measuring", [{}])["label"] == "Measuring"
    DatasetPipelineRun.objects.filter(pk=run.pk).update(
        result={"stage": "step_1_measuring", "steps": [{"output_rows": 50}], "rows_done": 20}
    )
    measuring = list_activity.current_work([dataset])[str(dataset.pk)]
    assert (measuring["completed"], measuring["total"], measuring["unit"]) == (20, 50, "rows")
    assert work("publishing", [{}])["label"] == "Publishing"


def test_a_landing_shows_the_rows_it_wrote():
    dataset = Dataset.objects.create(
        project=make_project(),
        name="big",
        state=Dataset.State.LANDING,
        source_spec={
            "landing_progress": {"stage": "writing", "rows_done": 4000, "rows_total": 10000}
        },
    )
    work = list_activity.current_work([dataset])[str(dataset.pk)]
    assert (work["label"], work["completed"], work["total"], work["unit"]) == (
        "Writing",
        4000,
        10000,
        "rows",
    )


def test_landing_reports_rows_written_then_measuring(tmp_path):
    source = tmp_path / "rows.jsonl"
    source.write_text("".join(f'{{"text": "t{i}"}}\n' for i in range(2500)))
    dataset = Dataset.objects.create(project=make_project(), name="rows")
    reported = []
    land.prepare(
        dataset,
        land.read_file(source, filename="rows.jsonl"),
        path=tmp_path / "out.parquet",
        on_progress=reported.append,
    )
    assert [r.get("rows_done") for r in reported if r["stage"] == "writing"] == [0, 1000, 2000]
    assert reported[-1] == {"stage": "measuring", "rows_done": 0, "rows_total": 2500}
