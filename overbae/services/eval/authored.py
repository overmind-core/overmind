"""Generator-authored :class:`Evaluator` rows. Superseding archives rather
than deletes so history survives."""

from __future__ import annotations

from collections.abc import Callable

from django.db.models import Max

from overbae.models import Evaluator
from overbae.services.eval import profiles
from overbae.services.eval.specs import EvaluatorSpec


# The dataset id is the only safe match key: ``data_version`` is shared across
# generations and often empty.
# Regenerating prompt A's evals must never archive prompt B's on the same dataset.
def author_judge_evaluator(validated_data: dict, user) -> Evaluator:
    """Version scope matches the model's (project, capability, name) uniqueness constraint."""
    from overbae.services.eval.rubric_compiler import (
        attach_compiled_checklist,
        compose_judge_evaluator_kwargs,
    )

    # Compiled here rather than left for later: a generative judge scores the
    # weighted fraction of its checklist that passes, so one saved without a
    # checklist is not gradable and would be refused when a run attaches it.
    kwargs = attach_compiled_checklist(compose_judge_evaluator_kwargs(validated_data))
    latest = (
        Evaluator.objects.filter(
            project=kwargs["project"], capability=kwargs["capability"], name=kwargs["name"]
        )
        .aggregate(v=Max("version"))
        .get("v")
    )
    return Evaluator.objects.create(created_by=user, version=(latest or 0) + 1, **kwargs)


def persist_specs(
    specs: list[EvaluatorSpec],
    *,
    project,
    capability=None,
    created_by=None,
    prepare: Callable[[EvaluatorSpec, dict], bool] | None = None,
) -> list[Evaluator]:
    """*prepare* is ``(spec, kwargs) -> is_archived`` and may mutate ``kwargs``,
    including ``kwargs["capability"]`` to rescope one row. Returns rows in *specs* order."""
    grades = profiles.grades_for_capability(capability.pk) if capability is not None else {}
    created: list[Evaluator] = []
    for spec in specs:
        kwargs = spec.to_evaluator_kwargs()
        unverifiable = profiles.unverifiable_clauses(grades, spec.warrant)
        if unverifiable:
            kwargs["config"]["unverifiable"] = {
                "clauses": unverifiable,
                "guidance": [profiles.GUIDANCE_BY_CLAUSE[c] for c in unverifiable],
            }
        is_archived = prepare(spec, kwargs) if prepare is not None else False
        # A *prepare*-set override must not collide with ``capability=`` below.
        row_capability = kwargs.pop("capability", capability)
        latest = (
            Evaluator.objects.filter(
                project=project, capability=row_capability, name=kwargs["name"]
            )
            .aggregate(v=Max("version"))
            .get("v")
        )
        created.append(
            Evaluator.objects.create(
                project=project,
                capability=row_capability,
                created_by=created_by,
                version=(latest or 0) + 1,
                is_archived=is_archived,
                **kwargs,
            )
        )
    return created
