from django.db import migrations

# Tasks that left the code while the database scheduler kept their rows. Beat still
# dispatches an enabled row, and the worker rejects it as unregistered.
RETIRED_TASKS = (
    "overbae.tasks.backtesting.auto_run_backtesting",
    "overbae.tasks.base_models.prewarm_base_models",
    "overbae.tasks.baseten_billing_sync.sync_baseten_finetuning_costs",
    "overbae.tasks.context_graph.check_context_graph_consistency",
    "overbae.tasks.context_graph.gc_context_span_nodes",
    "overbae.tasks.context_graph.repair_score_edges",
    "overbae.tasks.context_graph.sweep_unembedded_context_nodes",
    "overbae.tasks.datasets.sweep_executions",
    "overbae.tasks.decision_jobs.reconcile",
    "overbae.tasks.evaluations.auto_evaluate_unscored_spans",
    "overbae.tasks.github_divergence.check_repo_divergence",
    "overbae.tasks.github_token_refresh.refresh_github_tokens",
    "overbae.tasks.inference_controller.janitor_stuck_fsm",
    "overbae.tasks.ingestion.expire_stale_ingestions",
    "overbae.tasks.job_cleanup.cleanup_old_jobs",
    "overbae.tasks.job_reconciler.reconcile_pending_jobs",
    "overbae.tasks.job_timeout.timeout_stale_jobs",
    "overbae.tasks.otel_pr.sweep_open_otel_prs",
    "overbae.tasks.prompt_improvement.auto_improve_prompts",
    "overbae.tasks.trace_dataset.auto_transform_traces",
    "overbae.tasks.trace_scoring.sweep_unscored_traces",
    "overbae.tasks.workshop_timeout.timeout_stale_workshop_runs",
)


def delete_retired_beat_tasks(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.using(schema_editor.connection.alias).filter(
        task__in=RETIRED_TASKS
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("overbae", "0066_remove_optimizer"), ("django_celery_beat", "0001_initial")]

    operations = [migrations.RunPython(delete_retired_beat_tasks, migrations.RunPython.noop)]
