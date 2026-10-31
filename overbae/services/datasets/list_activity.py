from overbae.models import Activity, DatasetPipelineRun, DatasetPreparation, DatasetTransfer


def current_work(datasets):
    ids = [str(dataset.pk) for dataset in datasets]
    work = {}
    runs = list(
        DatasetPipelineRun.objects.filter(
            dataset_id__in=ids, state__in=["queued", "running"]
        ).order_by("created_at")
    )
    actors = dict(
        Activity.objects.filter(
            project_id__in={dataset.project_id for dataset in datasets},
            task_kind="dataset_pipeline",
            task_id__in=[str(run.pk) for run in runs],
        ).values_list("task_id", "actor_kind")
    )
    for run in runs:
        work[str(run.dataset_id)] = {
            "label": "Previewing" if run.mode == "preview" else "Transforming",
            "status": run.state,
            "actor_kind": actors.get(str(run.pk)),
            "completed": None,
            "total": None,
            "unit": "",
        }
    for transfer in DatasetTransfer.objects.filter(
        specification__dataset__in=ids, state="uploading"
    ).order_by("created_at"):
        members = transfer.specification.get("files") or [transfer.specification]
        work[transfer.specification["dataset"]] = {
            "label": "Uploading",
            "actor_kind": None,
            "status": "running",
            "completed": transfer.received,
            "total": sum(member["size"] for member in members),
            "unit": "bytes",
        }
    for preparation in DatasetPreparation.objects.filter(
        dataset_id__in=ids, state__in=["preparing", "paused"]
    ):
        work.setdefault(
            str(preparation.dataset_id),
            {
                "label": "Preparing" if preparation.state == "preparing" else "Preparation paused",
                "status": preparation.state,
                "actor_kind": preparation.actor_kind,
                "completed": None,
                "total": None,
                "unit": "",
            },
        )
    return work
