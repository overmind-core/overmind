from __future__ import annotations

import pytest

from overbae.models import (
    Capability,
    EvalRun,
    EvalSet,
    EvalSetMember,
    Evaluator,
    Project,
    RunEvaluator,
)
from overbae.services.eval.managed import repoint_members_to_latest

pytestmark = pytest.mark.django_db


def _template(name, version, **kw):
    return Evaluator.objects.create(
        project=None,
        is_managed=True,
        name=name,
        version=version,
        kind=Evaluator.Kind.LLM_JUDGE,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        checklist=[{"id": "q1", "q": "?", "weight": 1.0}],
        **kw,
    )


def _set(slug="mtv"):
    project = Project.objects.create(name=slug, slug=slug)
    agent = Capability.objects.create(project=project, name="A", slug=f"a-{slug}")
    return project, EvalSet.objects.create(project=project, capability=agent, name="Default")


def test_a_set_on_a_superseded_template_moves_to_the_current_version():
    # Without this, a corrected template reaches new installs only and every
    # existing set keeps the version that carried the defect.
    _, eval_set = _set()
    old = _template("Correctness", 1, rubric_md="the defect")
    new = _template("Correctness", 2, rubric_md="the fix")
    member = EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=old, role=EvalSetMember.Role.GENERATIVE
    )

    assert repoint_members_to_latest() == 1
    member.refresh_from_db()
    assert member.evaluator_id == new.id


def test_repointing_leaves_a_finished_run_untouched():
    # Reproducibility rests on RunEvaluator.snapshot, frozen at attach time, not
    # on where the eval-set member points.
    project, eval_set = _set("frozen")
    old = _template("Correctness", 1, rubric_md="as run")
    _template("Correctness", 2, rubric_md="changed since")
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=old, role=EvalSetMember.Role.GENERATIVE
    )
    run = EvalRun.objects.create(project=project, name="past", eval_set=eval_set)
    run_evaluator = RunEvaluator.objects.create(
        run=run, evaluator=old, snapshot={"name": "Correctness", "rubric_md": "as run"}
    )

    repoint_members_to_latest()

    run_evaluator.refresh_from_db()
    assert run_evaluator.snapshot["rubric_md"] == "as run"


def test_a_stale_duplicate_is_dropped_rather_than_colliding():
    # (eval_set, evaluator, role) is unique, so a set already carrying the
    # current version has nowhere to move the stale row to.
    _, eval_set = _set("dupe")
    old = _template("Correctness", 1)
    new = _template("Correctness", 2)
    stale = EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=old, role=EvalSetMember.Role.GENERATIVE
    )
    kept = EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=new, role=EvalSetMember.Role.GENERATIVE
    )

    repoint_members_to_latest()

    assert not EvalSetMember.objects.filter(pk=stale.pk).exists()
    assert EvalSetMember.objects.filter(pk=kept.pk).exists()


def test_the_same_name_under_two_roles_keeps_both():
    _, eval_set = _set("roles")
    old = _template("Correctness", 1)
    new = _template("Correctness", 2)
    for role in (EvalSetMember.Role.GENERATIVE, EvalSetMember.Role.TRACE_SCORING):
        EvalSetMember.objects.create(eval_set=eval_set, evaluator=old, role=role)

    assert repoint_members_to_latest() == 2
    assert EvalSetMember.objects.filter(eval_set=eval_set, evaluator=new).count() == 2


def test_repointing_is_idempotent():
    _, eval_set = _set("idem")
    old = _template("Correctness", 1)
    _template("Correctness", 2)
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=old, role=EvalSetMember.Role.GENERATIVE
    )

    assert repoint_members_to_latest() == 1
    assert repoint_members_to_latest() == 0


def test_an_archived_version_never_becomes_the_target():
    _, eval_set = _set("arch")
    old = _template("Correctness", 1)
    _template("Correctness", 2, is_archived=True)
    member = EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=old, role=EvalSetMember.Role.GENERATIVE
    )

    assert repoint_members_to_latest() == 0
    member.refresh_from_db()
    assert member.evaluator_id == old.id


def test_an_agent_scoped_evaluator_is_left_alone():
    # Only global templates are versioned this way; an agent's own row is edited
    # in place, and a customised copy must not be dragged onto a template.
    project, eval_set = _set("scoped")
    capability = eval_set.capability
    _template("Correctness", 1)
    _template("Correctness", 2)
    mine = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Correctness",
        version=1,
        kind=Evaluator.Kind.LLM_JUDGE,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        checklist=[{"id": "q1", "q": "?", "weight": 1.0}],
    )
    member = EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=mine, role=EvalSetMember.Role.GENERATIVE
    )

    assert repoint_members_to_latest() == 0
    member.refresh_from_db()
    assert member.evaluator_id == mine.id
