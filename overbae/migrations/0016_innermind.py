import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

CHECK_SCRIPTS = {
    "exact_match": (
        "def evaluate(span):\n"
        "    if span.expected is None:\n"
        "        return None\n"
        "    output, expected = str(span.output or '').strip(), str(span.expected).strip()\n"
        "{case}"
        "    return output == expected\n"
    ),
    "contains": (
        "def evaluate(span):\n"
        "    needle = {needle}\n"
        "    if not needle:\n"
        "        return None\n"
        "    output = str(span.output or '')\n"
        "{case}"
        "    return needle in output\n"
    ),
    "regex": (
        "def evaluate(span):\n    return re.search({pattern}, str(span.output or '')) is not None\n"
    ),
    "json_schema_valid": (
        "def evaluate(span):\n"
        "    output = span.output\n"
        "    if isinstance(output, str):\n"
        "        try:\n"
        "            output = json.loads(output)\n"
        "        except ValueError:\n"
        "            return False\n"
        "    if not isinstance(output, dict):\n"
        "        return False\n"
        "    return all(key in output for key in {required})\n"
    ),
}


def check_script(config):
    check = config.get("check", "exact_match")
    template = CHECK_SCRIPTS.get(check)
    if template is None:
        return None
    lowered = bool(config.get("case_insensitive"))
    if check == "exact_match":
        case = "    output, expected = output.lower(), expected.lower()\n" if lowered else ""
        return template.format(case=case)
    if check == "contains":
        reference = config.get("reference")
        needle = repr(str(reference)) if reference else "str(span.expected or '')"
        case = "    needle, output = needle.lower(), output.lower()\n" if lowered else ""
        return template.format(needle=needle, case=case)
    if check == "regex":
        return template.format(pattern=repr(str(config.get("pattern") or "")))
    return template.format(required=repr([str(k) for k in config.get("required_keys") or []]))


def evaluator_rows(evaluator):
    """(name, type, source) triples: a judge prompt is one llm row, each checklist
    item its own llm row, a recognised check a deterministic row."""
    if evaluator is None:
        return []
    if not isinstance(evaluator, dict):
        evaluator = {
            field: getattr(evaluator, field)
            for field in (
                "display_name",
                "name",
                "kind",
                "config",
                "checklist",
                "rubric_md",
                "description",
            )
        }
    name = (evaluator.get("display_name") or evaluator.get("name") or "evaluator")[:240]
    if evaluator.get("kind") == "deterministic":
        script = check_script(evaluator.get("config") or {})
        if script:
            return [(name, "deterministic", script)]
    questions = [
        str(item.get("q")).strip()
        for item in evaluator.get("checklist") or []
        if isinstance(item, dict) and str(item.get("q") or "").strip()
    ]
    if questions:
        if len(questions) == 1:
            return [(name, "llm", questions[0])]
        return [(f"{name} · {index}", "llm", q) for index, q in enumerate(questions, 1)]
    text = (evaluator.get("rubric_md") or evaluator.get("description") or "").strip()
    return [(name, "llm", text)] if text else []


def matrix_row(matrix_model, project_id, capability_id, name, kind, source):
    row = matrix_model.objects.filter(
        project_id=project_id, capability_id=capability_id, name=name
    ).first()
    if row is None:
        row = matrix_model.objects.create(
            project_id=project_id, capability_id=capability_id, name=name, type=kind, source=source
        )
    return row


def snapshot(row):
    return {
        "name": row.name,
        "type": row.type,
        "source": row.source,
        "span_type": row.span_type,
        "span_name": row.span_name,
    }


def migrated_summary(summary, slice_):
    gates = set(summary.get("gate_metrics") or [])
    metrics = {}
    for name, cell in (slice_.get("metrics") or {}).items():
        if not isinstance(cell, dict):
            continue
        mean = cell.get("mean") if cell.get("mean") is not None else cell.get("pass_rate")
        metrics[name] = {"mean": mean, "n": cell.get("n") or 0, "failed": None}
    scored = [
        cell["mean"]
        for name, cell in metrics.items()
        if name not in gates and cell["mean"] is not None
    ]
    return {
        "migrated": True,
        "score": sum(scored) / len(scored) if scored else None,
        "metrics": metrics,
        "rows": slice_.get("n") or slice_.get("sample_count") or 0,
        "errored": (summary.get("error_counts") or {}).get("total", 0),
        "degraded": (summary.get("trust") or {}).get("degraded", 0),
        "trust": summary.get("trust") or {},
    }


def forward(apps, schema_editor):
    Evaluator = apps.get_model("overbae", "Evaluator")
    EvalMatrix = apps.get_model("overbae", "EvalMatrix")
    EvalSet = apps.get_model("overbae", "EvalSet")
    EvalSetMember = apps.get_model("overbae", "EvalSetMember")
    EvalRun = apps.get_model("overbae", "EvalRun")
    EvalVariant = apps.get_model("overbae", "EvalVariant")
    RunEvaluator = apps.get_model("overbae", "RunEvaluator")

    # The latest live version of each project evaluator becomes a matrix row.
    latest = {}
    for evaluator in Evaluator.objects.filter(project__isnull=False, is_archived=False).order_by(
        "version", "created_at"
    ):
        latest[(evaluator.project_id, evaluator.capability_id, evaluator.name)] = evaluator
    for (project_id, capability_id, _), evaluator in latest.items():
        for name, kind, source in evaluator_rows(evaluator):
            matrix_row(EvalMatrix, project_id, capability_id, name, kind, source)

    for eval_set in EvalSet.objects.all():
        members = EvalSetMember.objects.filter(eval_set=eval_set, enabled=True).select_related(
            "evaluator"
        )
        rows = [
            matrix_row(EvalMatrix, eval_set.project_id, eval_set.capability_id, name, kind, source)
            for member in members.order_by("order", "created_at")
            for name, kind, source in evaluator_rows(member.evaluator)
        ]
        eval_set.rows.add(*rows)

    for run in EvalRun.objects.all().iterator():
        run_evaluators = RunEvaluator.objects.filter(run=run, enabled=True).select_related(
            "evaluator"
        )
        matrix = []
        seen = set()
        for run_evaluator in run_evaluators.order_by("order"):
            for name, kind, source in evaluator_rows(
                run_evaluator.snapshot or run_evaluator.evaluator
            ):
                if name not in seen:
                    seen.add(name)
                    matrix.append(
                        {
                            "name": name,
                            "type": kind,
                            "source": source,
                            "span_type": "",
                            "span_name": "",
                        }
                    )
        summary = run.summary or {}
        slices = summary.get("variants") or {}
        variants = list(EvalVariant.objects.filter(run=run).order_by("order", "created_at"))
        group = uuid.uuid4()
        if not variants:
            EvalRun.objects.filter(pk=run.pk).update(
                matrix=matrix,
                mode="recorded",
                group=group,
                summary=migrated_summary(summary, next(iter(slices.values()), summary)),
            )
            continue
        for index, variant in enumerate(variants):
            fields = {
                "matrix": matrix,
                "mode": "generate" if variant.mode == "generate" else "recorded",
                "model_ref_id": variant.model_ref_id,
                "model_name": variant.model_name,
                "params": {**(variant.params or {}), "label": variant.label},
                "group": group,
                "is_baseline": variant.is_baseline,
                "summary": migrated_summary(summary, slices.get(str(variant.id)) or {}),
            }
            if index == 0:
                # The first variant keeps the row, so finetuning and optimizer links survive.
                EvalRun.objects.filter(pk=run.pk).update(**fields)
                continue
            sibling = EvalRun.objects.create(
                project_id=run.project_id,
                name=f"{run.name} · {variant.label}"[:255],
                description=run.description,
                judge_model=run.judge_model,
                dataset_id=run.dataset_id,
                cell_id=run.cell_id,
                max_items=run.max_items,
                sampling=run.sampling,
                eval_set_id=run.eval_set_id,
                status=run.status,
                error=run.error,
                triggered_by_id=run.triggered_by_id,
                completed_at=run.completed_at,
                **fields,
            )
            EvalRun.objects.filter(pk=sibling.pk).update(created_at=run.created_at)


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0015_workshop_execution_clock"),
    ]

    operations = [
        migrations.CreateModel(
            name="EvalMatrix",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                (
                    "type",
                    models.CharField(
                        choices=[("deterministic", "Deterministic"), ("llm", "Llm")],
                        max_length=16,
                    ),
                ),
                ("source", models.TextField()),
                ("span_type", models.CharField(blank=True, default="", max_length=40)),
                ("span_name", models.CharField(blank=True, default="", max_length=255)),
                ("enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "eval_matrix",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="EvalRunRow",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("row_index", models.PositiveIntegerField()),
                (
                    "source_trace_id",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("output", models.JSONField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True, default="")),
                ("degraded", models.BooleanField(default=False)),
                (
                    "degraded_reason",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("waiting", "Waiting"),
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("done", "Done"),
                            ("unknown", "Unknown"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="waiting",
                        max_length=16,
                    ),
                ),
                ("task_id", models.CharField(blank=True, default="", max_length=255)),
                ("queued_at", models.DateTimeField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["row_index"],
            },
        ),
        migrations.CreateModel(
            name="EvalScore",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "source_type",
                    models.CharField(
                        choices=[
                            ("span", "Span"),
                            ("trajectory", "Trajectory"),
                            ("dataset_row", "Dataset Row"),
                        ],
                        max_length=16,
                    ),
                ),
                ("source_id", models.CharField(max_length=255)),
                ("score", models.FloatField(blank=True, null=True)),
                ("metrics", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "eval_score",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="SignalEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("signal_type", models.CharField(max_length=64)),
                ("trace_id", models.CharField(blank=True, default="", max_length=32)),
                ("message", models.TextField()),
                ("details", models.JSONField(blank=True, default=dict)),
                ("dedupe_key", models.CharField(max_length=64)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                ("seen_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-occurred_at"],
            },
        ),
        migrations.CreateModel(
            name="SignalSubscription",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("signal_type", models.CharField(max_length=64)),
                ("enabled", models.BooleanField(default=True)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["signal_type"],
            },
        ),
        migrations.AddField(
            model_name="evalrun",
            name="group",
            field=models.UUIDField(db_index=True, default=uuid.uuid4),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="is_baseline",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="last_admitted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="matrix",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="mode",
            field=models.CharField(
                choices=[
                    ("recorded", "Recorded"),
                    ("generate", "Generate"),
                    ("provided", "Provided"),
                ],
                default="recorded",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="model_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="model_ref",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="eval_runs",
                to="overbae.modelref",
            ),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="params",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="scoring_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="scoring_task_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="span",
            name="eval_attributes",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="span",
            name="eval_claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="evalrun",
            name="max_items",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name="span",
            index=models.Index(
                condition=models.Q(("eval_attributes__has_key", "score"), _negated=True),
                fields=["project", "received_at"],
                name="span_unscored_idx",
            ),
        ),
        migrations.AddField(
            model_name="evalmatrix",
            name="capability",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="eval_matrix",
                to="overbae.capability",
            ),
        ),
        migrations.AddField(
            model_name="evalmatrix",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="evalmatrix",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="eval_matrix",
                to="overbae.project",
            ),
        ),
        migrations.AddField(
            model_name="evalset",
            name="rows",
            field=models.ManyToManyField(
                blank=True, related_name="eval_sets", to="overbae.evalmatrix"
            ),
        ),
        migrations.AddField(
            model_name="evalrunrow",
            name="run",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rows",
                to="overbae.evalrun",
            ),
        ),
        migrations.AddField(
            model_name="evalscore",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="eval_scores",
                to="overbae.project",
            ),
        ),
        migrations.AddField(
            model_name="evalscore",
            name="run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="scores",
                to="overbae.evalrun",
            ),
        ),
        migrations.AddField(
            model_name="signalevent",
            name="capability",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="signal_events",
                to="overbae.capability",
            ),
        ),
        migrations.AddField(
            model_name="signalevent",
            name="conversation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="signal_events",
                to="overbae.conversation",
            ),
        ),
        migrations.AddField(
            model_name="signalevent",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="signal_events",
                to="overbae.project",
            ),
        ),
        migrations.AddField(
            model_name="signalsubscription",
            name="capability",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="signal_subscriptions",
                to="overbae.capability",
            ),
        ),
        migrations.AddField(
            model_name="signalsubscription",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="signalsubscription",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="signal_subscriptions",
                to="overbae.project",
            ),
        ),
        migrations.AddField(
            model_name="signalevent",
            name="subscription",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="events",
                to="overbae.signalsubscription",
            ),
        ),
        migrations.AddIndex(
            model_name="evalmatrix",
            index=models.Index(
                fields=["project", "enabled"], name="eval_matrix_project_495f18_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="evalmatrix",
            constraint=models.UniqueConstraint(
                fields=("project", "capability", "name"),
                name="uniq_eval_matrix_capability_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="evalmatrix",
            constraint=models.UniqueConstraint(
                condition=models.Q(("capability__isnull", True)),
                fields=("project", "name"),
                name="uniq_eval_matrix_project_name",
            ),
        ),
        migrations.AddIndex(
            model_name="evalrunrow",
            index=models.Index(fields=["state", "expires_at"], name="eval_row_state_expiry"),
        ),
        migrations.AddConstraint(
            model_name="evalrunrow",
            constraint=models.UniqueConstraint(
                fields=("run", "row_index"), name="uniq_eval_run_row"
            ),
        ),
        migrations.AddIndex(
            model_name="evalscore",
            index=models.Index(
                fields=["source_type", "source_id", "-created_at"],
                name="eval_score_source__75478a_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="evalscore",
            index=models.Index(fields=["run", "source_id"], name="eval_score_run_id_5cea0d_idx"),
        ),
        migrations.AddIndex(
            model_name="evalscore",
            index=models.Index(
                fields=["project", "-created_at"], name="eval_score_project_bb4be9_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="signalsubscription",
            constraint=models.UniqueConstraint(
                fields=("project", "capability", "signal_type"),
                name="uniq_signal_subscription",
            ),
        ),
        migrations.AddConstraint(
            model_name="signalsubscription",
            constraint=models.UniqueConstraint(
                condition=models.Q(("capability__isnull", True)),
                fields=("project", "signal_type"),
                name="uniq_signal_subscription_project",
            ),
        ),
        migrations.AddIndex(
            model_name="signalevent",
            index=models.Index(
                fields=["project", "-occurred_at"],
                name="overbae_sig_project_bab5f0_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="signalevent",
            index=models.Index(
                condition=models.Q(("seen_at__isnull", True)),
                fields=["project", "-occurred_at"],
                name="signal_event_unseen",
            ),
        ),
        migrations.AddConstraint(
            model_name="signalevent",
            constraint=models.UniqueConstraint(
                fields=("project", "dedupe_key"), name="uniq_signal_event"
            ),
        ),
        migrations.RunPython(forward, migrations.RunPython.noop),
        migrations.RunSQL("SET CONSTRAINTS ALL IMMEDIATE", migrations.RunSQL.noop),
        migrations.RunSQL("SET CONSTRAINTS ALL DEFERRED", migrations.RunSQL.noop),
        migrations.RemoveIndex(
            model_name="annotation",
            name="overbae_ann_sample__dc9e13_idx",
        ),
        migrations.RemoveField(
            model_name="backtestrun",
            name="capability",
        ),
        migrations.AlterUniqueTogether(
            name="behaviour",
            unique_together=None,
        ),
        migrations.RemoveIndex(
            model_name="behaviour",
            name="overbae_beh_project_03219f_idx",
        ),
        migrations.AlterUniqueTogether(
            name="behaviourversion",
            unique_together=None,
        ),
        migrations.RemoveField(
            model_name="behaviourversion",
            name="behaviour",
        ),
        migrations.RemoveIndex(
            model_name="conversationevent",
            name="overbae_con_project_f47bb2_idx",
        ),
        migrations.RemoveField(
            model_name="evalgenerationrun",
            name="run",
        ),
        migrations.RemoveIndex(
            model_name="evalgenerationwork",
            name="eval_gen_state_expiry",
        ),
        migrations.RemoveIndex(
            model_name="evalsample",
            name="overbae_eva_run_id_42bc85_idx",
        ),
        migrations.RemoveIndex(
            model_name="evalsample",
            name="overbae_eva_run_id_830e8f_idx",
        ),
        migrations.RemoveIndex(
            model_name="evalsetmember",
            name="overbae_eva_eval_se_9e7594_idx",
        ),
        migrations.RemoveConstraint(
            model_name="evalsetmember",
            name="uniq_eval_set_member_role",
        ),
        migrations.RemoveIndex(
            model_name="evaluator",
            name="overbae_eva_project_91154f_idx",
        ),
        migrations.RemoveConstraint(
            model_name="evaluator",
            name="uniq_evaluator_version",
        ),
        migrations.RemoveField(
            model_name="evalrun",
            name="evaluators",
        ),
        migrations.RemoveIndex(
            model_name="evalvariant",
            name="overbae_eva_run_id_d8d183_idx",
        ),
        migrations.RemoveField(
            model_name="evidenceprofile",
            name="capability",
        ),
        migrations.RemoveIndex(
            model_name="judgecache",
            name="overbae_jud_last_us_750d2b_idx",
        ),
        migrations.DeleteModel(
            name="JudgeCache",
        ),
        migrations.RemoveIndex(
            model_name="runevaluator",
            name="overbae_run_run_id_73057d_idx",
        ),
        migrations.RemoveIndex(
            model_name="score",
            name="overbae_sco_run_id_f13f0b_idx",
        ),
        migrations.RemoveIndex(
            model_name="score",
            name="overbae_sco_sample__ed55a1_idx",
        ),
        migrations.RemoveIndex(
            model_name="score",
            name="overbae_sco_project_8d0fa7_idx",
        ),
        migrations.RemoveIndex(
            model_name="scoringpass",
            name="overbae_sco_project_960467_idx",
        ),
        migrations.AlterUniqueTogether(
            name="taskexecution",
            unique_together=None,
        ),
        migrations.RemoveIndex(
            model_name="taskexecution",
            name="overbae_tas_project_8b724f_idx",
        ),
        migrations.RemoveIndex(
            model_name="taskexecution",
            name="overbae_tas_behavio_43352f_idx",
        ),
        migrations.RemoveIndex(
            model_name="taskexecution",
            name="overbae_tas_project_87480d_idx",
        ),
        migrations.RemoveIndex(
            model_name="verdict",
            name="overbae_ver_project_8214a3_idx",
        ),
        migrations.RemoveIndex(
            model_name="verdict",
            name="overbae_ver_evaluat_5756e2_idx",
        ),
        migrations.RemoveConstraint(
            model_name="verdict",
            name="uniq_verdict_series",
        ),
        migrations.RemoveField(
            model_name="datasetcontext",
            name="auto_rubric_checklist",
        ),
        migrations.RemoveField(
            model_name="datasetcontext",
            name="auto_rubric_md",
        ),
        migrations.RemoveField(
            model_name="datasetcontext",
            name="domain",
        ),
        migrations.RemoveField(
            model_name="datasetcontext",
            name="evaluator_scores",
        ),
        migrations.RemoveField(
            model_name="datasetcontext",
            name="notes",
        ),
        migrations.RemoveField(
            model_name="datasetcontext",
            name="suggested_rubrics",
        ),
        migrations.RemoveField(
            model_name="datasetcontext",
            name="task_description",
        ),
        migrations.RemoveField(
            model_name="evalrun",
            name="data_source",
        ),
        migrations.RemoveField(
            model_name="evalrun",
            name="trace_filter",
        ),
        migrations.RemoveField(
            model_name="evalset",
            name="prompts",
        ),
        migrations.RemoveField(
            model_name="annotation",
            name="evaluator",
        ),
        migrations.RemoveField(
            model_name="annotation",
            name="project",
        ),
        migrations.RemoveField(
            model_name="annotation",
            name="sample",
        ),
        migrations.RemoveField(
            model_name="annotation",
            name="user",
        ),
        migrations.DeleteModel(
            name="BacktestRun",
        ),
        migrations.RemoveField(
            model_name="behaviour",
            name="capability",
        ),
        migrations.RemoveField(
            model_name="behaviour",
            name="project",
        ),
        migrations.RemoveField(
            model_name="conversationevent",
            name="capability",
        ),
        migrations.RemoveField(
            model_name="conversationevent",
            name="execution",
        ),
        migrations.RemoveField(
            model_name="conversationevent",
            name="project",
        ),
        migrations.DeleteModel(
            name="EvalGenerationRun",
        ),
        migrations.RemoveField(
            model_name="evalgenerationwork",
            name="sample",
        ),
        migrations.RemoveField(
            model_name="evalsample",
            name="run",
        ),
        migrations.RemoveField(
            model_name="evalsample",
            name="variant",
        ),
        migrations.RemoveField(
            model_name="evalsetmember",
            name="eval_set",
        ),
        migrations.RemoveField(
            model_name="evalsetmember",
            name="evaluator",
        ),
        migrations.RemoveField(
            model_name="evalsetmember",
            name="prompt",
        ),
        migrations.RemoveField(
            model_name="evaluator",
            name="capability",
        ),
        migrations.RemoveField(
            model_name="evaluator",
            name="created_by",
        ),
        migrations.RemoveField(
            model_name="evaluator",
            name="project",
        ),
        migrations.RemoveField(
            model_name="evalvariant",
            name="model_ref",
        ),
        migrations.RemoveField(
            model_name="evalvariant",
            name="prompt",
        ),
        migrations.RemoveField(
            model_name="evalvariant",
            name="run",
        ),
        migrations.DeleteModel(
            name="EvidenceProfile",
        ),
        migrations.RemoveField(
            model_name="runevaluator",
            name="evaluator",
        ),
        migrations.RemoveField(
            model_name="runevaluator",
            name="prompt",
        ),
        migrations.RemoveField(
            model_name="runevaluator",
            name="run",
        ),
        migrations.RemoveField(
            model_name="score",
            name="evaluator",
        ),
        migrations.RemoveField(
            model_name="score",
            name="project",
        ),
        migrations.RemoveField(
            model_name="score",
            name="run",
        ),
        migrations.RemoveField(
            model_name="score",
            name="run_evaluator",
        ),
        migrations.RemoveField(
            model_name="score",
            name="sample",
        ),
        migrations.RemoveField(
            model_name="score",
            name="variant",
        ),
        migrations.RemoveField(
            model_name="scoringpass",
            name="capability",
        ),
        migrations.RemoveField(
            model_name="scoringpass",
            name="project",
        ),
        migrations.RemoveField(
            model_name="taskexecution",
            name="behaviour",
        ),
        migrations.RemoveField(
            model_name="taskexecution",
            name="behaviour_version",
        ),
        migrations.RemoveField(
            model_name="taskexecution",
            name="capability",
        ),
        migrations.RemoveField(
            model_name="taskexecution",
            name="project",
        ),
        migrations.RemoveField(
            model_name="verdict",
            name="evaluator",
        ),
        migrations.RemoveField(
            model_name="verdict",
            name="project",
        ),
        migrations.DeleteModel(
            name="Annotation",
        ),
        migrations.DeleteModel(
            name="ConversationEvent",
        ),
        migrations.DeleteModel(
            name="EvalGenerationWork",
        ),
        migrations.DeleteModel(
            name="EvalSetMember",
        ),
        migrations.DeleteModel(
            name="RunEvaluator",
        ),
        migrations.DeleteModel(
            name="EvalSample",
        ),
        migrations.DeleteModel(
            name="EvalVariant",
        ),
        migrations.DeleteModel(
            name="Score",
        ),
        migrations.DeleteModel(
            name="ScoringPass",
        ),
        migrations.DeleteModel(
            name="Behaviour",
        ),
        migrations.DeleteModel(
            name="BehaviourVersion",
        ),
        migrations.DeleteModel(
            name="TaskExecution",
        ),
        migrations.DeleteModel(
            name="Evaluator",
        ),
        migrations.DeleteModel(
            name="Verdict",
        ),
    ]
