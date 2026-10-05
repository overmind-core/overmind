"""Soft-disconnect keeps credential UUID so reconnect dedupes spans."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from factories import make_project, sync_until_live
from fakes.vendors import LangfuseAPI, support_desk_trace

from overbae.api.serializers import ConnectorCredentialSerializer
from overbae.api.views import ConnectorCredentialViewSet
from overbae.models import ConnectorCredential, ConnectorSyncConfig, Project, Span

pytestmark = pytest.mark.django_db


def _cred(project: Project, *, key: str = "pk-same", name: str = "LF") -> ConnectorCredential:
    return ConnectorCredential.objects.create(
        project=project,
        name=name,
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key=key,
        api_secret="sk",
        base_url="https://cloud.langfuse.com",
    )


def _finish_setup(cred: ConnectorCredential) -> None:
    ConnectorSyncConfig.objects.create(
        credential=cred,
        version=1,
        effective_from=timezone.now(),
        lookback_days=7,
    )


def test_disconnect_soft_deactivates_finished_connection():
    project = make_project()
    cred = _cred(project)
    _finish_setup(cred)

    ConnectorCredentialViewSet().perform_destroy(cred)
    cred.refresh_from_db()
    assert cred.is_active is False
    assert cred.auto_sync_enabled is False
    assert ConnectorCredential.objects.filter(pk=cred.pk).exists()


def test_disconnect_hard_deletes_unfinished_draft():
    project = make_project()
    cred = _cred(project)
    # No ConnectorSyncConfig → wizard draft.
    pk = cred.pk
    ConnectorCredentialViewSet().perform_destroy(cred)
    assert not ConnectorCredential.objects.filter(pk=pk).exists()


def test_reconnect_same_key_reactivates_and_imports_no_duplicate_spans(fake_llm, slept):
    langfuse = LangfuseAPI(host="https://cloud.langfuse.com")
    langfuse.record(*support_desk_trace(timezone.now() - timedelta(hours=1)))
    fake_llm.network.vendors.append(langfuse)
    project = make_project()
    cred = _cred(project)
    _finish_setup(cred)
    sync_until_live(cred)
    imported = Span.objects.filter(project=project).count()
    assert imported

    ConnectorCredentialViewSet().perform_destroy(cred)
    cred.refresh_from_db()
    assert cred.is_active is False

    reactivated = ConnectorCredentialSerializer().create(
        {
            "project": project,
            "name": "LF",
            "connector_type": ConnectorCredential.ConnectorType.LANGFUSE,
            "base_url": "https://cloud.langfuse.com",
            "api_key": "pk-same",
            "api_secret": "sk-new",
            "auto_sync_enabled": False,
        }
    )
    assert reactivated.pk == cred.pk
    assert reactivated.is_active is True
    assert reactivated.api_secret == "sk-new"

    ConnectorCredential.objects.filter(pk=cred.pk).update(sync_cursor={})
    sync_until_live(reactivated)
    assert Span.objects.filter(project=project).count() == imported


def test_reconnect_via_serializer_is_valid_despite_inactive_name_collision():
    """API path runs validators before create(); inactive rows must not 400."""
    project = make_project()
    cred = _cred(project, name="Langfuse")
    _finish_setup(cred)
    ConnectorCredentialViewSet().perform_destroy(cred)
    cred.refresh_from_db()
    assert cred.is_active is False

    ser = ConnectorCredentialSerializer(
        data={
            "project": str(project.id),
            "name": "Langfuse",
            "connector_type": ConnectorCredential.ConnectorType.LANGFUSE,
            "base_url": "https://cloud.langfuse.com",
            "api_key": "pk-same",
            "api_secret": "sk-new",
            "auto_sync_enabled": False,
        }
    )
    assert ser.is_valid(), ser.errors
    reactivated = ser.save()
    assert reactivated.pk == cred.pk
    assert reactivated.is_active is True
    assert reactivated.name == "Langfuse"


def test_reconnect_new_key_frees_inactive_name_slot_via_serializer():
    project = make_project()
    old = _cred(project, key="pk-old", name="Langfuse")
    _finish_setup(old)
    ConnectorCredentialViewSet().perform_destroy(old)
    old.refresh_from_db()

    ser = ConnectorCredentialSerializer(
        data={
            "project": str(project.id),
            "name": "Langfuse",
            "connector_type": ConnectorCredential.ConnectorType.LANGFUSE,
            "base_url": "https://cloud.langfuse.com",
            "api_key": "pk-new",
            "api_secret": "sk-new",
            "auto_sync_enabled": False,
        }
    )
    assert ser.is_valid(), ser.errors
    created = ser.save()
    assert created.pk != old.pk
    assert created.is_active is True
    assert created.name == "Langfuse"
    old.refresh_from_db()
    assert old.is_active is False
    assert old.name.startswith("Langfuse · disconnected ·")


def test_active_name_collision_still_rejected():
    project = make_project()
    live = _cred(project, key="pk-a", name="Langfuse")
    _finish_setup(live)  # a configured connection owns its name
    ser = ConnectorCredentialSerializer(
        data={
            "project": str(project.id),
            "name": "Langfuse",
            "connector_type": ConnectorCredential.ConnectorType.LANGFUSE,
            "base_url": "https://cloud.langfuse.com",
            "api_key": "pk-b",
            "api_secret": "sk-b",
            "auto_sync_enabled": False,
        }
    )
    assert not ser.is_valid()
    assert "non_field_errors" in ser.errors


def _setup_data(project: Project, *, key: str, name: str = "Langfuse") -> dict:
    return {
        "project": str(project.id),
        "name": name,
        "connector_type": ConnectorCredential.ConnectorType.LANGFUSE,
        "base_url": "https://cloud.langfuse.com",
        "api_key": key,
        "api_secret": "sk",
        "auto_sync_enabled": False,
    }


def test_retry_reclaims_abandoned_draft_with_same_key():
    """Leaving setup after verify strands an active draft; retrying must reuse it."""
    project = make_project()
    draft = _cred(project, key="pk-same", name="Langfuse")  # no config → draft

    ser = ConnectorCredentialSerializer(data=_setup_data(project, key="pk-same"))
    assert ser.is_valid(), ser.errors
    reclaimed = ser.save()

    assert reclaimed.pk == draft.pk
    assert ConnectorCredential.objects.filter(project=project).count() == 1


def test_retry_with_new_key_discards_abandoned_draft_holding_the_name():
    project = make_project()
    draft = _cred(project, key="pk-old", name="Langfuse")

    ser = ConnectorCredentialSerializer(data=_setup_data(project, key="pk-new"))
    assert ser.is_valid(), ser.errors
    created = ser.save()

    assert created.pk != draft.pk
    # Nothing synced under the draft, so it is dropped rather than renamed.
    assert not ConnectorCredential.objects.filter(pk=draft.pk).exists()


def test_sweep_deletes_stale_drafts_only():
    from overbae.tasks.connector_sync import _DRAFT_TTL, sweep_abandoned_drafts

    project = make_project()
    fresh = _cred(project, key="pk-fresh", name="Fresh")
    stale = _cred(project, key="pk-stale", name="Stale")
    configured = _cred(project, key="pk-live", name="Live")
    _finish_setup(configured)
    old = timezone.now() - _DRAFT_TTL - timedelta(minutes=1)
    ConnectorCredential.objects.filter(pk__in=[stale.pk, configured.pk]).update(created_at=old)

    assert sweep_abandoned_drafts()["deleted"] == 1

    assert not ConnectorCredential.objects.filter(pk=stale.pk).exists()
    assert ConnectorCredential.objects.filter(pk=fresh.pk).exists()
    assert ConnectorCredential.objects.filter(pk=configured.pk).exists()
