from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.api.serializers import CapabilitySerializer
from overbae.core.errors import InputValidationError
from overbae.models import Capability, DeployedModel, ModelActivation, Project
from overbae.services import model_activation as service
from overbae.services import operational_progress
from overbae.services.deployed_chat import is_cold_start, record_inference_call

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(serving):
    project = Project.objects.create(name="Serving")
    old = DeployedModel.objects.create(project=project, model_id="old", status="ready")
    target = DeployedModel.objects.create(project=project, model_id="new", status="ready")
    capability = Capability.objects.create(
        project=project, name="Agent", slug="agent", active_model=old
    )
    return capability, old, target


def advance(activation):
    ModelActivation.objects.filter(pk=activation.pk).update(next_poll_at=timezone.now())
    service.advance_activation(activation.pk)
    activation.refresh_from_db()


def verify(activation):
    advance(activation)
    advance(activation)


def test_routing_switches_only_after_verification_and_retains_previous(setup):
    capability, old, target = setup
    activation = service.start_activation(capability.pk, target.pk)
    assert activation.stage == "checking"
    verify(activation)
    capability.refresh_from_db()
    assert capability.active_model == old
    assert activation.stage == "switching"
    advance(activation)
    capability.refresh_from_db()
    assert capability.active_model == target
    assert capability.previous_active_model == old
    assert capability.first_application_request_at is None
    assert activation.stage == "complete"
    assert not is_cold_start(target)
    rollback = service.start_activation(capability.pk, old.pk)
    verify(rollback)
    advance(rollback)
    capability.refresh_from_db()
    assert capability.active_model == old
    assert capability.previous_active_model == target


def test_failed_verification_keeps_routing_and_retry_starts_new_attempt(setup, fake_modal):
    def out_of_memory(**_):
        raise RuntimeError("CUDA out of memory")

    fake_modal.deploy("overmind-inference", "pre_warm", out_of_memory)
    capability, old, target = setup
    activation = service.start_activation(capability.pk, target.pk)
    verify(activation)
    capability.refresh_from_db()
    assert capability.active_model == old
    assert activation.stage == "failed"
    assert activation.failed_stage == "verifying"
    assert "out of memory" in activation.error
    retry = service.start_activation(capability.pk, target.pk)
    assert retry.generation != activation.generation
    assert retry.stage == "checking"
    assert retry.error == ""


def test_duplicate_requests_and_worker_delivery_do_not_duplicate_verification(setup, fake_modal):
    capability, _, target = setup
    activation = service.start_activation(capability.pk, target.pk)
    assert service.start_activation(capability.pk, target.pk).generation == activation.generation

    def redelivered(**_):
        service.advance_activation(activation.pk)

    fake_modal.deploy("overmind-inference", "pre_warm", redelivered)
    advance(activation)

    assert fake_modal.spawns() == ["pre_warm"]
    assert activation.call_id == next(iter(fake_modal.calls))


def test_competing_switch_is_rejected_and_clearing_cancels_pending_switch(setup):
    capability, old, target = setup
    activation = service.start_activation(capability.pk, target.pk)
    with pytest.raises(InputValidationError, match="already in progress"):
        service.start_activation(capability.pk, old.pk)
    verify(activation)
    service.start_activation(capability.pk, None)
    advance(activation)
    capability.refresh_from_db()
    assert capability.active_model is None
    assert activation.stage == "cancelled"
    assert (
        operational_progress.latest(capability.project_id, "model_activation", activation.pk)[
            "status"
        ]
        == "cancelled"
    )


def test_poll_transport_error_reconnects_and_deadline_preserves_incumbent(setup, fake_modal):
    capability, old, target = setup
    activation = service.start_activation(capability.pk, target.pk)
    advance(activation)
    call = fake_modal.calls[activation.call_id]
    call.unreachable = ConnectionError("offline")
    advance(activation)
    assert activation.stage == "verifying"
    assert activation.call_id == call.object_id
    ModelActivation.objects.filter(pk=activation.pk).update(
        deadline=timezone.now() - timedelta(seconds=1)
    )
    advance(activation)
    capability.refresh_from_db()
    assert activation.stage == "failed"
    assert capability.active_model == old


def test_deleted_target_cannot_become_live_after_verification(setup):
    capability, old, target = setup
    activation = service.start_activation(capability.pk, target.pk)
    verify(activation)
    target.delete()
    advance(activation)
    capability.refresh_from_db()
    assert activation.stage == "failed"
    assert capability.active_model == old


def test_serializer_queues_activation_without_optimistic_switch(setup):
    capability, old, target = setup
    serializer = CapabilitySerializer(
        capability, data={"active_model": str(target.pk)}, partial=True
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    assert str(serializer.data["active_model"]) == str(old.pk)
    assert serializer.data["activation"]["stage"] == "checking"
    stale = CapabilitySerializer(capability, data={"description": "changed"}, partial=True)
    stale.is_valid(raise_exception=True)
    activation = capability.activation
    verify(activation)
    advance(activation)
    stale.save()
    capability.refresh_from_db()
    assert capability.active_model == target


def test_cross_project_activation_is_rejected(setup):
    capability, _, target = setup
    target.project = Project.objects.create(name="Other", slug="other")
    target.save()
    with pytest.raises(InputValidationError):
        service.start_activation(capability.pk, target.pk)


def test_only_successful_application_alias_traffic_confirms_connection(setup):
    capability, _, target = setup
    activation = service.start_activation(capability.pk, target.pk)
    verify(activation)
    advance(activation)
    kwargs = {"requested_model": f"overmind/{capability.pk}", "source": "application"}
    record_inference_call(target, {}, 15, outcome="failed", **kwargs)
    record_inference_call(target, {}, 15)
    record_inference_call(target, {}, 15, **{**kwargs, "requested_model": target.model_id})
    capability.refresh_from_db()
    assert capability.first_application_request_at is None
    record_inference_call(target, {}, 15, **kwargs)
    capability.refresh_from_db()
    assert capability.first_application_request_at is not None
    first = capability.first_application_request_at
    record_inference_call(target, {}, 20, **kwargs)
    capability.refresh_from_db()
    assert capability.first_application_request_at == first
    assert capability.last_application_request_at > first
