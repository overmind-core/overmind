"""Celery tasks — imported here so autodiscover registers them at worker startup."""

from overbae.api.otlp import process_span

from . import (  # noqa: E402
    baseten_billing_sync,
    behaviour,
    capability_rebind,
    cleanup_modal,
    cleanup_tmp,
    connector_sync,
    dataset_context,
    datasets,
    eval,
    eval_watchdog,
    finetuning,
    finetuning_reconciler,
    guest_cleanup,
    inference_controller,
    model_deployment,
    optimizer_reconciler,
    trace_scoring,
    training_preparation,
)
from .dataset_context import refresh_dataset_context

__all__ = [
    "baseten_billing_sync",
    "behaviour",
    "capability_rebind",
    "cleanup_modal",
    "cleanup_tmp",
    "connector_sync",
    "dataset_context",
    "datasets",
    "eval",
    "eval_watchdog",
    "finetuning",
    "finetuning_reconciler",
    "guest_cleanup",
    "inference_controller",
    "optimizer_reconciler",
    "model_deployment",
    "process_span",
    "refresh_dataset_context",
    "trace_scoring",
    "training_preparation",
]
