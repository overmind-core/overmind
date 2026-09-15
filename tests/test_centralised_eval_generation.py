from __future__ import annotations

import pytest

from overbae.models import Capability, EvalSetMember, Evaluator, Project
from overbae.services.eval import semantic_recommender
from overbae.services.eval.eval_set import generate_and_preload_default_set


@pytest.fixture
def grounded_capability(db):
    project = Project.objects.create(name="centralise", slug="centralise")
    return Capability.objects.create(
        project=project,
        name="extractor",
        slug="extractor",
        description="Extract structured fields from a document.",
        improvement_metadata={
            "capability_card": {
                "task": "Extract structured fields from a document.",
                "output_fields": {"category": {"type": "text"}, "summary": {"type": "text"}},
                "output_schema": {"required_keys": ["category", "summary"]},
            },
            "system_prompt": "You extract category and summary.",
        },
    )


@pytest.fixture(autouse=True)
def _no_llm_authoring(monkeypatch):
    """Tier 1 hits the network; return the triple the merged API unpacks."""
    monkeypatch.setattr(semantic_recommender, "author_tier1_suites", lambda *a, **k: ([], [], None))


@pytest.mark.django_db
def test_preload_authors_card_grounded_graders_and_puts_them_on_the_run_list(grounded_capability):
    """The path onboarding actually runs. Saving an evaluator and adding it to
    the run list are separate jobs, and only the second makes it score."""
    result = generate_and_preload_default_set(grounded_capability)
    assert result["generated"] >= 1

    graders = Evaluator.objects.filter(capability=grounded_capability, is_archived=False)
    assert graders.exists(), "preload produced no graders"
    assert all(ev.capability_id == grounded_capability.id for ev in graders)
    assert any(
        (ev.config or {}).get("provenance", {}).get("generator") == "card_compiler@v1"
        for ev in graders
    )

    members = EvalSetMember.objects.filter(eval_set__capability=grounded_capability)
    assert members.exists(), "graders exist but nothing would run them"


@pytest.mark.django_db
def test_preload_is_additive_rather_than_destructive(grounded_capability):
    """A re-scan must not delete what is already there: an Evaluator delete
    cascades its eval-set members and nulls the provenance on past scores."""
    generate_and_preload_default_set(grounded_capability)
    first = {ev.id for ev in Evaluator.objects.filter(capability=grounded_capability)}
    hand_made = Evaluator.objects.create(
        project=grounded_capability.project,
        capability=grounded_capability,
        name="hand-made-check",
        kind="deterministic",
        config={},
    )

    generate_and_preload_default_set(grounded_capability)

    surviving = {ev.id for ev in Evaluator.objects.filter(capability=grounded_capability)}
    assert first <= surviving
    assert hand_made.id in surviving


@pytest.mark.django_db
def test_a_capability_with_no_card_gets_nothing(db):
    """Both tiers derive from the codebase card, so a cardless capability produces
    no graders at all."""
    project = Project.objects.create(name="nocard", slug="nocard")
    capability = Capability.objects.create(
        project=project, name="bare", slug="bare", improvement_metadata={}
    )

    result = generate_and_preload_default_set(capability)

    assert result["generated"] == 0
    assert not Evaluator.objects.filter(capability=capability).exists()


@pytest.mark.django_db
def test_visible_catalog_includes_generic_and_bespoke_excludes_sentinels():
    project = Project.objects.create(name="centralise", slug="centralise")
    capability = Capability.objects.create(project=project, name="extractor", slug="extractor")

    Evaluator.objects.create(project=project, name="generic-check", kind="deterministic", config={})
    Evaluator.objects.create(
        project=project,
        capability=capability,
        name="output-parses-as-json",
        kind="deterministic",
        config={"provenance": {"generator": "card_compiler@v1"}},
    )
    # Legacy eval-matrix sentinels persisting in deployed DBs: excluded by
    # __-name and by spec_role, whichever a row still carries.
    Evaluator.objects.create(
        project=project,
        capability=capability,
        name="__agent_spec__",
        kind="agentic",
        config={"capability_spec_role": "spec"},
    )
    Evaluator.objects.create(
        project=project,
        capability=capability,
        name="structure-carrier",
        kind="deterministic",
        config={"capability_spec_role": "structure"},
    )

    catalog = Evaluator.objects.filter(project=project).visible_catalog()
    names = {ev.name for ev in catalog}
    assert "generic-check" in names
    assert "output-parses-as-json" in names
    assert "__agent_spec__" not in names
    assert "structure-carrier" not in names
    by_name = {ev.name: ev for ev in catalog}
    assert by_name["generic-check"].capability_id is None
    assert by_name["output-parses-as-json"].capability_id == capability.id
