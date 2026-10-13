"""Celery tasks — imported here so autodiscover registers them at worker startup."""

from overbae.api.otlp import process_span

from . import (  # noqa: E402
    baseten_billing_sync,
    capability_rebind,
    cleanup_modal,
    cleanup_tmp,
    connector_sync,
    dataset_context,
    datasets,
    finetuning,
    finetuning_reconciler,
    guest_cleanup,
    inference_controller,
    inference_requests,
    innermind,
    model_deployment,
    optimizer_reconciler,
    queue_metrics,
    training_experiments,
    training_preparation,
)
from .dataset_context import refresh_dataset_context

__all__ = [
    "baseten_billing_sync",
    "capability_rebind",
    "cleanup_modal",
    "cleanup_tmp",
    "connector_sync",
    "dataset_context",
    "datasets",
    "finetuning",
    "finetuning_reconciler",
    "guest_cleanup",
    "inference_controller",
    "inference_requests",
    "innermind",
    "optimizer_reconciler",
    "queue_metrics",
    "model_deployment",
    "process_span",
    "refresh_dataset_context",
    "training_experiments",
    "training_preparation",
]
