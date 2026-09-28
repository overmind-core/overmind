from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from overbae.core.errors import InputValidationError
from overbae.models import Capability, DeployedModel, ModelActivation
from overbae.services.deployment import poll_operation, spawn_verification

logger = logging.getLogger(__name__)
ACTIVE_STAGES = ("checking", "verifying", "switching")


def start_activation(capability_id, target_id) -> ModelActivation | None:
    with transaction.atomic():
        capability = Capability.objects.select_for_update().get(pk=capability_id)
        activation = ModelActivation.objects.filter(capability=capability).first()
        if target_id is None:
            if activation:
                ModelActivation.objects.filter(pk=activation.pk).update(
                    stage="cancelled", generation=uuid.uuid4(), claim=None, claim_until=None
                )
            Capability.objects.filter(pk=capability.pk).update(
                previous_active_model_id=capability.active_model_id,
                active_model=None,
                active_model_activated_at=None,
                first_application_request_at=None,
                last_application_request_at=None,
                updated_at=timezone.now(),
            )
            return None
        target = DeployedModel.objects.filter(
            pk=target_id, project_id=capability.project_id
        ).first()
        if (
            not target
            or target.status != "ready"
            or target.project_id != capability.project_id
            or not capability.is_current
        ):
            raise InputValidationError("Select a ready deployment in this capability's project.")
        if activation and activation.stage in ACTIVE_STAGES:
            if activation.target_id == target.pk:
                return activation
            raise InputValidationError("A model activation is already in progress.")
        if capability.active_model_id == target.pk:
            return activation
        now = timezone.now()
        activation, _ = ModelActivation.objects.update_or_create(
            capability=capability,
            defaults={
                "target": target,
                "generation": uuid.uuid4(),
                "stage": "checking",
                "failed_stage": "",
                "error": "",
                "call_id": "",
                "dispatching": False,
                "claim": None,
                "claim_until": None,
                "started_at": now,
                "next_poll_at": now,
                "deadline": now + timedelta(minutes=50),
                "completed_at": None,
            },
        )
        return activation


def due_activations():
    now = timezone.now()
    return ModelActivation.objects.filter(stage__in=ACTIVE_STAGES, next_poll_at__lte=now).filter(
        Q(claim_until__isnull=True) | Q(claim_until__lte=now)
    )


def advance_activation(activation_id) -> None:
    claim = uuid.uuid4()
    now = timezone.now()
    if (
        not due_activations()
        .filter(pk=activation_id)
        .update(claim=claim, claim_until=now + timedelta(seconds=45))
    ):
        return
    activation = ModelActivation.objects.select_related("target").get(pk=activation_id)
    owned = ModelActivation.objects.filter(
        pk=activation_id, generation=activation.generation, claim=claim, stage__in=ACTIVE_STAGES
    )
    changes = {"next_poll_at": now + timedelta(seconds=15)}
    try:
        if activation.deadline <= now:
            changes.update(
                stage="failed",
                failed_stage=activation.stage,
                error="Verification timed out. Retry activation.",
            )
        elif not activation.target or activation.target.status != "ready":
            changes.update(
                stage="failed",
                failed_stage=activation.stage,
                error="The deployment is no longer ready.",
            )
        elif activation.stage == "checking":
            if activation.dispatching:
                changes.update(
                    stage="failed",
                    failed_stage="verifying",
                    error="Verification was interrupted. Retry activation.",
                )
            else:
                if not owned.update(dispatching=True):
                    return
                call_id = spawn_verification(activation.target)
                changes.update(stage="verifying", call_id=call_id, dispatching=False)
        elif activation.stage == "verifying":
            state, result = poll_operation(activation.call_id)
            if state == "complete":
                changes.update(stage="switching", next_poll_at=now)
            elif state == "failed":
                changes.update(stage="failed", failed_stage="verifying", error=str(result))
        elif activation.stage == "switching":
            with transaction.atomic():
                capability = Capability.objects.select_for_update().get(pk=activation.capability_id)
                # Clear, retry and switch all take the capability lock before changing the operation.
                if not owned.exists():
                    return
                target = (
                    DeployedModel.objects.select_for_update()
                    .filter(pk=activation.target_id)
                    .first()
                )
                if (
                    not target
                    or target.status != "ready"
                    or target.project_id != capability.project_id
                    or not capability.is_current
                ):
                    changes.update(
                        stage="failed",
                        failed_stage="switching",
                        error="The capability or deployment is no longer available.",
                    )
                else:
                    Capability.objects.filter(pk=capability.pk).update(
                        previous_active_model_id=capability.active_model_id,
                        active_model=target,
                        active_model_activated_at=now,
                        first_application_request_at=None,
                        last_application_request_at=None,
                        updated_at=now,
                    )
                    changes.update(stage="complete", completed_at=now)
                owned.update(**changes, claim=None, claim_until=None)
                return
    except Exception:
        logger.exception("Activation %s failed during %s", activation.id, activation.stage)
        if activation.stage == "checking":
            changes.update(
                stage="failed",
                failed_stage="verifying",
                error="Could not start verification. Retry activation.",
            )
        # Poll transport errors retain the saved handle until the deadline.
    finally:
        owned.update(**changes, claim=None, claim_until=None)


def activation_progress(activation: ModelActivation | None) -> dict | None:
    if activation is None:
        return None
    return {
        "id": str(activation.pk),
        "target": str(activation.target_id) if activation.target_id else None,
        "stage": activation.stage,
        "failed_stage": activation.failed_stage,
        "error": activation.error,
        "started_at": activation.started_at.isoformat(),
        "completed_at": activation.completed_at.isoformat() if activation.completed_at else None,
    }
