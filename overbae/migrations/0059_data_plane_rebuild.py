from django.db import migrations, models

INTENTS = {"explore": "pending", "ft": "train", "unstructured": "eval"}
STATS = (
    "preparation_profile",
    "p95_token_length",
    "has_multi_turn_tool_calls",
    "length_profile_method",
)
REVIEW = (
    "impact",
    "rows_before",
    "rows_after",
    "rows_removed",
    "rows_added",
    "identity_preserved",
    "input_evidence_removed",
    "input_evidence_removed_examples",
    "instruction_changes",
    "instruction_change_examples",
    "decision_changes",
    "decision_rows_removed",
    "coverage_before",
    "coverage_after",
    "removed_examples",
    "input_examples",
    "output_examples",
    "validation",
)
RESULT = ("impact", "semantic_quality", "analytics", "source_columns")


def _without(value, keys):
    return {key: item for key, item in (value or {}).items() if key not in keys}


def forward(apps, schema_editor):
    Dataset = apps.get_model("overbae", "Dataset")
    Cell = apps.get_model("overbae", "Cell")
    Run = apps.get_model("overbae", "DatasetPipelineRun")
    for old, new in INTENTS.items():
        Dataset.objects.filter(intent=old).update(intent=new)
    for dataset in Dataset.objects.filter(intent="train").iterator():
        cell = (
            Cell.objects.filter(pk=dataset.active_id).first()
            if dataset.active_id
            else Cell.objects.filter(dataset=dataset, state="ok").order_by("-position").first()
        )
        train = ((cell.intent_report or {}).get("train") or {}) if cell else {}
        if train.get("ok"):
            dataset.train_format = "decision" if train.get("format") == "decision" else "sft"
            dataset.save(update_fields=["train_format"])
    for cell in Cell.objects.only("id", "stats", "review").iterator():
        stats, review = _without(cell.stats, STATS), _without(cell.review, REVIEW)
        # New step dicts: popping from the shared ones would hide the change from the check.
        if "steps" in review:
            review["steps"] = [_without(step, ("impact",)) for step in review["steps"] or []]
        if stats != cell.stats or review != cell.review:
            Cell.objects.filter(pk=cell.pk).update(stats=stats, review=review)
    for run in Run.objects.only("id", "result").iterator():
        result = _without(run.result, RESULT)
        if "steps" in result:
            result["steps"] = [
                _without(step, ("impact", "validation")) for step in result["steps"] or []
            ]
        if result != run.result:
            Run.objects.filter(pk=run.pk).update(result=result)


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0058_remove_dataset_preparation"),
    ]

    operations = [
        migrations.AddField(
            model_name="dataset",
            name="train_format",
            field=models.CharField(
                blank=True, choices=[("sft", "Sft"), ("decision", "Decision")], max_length=8
            ),
        ),
        migrations.AlterField(
            model_name="dataset",
            name="intent",
            field=models.CharField(
                choices=[("train", "Train"), ("eval", "Eval"), ("pending", "Pending")],
                default="pending",
                max_length=8,
            ),
        ),
        migrations.RunPython(forward, migrations.RunPython.noop),
        migrations.RemoveField(model_name="cell", name="preparation_plan"),
        migrations.RemoveField(model_name="cell", name="quality_report"),
        migrations.RemoveField(model_name="dataset", name="preparation_plan"),
    ]
