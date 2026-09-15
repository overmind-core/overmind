"""The pre_save/post_save pair on Capability: any writer that changes
``improvement_metadata.capability_card`` must enqueue the Tier-0 evaluator sync,
and saves that cannot touch the card must not pay the lookup."""

from __future__ import annotations

from unittest import mock

import pytest

from overbae.models import Capability
from tests.factories import make_capability, make_project

pytestmark = pytest.mark.django_db


def _capability_with_card(card) -> Capability:
    return make_capability(make_project(), improvement_metadata={"capability_card": card})


def test_card_change_on_plain_save_enqueues_sync(django_capture_on_commit_callbacks):
    capability = _capability_with_card({"purpose": "v1"})
    capability.improvement_metadata = {"capability_card": {"purpose": "v2"}}
    with (
        mock.patch("overbae.tasks.eval.sync_card_evaluators_task.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        capability.save()
    delay.assert_called_once_with(capability_id=str(capability.pk))


def test_card_change_via_update_fields_enqueues_sync(django_capture_on_commit_callbacks):
    capability = _capability_with_card({"purpose": "v1"})
    capability.improvement_metadata = {"capability_card": {"purpose": "v2"}}
    with (
        mock.patch("overbae.tasks.eval.sync_card_evaluators_task.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        capability.save(update_fields=["improvement_metadata"])
    delay.assert_called_once_with(capability_id=str(capability.pk))


def test_unchanged_card_does_not_enqueue(django_capture_on_commit_callbacks):
    capability = _capability_with_card({"purpose": "v1"})
    capability.improvement_metadata = {
        "capability_card": {"purpose": "v1"},
        "preload_status": "done",
    }
    with (
        mock.patch("overbae.tasks.eval.sync_card_evaluators_task.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        capability.save(update_fields=["improvement_metadata"])
    delay.assert_not_called()


def test_disjoint_update_fields_skips_the_card_lookup(django_assert_num_queries):
    capability = _capability_with_card({"purpose": "v1"})
    capability.cli_version = "9.9.9"
    with django_assert_num_queries(1):
        capability.save(update_fields=["cli_version"])


def test_created_capability_does_not_enqueue(django_capture_on_commit_callbacks):
    with (
        mock.patch("overbae.tasks.eval.sync_card_evaluators_task.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        _capability_with_card({"purpose": "v1"})
    delay.assert_not_called()
