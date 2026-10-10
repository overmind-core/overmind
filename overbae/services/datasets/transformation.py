from uuid import UUID

from overbae.models import DatasetPipelineRun


def records(dataset, cells):
    cells = list(cells)
    identities = set()
    for cell in cells:
        try:
            identities.add(UUID(str((cell.review or {}).get("run"))))
        except ValueError:
            continue
    runs = DatasetPipelineRun.objects.filter(
        dataset=dataset, pk__in=identities, state="completed", mode="publish"
    ).select_related("pipeline__package")
    publications = {}
    for run in runs:
        for index, step in enumerate(run.result.get("steps", [])):
            if step.get("output_cell"):
                publications[(str(run.pk), step["output_cell"])] = (run, index, step)
    result = {}
    for cell in cells:
        record = {
            "execution": "source" if cell.position == 0 else "unrecorded",
            "run": None,
            "pipeline": None,
            "revision": None,
            "package": None,
            "package_sha256": "",
            "entrypoint": "",
            "provenance": "",
        }
        # A review's run ID alone is not proof that this cell was published by that run.
        publication = publications.get((str((cell.review or {}).get("run")), str(cell.pk)))
        if publication and publication[2].get("output_fingerprint") == cell.fingerprint:
            run, index, _ = publication
            pipeline = run.pipeline
            package = pipeline.package if pipeline else None
            record.update(
                execution="isolated_container"
                if package
                else "platform_operations"
                if pipeline
                else "external_import",
                run=str(run.pk),
                pipeline=str(pipeline.pk) if pipeline else None,
                revision=pipeline.revision if pipeline else None,
                package=str(package.pk) if package else None,
                package_sha256=package.sha256 if package else "",
                entrypoint=pipeline.steps[index].get("entrypoint", "") if pipeline else "",
                provenance=run.specification.get("provenance", ""),
            )
        result[cell.pk] = record
    return result
