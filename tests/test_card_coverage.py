from __future__ import annotations

import pytest

from overbae.models import Capability, Evaluator, Project
from overbae.services.eval.card_compiler import compile_card_evaluators, deterministic_coverage
from overbae.services.eval.grounding import EvalGroundingContext

pytestmark = pytest.mark.django_db

CARD = {
    "output_fields": {
        "amount": "number — the total due",
        "dueDate": "string (YYYY-MM-DD) — payment due date",
        "isInvoice": "boolean — whether this is a payable invoice",
        "vendor": "string — who issued it",
        "confidence": "number — self-reported certainty",
    },
    "output_schema": {
        "required_keys": ["amount", "dueDate", "isInvoice", "vendor", "confidence"],
        "properties": {
            "amount": {"type": "number"},
            "dueDate": {"type": "string", "format": "date"},
            "isInvoice": {"type": "boolean"},
            "vendor": {"type": "string"},
            "confidence": {"type": "number"},
        },
    },
}


def _grounding(inventory=None):
    project = Project.objects.create(name="cov", slug="cov")
    agent = Capability.objects.create(project=project, name="A", slug="a-cov")
    return EvalGroundingContext(
        capability=agent,
        codebase_card=CARD,
        evaluator_inventory=inventory or [],
    )


def _already_materialized(grounding, spec):
    """An Evaluator row carrying the spec's provenance, as a real scan leaves."""
    return Evaluator.objects.create(
        project=grounding.capability.project,
        capability=grounding.capability,
        name=spec.name,
        kind=spec.kind,
        scope=spec.scope,
        config={**spec.config, "provenance": spec.provenance.model_dump()},
    )


def test_coverage_survives_the_dedup_that_hides_it():
    """compile_card_evaluators answers what still needs creating; coverage must
    still name the exact checks a judge must not re-author."""
    grounding = _grounding()
    field_check = next(
        s for s in deterministic_coverage(grounding) if s.config.get("check") == "canonical_fields"
    )

    grounding.evaluator_inventory = [_already_materialized(grounding, field_check)]

    assert field_check.name not in {s.name for s in compile_card_evaluators(grounding)}
    assert field_check.name in {s.name for s in deterministic_coverage(grounding)}


def test_coverage_names_the_exactly_checked_fields():
    covered = {
        field
        for spec in deterministic_coverage(_grounding())
        for field in (f.get("name") for f in spec.config.get("fields") or [])
    }
    # Canonically-typed fields are checked exactly; a self-reported confidence is not.
    assert {"amount", "dueDate", "isInvoice", "vendor"} <= covered
    assert "confidence" not in covered


def test_coverage_excludes_judges():
    """It answers what is checked EXACTLY. A judge is the thing being advised."""
    assert all(spec.kind != "llm_judge" for spec in deterministic_coverage(_grounding()))
