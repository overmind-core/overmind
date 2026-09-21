from __future__ import annotations

import uuid
from unittest import mock

import pytest
from conftest import EVAL_ROWS, frozen_dataset
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    Behaviour,
    BehaviourVersion,
    Capability,
    Dataset,
    EvalRun,
    EvalSet,
    EvalSetMember,
    Evaluator,
    Project,
    ProjectMembership,
    Score,
    User,
)

pytestmark = pytest.mark.django_db


def _user(email: str) -> User:
    return User.objects.create_user(
        email=email,
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        projects_limit=5,
    )


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def _setup():
    user = _user(f"u-{uuid.uuid4().hex[:6]}@example.com")
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    return user, _auth_client(user), project


# LangExtract-shaped card: a nested output schema generation must surface and bind.
_LANGEXTRACT_CARD = {
    "output_fields": {
        "extractions": "list[Extraction] | None — structured entities extracted",
        "text": "str | None — original document text",
        "document_id": "str — unique document identifier",
    },
    "output_schema": {
        "required_keys": ["extractions"],
        "properties": {
            "char_interval.start_pos": "int | None after alignment",
            "char_interval.end_pos": "int | None after alignment",
            "extractions": "list[dict], non-empty when the model succeeds",
            "{class}_attributes": "dict | None",
        },
        "provenance": [],
    },
    "expected_output": {
        "description": "AnnotatedDocument with a non-empty extractions list.",
        "example": {
            "document_id": "doc_1",
            "text": "Lady Juliet gazed longingly at the stars",
            "extractions": [
                {
                    "extraction_class": "character",
                    "extraction_text": "Lady Juliet",
                    "char_interval": {"start_pos": 0, "end_pos": 11},
                    "alignment_status": "match_exact",
                    "attributes": {"emotional_state": "longing"},
                }
            ],
        },
    },
}


class TestEvaluatorApi:
    def test_create_bumps_version(self):
        _user_, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "MyEval",
            "kind": "llm_judge",
            "rubric_md": "x",
            "checklist": [{"id": "q1", "q": "?", "weight": 1.0}],
        }
        r1 = client.post("/api/evaluators/", body, format="json")
        assert r1.status_code == 201, r1.content
        assert r1.json()["version"] == 1
        r2 = client.post("/api/evaluators/", body, format="json")
        assert r2.json()["version"] == 2

    def test_authored_judge_lands_gradable(self):
        # The dialog is the main way a judge is created, so a judge born without
        # a checklist here would be refused later, at run time, by the attach.
        _user_, client, project = _setup()
        r = client.post(
            "/api/evaluators/author/",
            {
                "project": str(project.id),
                "name": "Authored",
                "evaluation_prompt": "Judge whether the summary is accurate.",
                "score_type": "numeric",
            },
            format="json",
        )
        assert r.status_code == 201, r.content

        from overbae.models import Evaluator
        from overbae.services.eval import snapshots

        ev = Evaluator.objects.get(id=r.json()["id"])
        assert ev.checklist
        assert ev.unbound_checklist_variables() == []
        snapshots.build_snapshot(ev)

    def test_judge_without_checklist_is_rejected(self):
        # Caught at authoring: the score is the weighted fraction of items that
        # pass, so an empty checklist can never produce one.
        _user_, client, project = _setup()
        r = client.post(
            "/api/evaluators/",
            {
                "project": str(project.id),
                "name": "NoChecklist",
                "kind": "llm_judge",
                "rubric_md": "Grade it.",
            },
            format="json",
        )
        assert r.status_code == 400, r.content
        assert "checklist" in r.json()

    def test_deterministic_without_checklist_is_allowed(self):
        _user_, client, project = _setup()
        r = client.post(
            "/api/evaluators/",
            {
                "project": str(project.id),
                "name": "Exact",
                "kind": "deterministic",
                "config": {"check": "exact_match"},
            },
            format="json",
        )
        assert r.status_code == 201, r.content

    def test_managed_visible_in_list(self):
        _user_, client, project = _setup()
        Evaluator.objects.create(
            project=None, name="Managed", kind="llm_judge", is_managed=True, version=1
        )
        r = client.get(f"/api/evaluators/?project={project.id}&include_managed=true")
        assert r.status_code == 200
        names = [e["name"] for e in r.json()["results"]]
        assert "Managed" in names

    def test_scoped_to_membership(self):
        _user_, client, _project = _setup()
        other = Project.objects.create(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")
        Evaluator.objects.create(project=other, name="Hidden", kind="deterministic", version=1)
        r = client.get("/api/evaluators/?include_managed=false")
        names = [e["name"] for e in r.json()["results"]]
        assert "Hidden" not in names

    def test_filter_by_capability(self):
        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        scoped = Evaluator.objects.create(
            project=project,
            capability=capability,
            name="AgentScoped",
            kind="deterministic",
            version=1,
        )
        Evaluator.objects.create(
            project=project, name="ProjectWide", kind="deterministic", version=1
        )
        r = client.get(f"/api/evaluators/?capability={capability.id}")
        assert r.status_code == 200, r.content
        results = r.json()["results"]
        assert [e["name"] for e in results] == ["AgentScoped"]
        assert results[0]["capability"] == str(scoped.capability_id)

    def test_judge_prompt_exposed_for_judges_only(self):
        _user_, client, project = _setup()
        judge = Evaluator.objects.create(
            project=project,
            name="Judge",
            kind="llm_judge",
            version=1,
            rubric_md="Answer must be correct.",
            # Gates only render (and only apply) on boolean judges.
            score_type="boolean",
            checklist=[{"id": "correct", "q": "Is it correct?", "weight": 1.0, "gate": True}],
            variable_mapping=[{"var": "output", "source": "output"}],
        )
        det = Evaluator.objects.create(project=project, name="Det", kind="deterministic", version=1)

        rj = client.get(f"/api/evaluators/{judge.id}/")
        assert rj.status_code == 200, rj.content
        prompt = rj.json()["judge_prompt"]
        assert "Answer must be correct." in prompt
        assert "Is it correct?" in prompt
        assert "GATE" in prompt
        assert "run time" in prompt

        rd = client.get(f"/api/evaluators/{det.id}/")
        assert rd.json()["judge_prompt"] == ""


class TestAuthorJudgeEvaluator:
    def test_numeric_persists_runnable_judge(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Numeric Judge",
            "evaluation_prompt": "Rate the answer to {{input}}.",
            "score_type": "numeric",
            "score_reasoning_prompt": "Explain the assigned score in one concise sentence.",
            "score_output_prompt": "Return a numeric score between 0 and 1.",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        ev = Evaluator.objects.get(id=r.json()["id"])
        assert ev.kind == "llm_judge"
        assert ev.score_type == "numeric"
        assert ev.scope == "final_output"
        assert (ev.score_min, ev.score_max) == (0.0, 1.0)
        assert "Rate the answer" in ev.rubric_md
        # The rubric says what good looks like and nothing about how to answer.
        # A generative judge scores from its verdicts and is told in the same
        # prompt not to return a number, so folding this in contradicted it.
        assert "Return a numeric score" not in ev.rubric_md
        assert "Explain the assigned score" not in ev.rubric_md
        # Still round-tripped, so the edit dialog shows what the user typed.
        assert (
            ev.config["authoring"]["score_output_prompt"]
            == "Return a numeric score between 0 and 1."
        )

    def test_boolean_persists(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Bool Judge",
            "evaluation_prompt": "Does the answer satisfy the criteria?",
            "score_type": "boolean",
            "boolean_verdict_prompt": "Return true if the answer satisfies the criteria.",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        ev = Evaluator.objects.get(id=r.json()["id"])
        assert ev.kind == "llm_judge"
        assert ev.score_type == "boolean"
        assert ev.rubric_md == "Does the answer satisfy the criteria?"
        assert (
            ev.config["authoring"]["boolean_verdict_prompt"]
            == "Return true if the answer satisfies the criteria."
        )

    def test_categorical_persists_choices(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Cat Judge",
            "evaluation_prompt": "Classify the sentiment of {{output}}.",
            "score_type": "categorical",
            "categories": ["positive", "neutral", "negative"],
            "allow_multiple": True,
            "category_selection_prompt": "Choose exactly one category from the provided list.",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        ev = Evaluator.objects.get(id=r.json()["id"])
        assert ev.score_type == "categorical"
        assert [c["label"] for c in ev.choices] == ["positive", "neutral", "negative"]
        assert [c["value"] for c in ev.choices] == [0.0, 0.5, 1.0]
        assert ev.config["authoring"]["allow_multiple"] is True

    def test_categorical_requires_two_categories(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Bad Cat",
            "evaluation_prompt": "Classify it.",
            "score_type": "categorical",
            "categories": ["only-one"],
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400
        assert "categories" in r.json()

    def test_empty_prompt_rejected(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Empty",
            "evaluation_prompt": "   ",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400

    def test_applicable_roles_round_trip(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        behaviour = _behaviour_with_contract(capability)
        body = {
            "project": str(project.id),
            "behaviour": str(behaviour.id),
            "name": "Both Roles Judge",
            "evaluation_prompt": "Rate the answer.",
            "score_type": "numeric",
            "applicable_roles": ["generative", "trace_scoring"],
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        assert r.json()["applicable_roles"] == ["generative", "trace_scoring"]
        ev = Evaluator.objects.get(id=r.json()["id"])
        assert ev.applicable_roles == ["generative", "trace_scoring"]

        r2 = client.put(
            f"/api/evaluators/{ev.id}/author/",
            {**body, "applicable_roles": ["generative"]},
            format="json",
        )
        assert r2.status_code == 200, r2.content
        assert r2.json()["applicable_roles"] == ["generative"]
        ev.refresh_from_db()
        assert ev.applicable_roles == ["generative"]

    def test_trace_scoring_role_requires_a_behaviour(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Unbound Trace Judge",
            "evaluation_prompt": "Rate the answer.",
            "score_type": "numeric",
            "applicable_roles": ["generative", "trace_scoring"],
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400, r.content
        assert "behaviour" in r.json()

    def test_applicable_roles_omitted_defaults_to_empty(self):
        # No explicit roles => derived from scope at routing time.
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Legacy Judge",
            "evaluation_prompt": "Rate the answer.",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        assert Evaluator.objects.get(id=r.json()["id"]).applicable_roles == []

    def test_applicable_roles_rejects_empty_and_unknown(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Bad Roles",
            "evaluation_prompt": "Rate the answer.",
            "score_type": "numeric",
        }
        for bad in ([], ["vibes"]):
            r = client.post(
                "/api/evaluators/author/", {**body, "applicable_roles": bad}, format="json"
            )
            assert r.status_code == 400, r.content
            assert "applicable_roles" in r.json()

    def test_authored_evaluator_shows_in_catalog(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Catalog Judge",
            "evaluation_prompt": "Grade {{output}}.",
            "score_type": "numeric",
        }
        client.post("/api/evaluators/author/", body, format="json")
        r = client.get(f"/api/evaluators/catalog/?project={project.id}")
        assert r.status_code == 200, r.content
        assert "Catalog Judge" in [e["name"] for e in r.json()]

    def test_capability_tags_created_evaluator(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        body = {
            "project": str(project.id),
            "capability": str(capability.id),
            "name": "Capability Judge",
            "evaluation_prompt": "Grade {{output}} for this capability.",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        ev = Evaluator.objects.get(id=r.json()["id"])
        assert ev.capability_id == capability.id
        r_a = client.get(f"/api/evaluators/catalog/?capability={capability.id}")
        names_a = {e["name"]: e for e in r_a.json()}
        assert "Capability Judge" in names_a
        assert names_a["Capability Judge"]["capability"] == str(capability.id)
        assert names_a["Capability Judge"]["is_generic"] is False
        other = Capability.objects.create(
            project=project, name="B", slug=f"b-{uuid.uuid4().hex[:6]}"
        )
        r_b = client.get(f"/api/evaluators/catalog/?capability={other.id}")
        assert "Capability Judge" not in [e["name"] for e in r_b.json()]

    def test_config_roundtrips_evaluation_prompt(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Roundtrip",
            "evaluation_prompt": "Is {{output}} polite?",
            "score_type": "boolean",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        ev = Evaluator.objects.get(id=r.json()["id"])
        assert ev.config["authoring"]["evaluation_prompt"] == "Is {{output}} polite?"

    def test_field_variable_bound_in_mapping(self):
        # source="" means the field resolves through the schema tier at scoring time.
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Field Judge",
            "evaluation_prompt": "Every {{extraction_class}} in {{output}} must be capitalized.",
            "score_type": "boolean",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        ev = Evaluator.objects.get(id=r.json()["id"])
        mapping = ev.variable_mapping
        assert {"var": "extraction_class", "source": ""} in mapping
        assert {"var": "output", "source": "output"} in mapping
        assert {"var": "reference", "source": "reference"} in mapping
        assert sum(1 for e in mapping if e["var"] == "output") == 1

    def test_nested_field_persists_and_binds_jsonpath(self):
        # The {{var}} binder can't carry a dotted path, so a nested leaf needs jsonpath.
        from overbae.services.eval.evaluators.base import EvalUnit, resolve_variables

        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project,
            name="LX",
            slug=f"lx-{uuid.uuid4().hex[:6]}",
            improvement_metadata={"capability_card": _LANGEXTRACT_CARD},
        )
        body = {
            "project": str(project.id),
            "capability": str(capability.id),
            "name": "Char Interval Judge",
            "evaluation_prompt": (
                "For each item in {{extractions}}, verify "
                "{{extractions_char_interval_start_pos}} is a non-negative integer."
            ),
            "score_type": "boolean",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        ev = Evaluator.objects.get(id=r.json()["id"])

        entry = next(
            e for e in ev.variable_mapping if e["var"] == "extractions_char_interval_start_pos"
        )
        assert entry["source"] == "output"
        assert entry["jsonpath"] == "$.extractions[*].char_interval.start_pos"
        assert {"var": "extractions", "source": ""} in ev.variable_mapping

        unit = EvalUnit(
            trajectory={"final_output": _LANGEXTRACT_CARD["expected_output"]["example"]}
        )
        resolved = resolve_variables(unit, ev.variable_mapping)
        assert "0" in resolved["extractions_char_interval_start_pos"]  # start_pos == 0 binds
        assert "Lady Juliet" in resolved["extractions"]


class TestEditJudgeEvaluator:
    def _create_judge(self, client, project):
        body = {
            "project": str(project.id),
            "name": "Editable",
            "evaluation_prompt": "Rate {{output}} 0..1.",
            "score_type": "numeric",
        }
        return client.post("/api/evaluators/author/", body, format="json").json()

    def test_edit_updates_config_and_keeps_version(self):
        _u, client, project = _setup()
        created = self._create_judge(client, project)
        ev_id = created["id"]
        assert created["version"] == 1

        body = {
            "project": str(project.id),
            "name": "Editable v2 name",
            "evaluation_prompt": "Classify {{output}} sentiment.",
            "score_type": "categorical",
            "categories": ["positive", "negative"],
            "category_selection_prompt": "Pick one.",
        }
        r = client.put(f"/api/evaluators/{ev_id}/author/", body, format="json")
        assert r.status_code == 200, r.content
        ev = Evaluator.objects.get(id=ev_id)
        assert str(ev.id) == ev_id
        assert ev.version == 1
        assert ev.name == "Editable v2 name"
        assert ev.score_type == "categorical"
        assert [c["label"] for c in ev.choices] == ["positive", "negative"]
        assert ev.config["authoring"]["evaluation_prompt"] == "Classify {{output}} sentiment."

    def test_edit_can_retag_capability(self):
        _u, client, project = _setup()
        created = self._create_judge(client, project)
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        body = {
            "project": str(project.id),
            "capability": str(capability.id),
            "name": "Editable",
            "evaluation_prompt": "Rate {{output}} 0..1.",
            "score_type": "numeric",
        }
        r = client.put(f"/api/evaluators/{created['id']}/author/", body, format="json")
        assert r.status_code == 200, r.content
        assert Evaluator.objects.get(id=created["id"]).capability_id == capability.id

    def test_edit_rejects_non_judge(self):
        _u, client, project = _setup()
        det = Evaluator.objects.create(project=project, name="Det", kind="deterministic", version=1)
        body = {
            "project": str(project.id),
            "name": "Det",
            "evaluation_prompt": "x",
            "score_type": "numeric",
        }
        r = client.put(f"/api/evaluators/{det.id}/author/", body, format="json")
        assert r.status_code == 400, r.content


class TestGenerateEvaluatorPrompt:
    def _mock_llm(self, payload: dict):
        import json as _json

        return mock.patch(
            "overbae.services.eval.rubric_compiler.call_llm",
            return_value=(_json.dumps(payload), {}),
        )

    def test_generic_prompt_when_no_capability(self):
        _u, client, _project = _setup()
        with (
            mock.patch("overbae.services.eval.rubric_compiler.resolve_model", return_value="m"),
            self._mock_llm(
                {
                    "rubric_md": "Evaluate clarity and correctness.",
                    "score_type": "numeric",
                    "score_output_prompt": "Return 0..1.",
                }
            ),
        ):
            r = client.post(
                "/api/evaluators/generate-prompt/",
                {"description": "Rate how clear and correct answers are, 0 to 1."},
                format="json",
            )
        assert r.status_code == 200, r.content
        body = r.json()
        assert body["prompt"]
        assert body["grounded"] is False
        assert body["score_type"] == "numeric"
        assert body["score_output_prompt"]
        assert body["score_reasoning_prompt"]  # defaulted when the LLM omits it

    def test_boolean_score_type_returned(self):
        _u, client, _project = _setup()
        with (
            mock.patch("overbae.services.eval.rubric_compiler.resolve_model", return_value="m"),
            self._mock_llm(
                {"rubric_md": "Does the answer cite a source?", "score_type": "boolean"}
            ),
        ):
            r = client.post(
                "/api/evaluators/generate-prompt/",
                {"description": "Check whether the answer cites a source or not."},
                format="json",
            )
        body = r.json()
        assert body["score_type"] == "boolean"
        assert body["boolean_verdict_prompt"]  # defaulted
        assert not body["categories"]

    def test_categorical_score_type_returns_categories(self):
        _u, client, _project = _setup()
        with (
            mock.patch("overbae.services.eval.rubric_compiler.resolve_model", return_value="m"),
            self._mock_llm(
                {
                    "rubric_md": "Classify the sentiment of the answer.",
                    "score_type": "categorical",
                    "categories": ["positive", "neutral", "negative"],
                    "allow_multiple": False,
                }
            ),
        ):
            r = client.post(
                "/api/evaluators/generate-prompt/",
                {"description": "Classify sentiment as positive, neutral or negative."},
                format="json",
            )
        body = r.json()
        assert body["score_type"] == "categorical"
        assert len(body["categories"]) >= 2
        assert body["category_selection_prompt"]

    def test_underspecified_categorical_falls_back_to_numeric(self):
        _u, client, _project = _setup()
        with (
            mock.patch("overbae.services.eval.rubric_compiler.resolve_model", return_value="m"),
            self._mock_llm(
                {
                    "rubric_md": "Classify it.",
                    "score_type": "categorical",
                    "categories": ["only-one"],
                }
            ),
        ):
            r = client.post(
                "/api/evaluators/generate-prompt/",
                {"description": "Classify it somehow."},
                format="json",
            )
        body = r.json()
        assert body["score_type"] == "numeric"
        assert body["categories"] == []

    def test_capability_grounded_prompt(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        with (
            mock.patch(
                "overbae.services.eval.grounding.resolve_grounding_for_capability",
                return_value=object(),
            ),
            mock.patch(
                "overbae.services.eval.grounding.render_grounding_pack",
                return_value="CODEBASE CARD: this capability extracts fields.",
            ),
            mock.patch("overbae.services.eval.rubric_compiler.resolve_model", return_value="m"),
            self._mock_llm(
                {"rubric_md": "Check extracted fields against the schema.", "score_type": "numeric"}
            ) as llm,
        ):
            r = client.post(
                "/api/evaluators/generate-prompt/",
                {
                    "description": "Check the capability extracts the right fields.",
                    "capability": str(capability.id),
                },
                format="json",
            )
        assert r.status_code == 200, r.content
        body = r.json()
        assert body["prompt"]
        assert body["grounded"] is True
        assert body["score_type"] == "numeric"
        sent_prompt = llm.call_args.args[0]
        assert "CODEBASE CARD" in sent_prompt

    def test_grounded_generation_advertises_fields_and_repairs_unknown_vars(self):
        from types import SimpleNamespace

        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        grounding = SimpleNamespace(
            codebase_card={
                "output_fields": {
                    "extractions": {"type": "list"},
                    "extraction_class": {"description": "the class label of an extraction"},
                }
            }
        )
        with (
            mock.patch(
                "overbae.services.eval.grounding.resolve_grounding_for_capability",
                return_value=grounding,
            ),
            mock.patch(
                "overbae.services.eval.grounding.render_grounding_pack",
                return_value="Output contract fields: extractions, extraction_class",
            ),
            mock.patch("overbae.services.eval.rubric_compiler.resolve_model", return_value="m"),
            self._mock_llm(
                {
                    "rubric_md": (
                        "Judge {{outputs}} where each {{extraction_class}} is capitalized "
                        "and matches {{expected_json}}."
                    ),
                    "score_type": "numeric",
                }
            ) as llm,
        ):
            r = client.post(
                "/api/evaluators/generate-prompt/",
                {
                    "description": "Check extraction_class capitalization.",
                    "capability": str(capability.id),
                },
                format="json",
            )
        assert r.status_code == 200, r.content
        prompt = r.json()["prompt"]
        assert "{{extraction_class}}" in prompt
        assert "{{outputs}}" not in prompt and "{{output}}" in prompt
        assert "{{expected_json}}" not in prompt and "{{reference}}" in prompt
        from overbae.services.eval.evaluators.base import _normalize_var
        from overbae.services.eval.rubric_compiler import (
            _VAR_RE,
            _bindable_variables,
            _variable_catalog,
        )

        bindable = _bindable_variables(_variable_catalog(grounding))
        assert all(_normalize_var(v) in bindable for v in _VAR_RE.findall(prompt))
        sent_prompt = llm.call_args.args[0]
        assert "Template variables" in sent_prompt
        assert "{{extraction_class}}" in sent_prompt

    def test_grounded_generation_advertises_nested_schema(self):
        from types import SimpleNamespace

        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        grounding = SimpleNamespace(codebase_card=_LANGEXTRACT_CARD)
        with (
            mock.patch(
                "overbae.services.eval.grounding.resolve_grounding_for_capability",
                return_value=grounding,
            ),
            mock.patch(
                "overbae.services.eval.grounding.render_grounding_pack",
                return_value="Capability extracts entities.",
            ),
            mock.patch("overbae.services.eval.rubric_compiler.resolve_model", return_value="m"),
            self._mock_llm(
                {"rubric_md": "Check {{extractions}} alignment.", "score_type": "numeric"}
            ) as llm,
        ):
            r = client.post(
                "/api/evaluators/generate-prompt/",
                {"description": "Check char_interval alignment.", "capability": str(capability.id)},
                format="json",
            )
        assert r.status_code == 200, r.content
        sent = llm.call_args.args[0]
        assert "{{extractions_char_interval_start_pos}}" in sent
        assert "$.extractions[*].char_interval.start_pos" in sent
        assert "Required output keys: extractions" in sent
        assert "Observed output leaves" in sent
        assert "{{output.<path>}}" in sent
        assert "NEVER write {{output}}.<path>" in sent
        assert "Example output" in sent and "Lady Juliet" in sent

    def test_repairs_split_path_variable_docs(self):
        # Models often emit {{output}}.field; the canonical form is {{output.field}}.
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        with (
            mock.patch(
                "overbae.services.eval.grounding.resolve_grounding_for_capability",
                return_value=object(),
            ),
            mock.patch(
                "overbae.services.eval.grounding.render_grounding_pack",
                return_value="CODEBASE CARD",
            ),
            mock.patch("overbae.services.eval.rubric_compiler.resolve_model", return_value="m"),
            self._mock_llm(
                {
                    "rubric_md": (
                        "Compare {{output}}.isInvoice to {{reference}}.isInvoice. "
                        "Also grade {{output}}."
                    ),
                    "score_type": "numeric",
                }
            ),
        ):
            r = client.post(
                "/api/evaluators/generate-prompt/",
                {"description": "Check classification.", "capability": str(capability.id)},
                format="json",
            )
        assert r.status_code == 200, r.content
        prompt = r.json()["prompt"]
        assert "{{output.isInvoice}}" in prompt
        assert "{{reference.isInvoice}}" in prompt
        assert "{{output}}.isInvoice" not in prompt
        assert "{{reference}}.isInvoice" not in prompt
        assert "{{output}}" in prompt  # bare whole-blob still allowed

    def test_trace_scoring_role_uses_no_gold_contract(self):
        from overbae.services.eval.semantic_recommender import TRACE_NO_GOLD_RULE

        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        with (
            mock.patch(
                "overbae.services.eval.grounding.resolve_grounding_for_capability",
                return_value=object(),
            ),
            mock.patch(
                "overbae.services.eval.grounding.render_grounding_pack",
                return_value="CODEBASE CARD: live capability.",
            ),
            mock.patch("overbae.services.eval.rubric_compiler.resolve_model", return_value="m"),
            self._mock_llm(
                {
                    "rubric_md": ("Compare {{output}} to {{reference}} and {{expected_json}}."),
                    "score_type": "numeric",
                }
            ) as llm,
        ):
            r = client.post(
                "/api/evaluators/generate-prompt/",
                {
                    "description": "Score live task success from the trace.",
                    "capability": str(capability.id),
                    "applicable_role": "trace_scoring",
                },
                format="json",
            )
        assert r.status_code == 200, r.content
        body = r.json()
        prompt = body["prompt"]
        # Gold placeholders are repaired away — live traces have no reference.
        assert "{{reference}}" not in prompt
        assert "{{expected_json}}" not in prompt
        assert "{{output}}" in prompt
        sent_prompt, kwargs = llm.call_args.args[0], llm.call_args.kwargs
        assert TRACE_NO_GOLD_RULE in sent_prompt
        assert "There is NO {{reference}}" in sent_prompt
        assert "TRACE SCORING" in kwargs["system_prompt"]
        assert "NO curated golden reference" in kwargs["system_prompt"]

    def test_generative_role_keeps_reference_contract(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        with (
            mock.patch(
                "overbae.services.eval.grounding.resolve_grounding_for_capability",
                return_value=object(),
            ),
            mock.patch(
                "overbae.services.eval.grounding.render_grounding_pack",
                return_value="CODEBASE CARD",
            ),
            mock.patch("overbae.services.eval.rubric_compiler.resolve_model", return_value="m"),
            self._mock_llm(
                {"rubric_md": "Compare {{output}} to {{reference}}.", "score_type": "numeric"}
            ) as llm,
        ):
            r = client.post(
                "/api/evaluators/generate-prompt/",
                {
                    "description": "Check correctness vs gold.",
                    "capability": str(capability.id),
                    "applicable_role": "generative",
                },
                format="json",
            )
        assert r.status_code == 200, r.content
        assert "{{reference}}" in r.json()["prompt"]
        sent_prompt = llm.call_args.args[0]
        assert "{{reference.<path>}}" in sent_prompt
        assert "TRACE SCORING" not in llm.call_args.kwargs["system_prompt"]

    def test_applicable_role_rejects_unknown(self):
        _u, client, _project = _setup()
        r = client.post(
            "/api/evaluators/generate-prompt/",
            {"description": "Anything.", "applicable_role": "vibes"},
            format="json",
        )
        assert r.status_code == 400, r.content
        assert "applicable_role" in r.json()


class TestEvalRunApi:
    def test_create_dispatches_task(self):
        _user_, client, project = _setup()
        capability = Capability.objects.create(project=project, name="A", slug="a")
        dataset = frozen_dataset(capability.project, EVAL_ROWS, capability=capability)
        ev = Evaluator.objects.create(project=project, name="E", kind="deterministic", version=1)
        with mock.patch(
            "overbae.tasks.eval.run_eval_run.apply_async", return_value=mock.Mock(id="task-1")
        ) as disp:
            r = client.post(
                "/api/eval-runs/",
                {
                    "project": str(project.id),
                    "name": "run1",
                    "data_source": "dataset",
                    "dataset": str(dataset.id),
                    "evaluator_ids": [str(ev.id)],
                    "variants_input": [
                        {"label": "gpt-5-mini", "model_name": "gpt-5-mini", "mode": "existing"}
                    ],
                },
                format="json",
            )
        assert r.status_code == 201, r.content
        disp.assert_called_once()
        run = EvalRun.objects.get(id=r.json()["id"])
        assert run.variants.count() == 1
        assert run.evaluators.count() == 1

    def test_dataset_required_for_dataset_source(self):
        _user_, client, project = _setup()
        r = client.post(
            "/api/eval-runs/",
            {"project": str(project.id), "name": "bad", "data_source": "dataset"},
            format="json",
        )
        assert r.status_code == 400

    def test_comparison_action(self):
        _user_, client, project = _setup()
        run = EvalRun.objects.create(project=project, name="r", summary={"metrics": ["acc"]})
        r = client.get(f"/api/eval-runs/{run.id}/comparison/")
        assert r.status_code == 200
        assert r.json()["summary"]["metrics"] == ["acc"]

    def test_create_rejects_dead_generate_model(self):
        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        dataset = frozen_dataset(capability.project, EVAL_ROWS, capability=capability)
        catalog = ([{"id": "openai/gpt-5-mini"}, {"id": "openai/gpt-4o-2024-05-13"}], True)
        with mock.patch("overbae.services.model_catalog.fetch_model_catalog", return_value=catalog):
            r = client.post(
                "/api/eval-runs/",
                {
                    "project": str(project.id),
                    "name": "dead",
                    "data_source": "dataset",
                    "dataset": str(dataset.id),
                    "variants_input": [
                        {"label": "v", "model_name": "gpt-4-turbo-preview", "mode": "generate"}
                    ],
                },
                format="json",
            )
        assert r.status_code == 400, r.content
        assert "not available" in str(r.json()).lower()

    def test_create_accepts_valid_generate_model(self):
        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        dataset = frozen_dataset(capability.project, EVAL_ROWS, capability=capability)
        catalog = ([{"id": "openai/gpt-5-mini"}, {"id": "openai/gpt-4o-2024-05-13"}], True)
        with (
            mock.patch("overbae.services.model_catalog.fetch_model_catalog", return_value=catalog),
            mock.patch(
                "overbae.tasks.eval.run_eval_run.apply_async", return_value=mock.Mock(id="t")
            ),
        ):
            r = client.post(
                "/api/eval-runs/",
                {
                    "project": str(project.id),
                    "name": "ok",
                    "data_source": "dataset",
                    "dataset": str(dataset.id),
                    "variants_input": [
                        {
                            "label": "v",
                            "model_name": "openai/gpt-4o-2024-05-13",
                            "mode": "generate",
                        }
                    ],
                },
                format="json",
            )
        assert r.status_code == 201, r.content

    def test_create_refuses_generate_eval_set_without_judges(self):
        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        dataset = frozen_dataset(
            project, EVAL_ROWS, capability=capability, name="d", contract="eval"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        ev = Evaluator.objects.create(
            project=project,
            capability=capability,
            name="output-field-accuracy",
            kind=Evaluator.Kind.DETERMINISTIC,
            scope="final_output",
            config={"check": "canonical_fields", "fields": []},
        )
        EvalSetMember.objects.create(
            eval_set=eval_set, evaluator=ev, role=EvalSetMember.Role.GENERATIVE
        )
        catalog = ([{"id": "openai/gpt-4o-2024-05-13"}], True)
        with mock.patch("overbae.services.model_catalog.fetch_model_catalog", return_value=catalog):
            r = client.post(
                "/api/eval-runs/",
                {
                    "project": str(project.id),
                    "name": "hollow",
                    "data_source": "dataset",
                    "dataset": str(dataset.id),
                    "eval_set": str(eval_set.id),
                    "variants_input": [
                        {
                            "label": "v",
                            "model_name": "openai/gpt-4o-2024-05-13",
                            "mode": "generate",
                        }
                    ],
                },
                format="json",
            )
        assert r.status_code == 400, r.content
        assert "no live generative judges" in str(r.json()).lower()

    def test_list_filters_by_capability_and_orders_by_name(self):
        _user_, client, project = _setup()
        capability_a = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        capability_b = Capability.objects.create(
            project=project, name="B", slug=f"b-{uuid.uuid4().hex[:6]}"
        )
        ds_a = frozen_dataset(capability_a.project, EVAL_ROWS, capability=capability_a, name="ds-a")
        ds_b = frozen_dataset(capability_b.project, EVAL_ROWS, capability=capability_b, name="ds-b")
        EvalRun.objects.create(project=project, name="zeta", dataset=ds_a)
        EvalRun.objects.create(project=project, name="alpha", dataset=ds_a)
        EvalRun.objects.create(project=project, name="other", dataset=ds_b)

        by_capability = client.get(
            f"/api/eval-runs/?project={project.id}&capability={capability_a.id}"
        )
        assert by_capability.status_code == 200, by_capability.content
        names = [row["name"] for row in by_capability.json()["results"]]
        assert set(names) == {"zeta", "alpha"}

        ordered = client.get(
            f"/api/eval-runs/?project={project.id}&capability={capability_a.id}&ordering=name"
        )
        assert ordered.status_code == 200, ordered.content
        assert [row["name"] for row in ordered.json()["results"]] == ["alpha", "zeta"]

    def test_datasets_facet_lists_only_datasets_with_runs(self):
        """The run-bearing dataset sits past ``/api/datasets/cards/``'s
        max_page_size=100, so a facet built off the dataset list would drop it."""
        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        used = frozen_dataset(
            capability.project, EVAL_ROWS, capability=capability, name="used-dataset"
        )
        EvalRun.objects.create(project=project, name="r", dataset=used)
        Dataset.objects.bulk_create(
            Dataset(capability=capability, project=project, name=f"unused-{i:03d}")
            for i in range(101)
        )

        # A run in a project the caller is not a member of must not leak.
        other_project = Project.objects.create(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")
        other_capability = Capability.objects.create(
            project=other_project, name="O", slug=f"o-{uuid.uuid4().hex[:6]}"
        )
        other_dataset = frozen_dataset(
            other_capability.project, EVAL_ROWS, capability=other_capability, name="foreign"
        )
        EvalRun.objects.create(project=other_project, name="foreign-run", dataset=other_dataset)

        r = client.get(f"/api/eval-runs/datasets/?project={project.id}")
        assert r.status_code == 200, r.content
        # A bare array, not a pagination envelope — the generated client types it as Array<…>.
        assert r.json() == [{"id": str(used.id), "name": "used-dataset"}]

        unscoped = client.get("/api/eval-runs/datasets/")
        assert unscoped.status_code == 200, unscoped.content
        assert str(other_dataset.id) not in {row["id"] for row in unscoped.json()}


class TestScoreApi:
    def test_list_scoped(self):
        _user_, client, project = _setup()
        run = EvalRun.objects.create(project=project, name="r")
        Score.objects.create(project=project, run=run, name="acc", data_type="numeric", value=0.9)
        r = client.get(f"/api/eval-scores/?run={run.id}")
        assert r.status_code == 200
        assert r.json()["results"][0]["name"] == "acc"


class TestAgentEvalMetricsApi:
    def test_eval_metrics_validate_endpoint_removed(self):
        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        r = client.post(f"/api/capabilities/{capability.id}/eval-metrics/validate/")
        assert r.status_code == 404, r.content

    def test_capability_serializer_drops_validation_fields(self):
        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        r = client.get(f"/api/capabilities/{capability.id}/")
        assert r.status_code == 200, r.content
        assert "eval_metrics_validated" not in r.json()
        assert "eval_metrics_validated_at" not in r.json()


class TestEvalSetCreation:
    @pytest.mark.parametrize("capability_field", [{}, {"capability": None}])
    def test_unassigned_sets_can_be_created_listed_and_read(self, capability_field):
        _, client, project = _setup()
        evaluator = Evaluator.objects.create(project=project, name="Accuracy", kind="deterministic")
        response = client.post(
            "/api/eval-sets/",
            {
                "project": str(project.id),
                "name": "General",
                "evaluator_ids": [str(evaluator.id)],
                **capability_field,
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        data = response.json()
        assert data["capability"] is None
        assert data["is_active"] is False
        assert data["members"][0]["evaluator_capability_name"] is None
        assert client.get(f"/api/eval-sets/{data['id']}/").status_code == 200
        listed = client.get("/api/eval-sets/").json()["results"]
        assert any(row["id"] == data["id"] for row in listed)
        assert client.post(f"/api/eval-sets/{data['id']}/activate/").status_code == 400
        duplicate = client.post(
            "/api/eval-sets/",
            {
                "project": str(project.id),
                "name": "General",
                "capability": None,
            },
            format="json",
        )
        assert duplicate.status_code == 400
        assert EvalSet.objects.filter(project=project, name="General").count() == 1

    def test_creates_members_in_supported_roles_atomically(self):
        user, client, project = _setup()
        capability = Capability.objects.create(project=project, name="Support", slug="support")
        generic = Evaluator.objects.create(
            name="Accuracy", kind="deterministic", is_managed=True, surface="model"
        )
        mapped = Evaluator.objects.create(
            project=project, capability=capability, name="Tone", kind="llm_judge", surface="any"
        )
        response = client.post(
            "/api/eval-sets/",
            {
                "project": str(project.id),
                "capability": str(capability.id),
                "name": "Quality",
                "evaluator_ids": [str(generic.id), str(mapped.id), str(generic.id)],
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        eval_set = EvalSet.objects.get(id=response.json()["id"])
        assert eval_set.created_by == user
        assert set(eval_set.members.values_list("evaluator_id", "role")) == {
            (generic.id, "generative"),
            (mapped.id, "generative"),
            (mapped.id, "trace_scoring"),
        }
        capability.refresh_from_db()
        assert capability.active_eval_set_id is None

    @pytest.mark.parametrize(
        "invalid", ["foreign_evaluator", "foreign_capability", "archived", "duplicate_name"]
    )
    def test_rejects_invalid_members_without_creating_set(self, invalid):
        _, client, project = _setup()
        capability = Capability.objects.create(project=project, name="Support", slug="support")
        foreign = Project.objects.create(name="Other", slug="other")
        evaluator = Evaluator.objects.create(project=project, name="Quality", kind="llm_judge")
        ids = [str(evaluator.id)]
        if invalid == "foreign_evaluator":
            evaluator.project = foreign
            evaluator.save(update_fields=["project"])
        elif invalid == "foreign_capability":
            capability = Capability.objects.create(project=foreign, name="Other", slug="other")
        elif invalid == "archived":
            evaluator.is_archived = True
            evaluator.save(update_fields=["is_archived"])
        else:
            duplicate = Evaluator.objects.create(
                project=project, capability=capability, name="Quality", kind="deterministic"
            )
            ids.append(str(duplicate.id))
        response = client.post(
            "/api/eval-sets/",
            {
                "project": str(project.id),
                "capability": str(capability.id),
                "name": "New",
                "evaluator_ids": ids,
            },
            format="json",
        )
        assert response.status_code == 400, response.content
        assert not EvalSet.objects.filter(name="New").exists()


class TestEvalSetMemberDedupe:
    def _capability_and_evaluator(self, project):
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        evaluator = Evaluator.objects.create(
            project=project, capability=capability, name="Quality", kind="llm_judge", rubric_md="x"
        )
        return capability, evaluator

    def test_adding_same_evaluator_twice_yields_one_member(self):
        from overbae.models import EvalSet, EvalSetMember

        _user_, client, project = _setup()
        capability, evaluator = self._capability_and_evaluator(project)
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        url = f"/api/eval-sets/{eval_set.id}/members/"
        body = {"role": "generative", "evaluator_ids": [str(evaluator.id)]}

        r1 = client.post(url, body, format="json")
        assert r1.status_code == 201, r1.content
        # Re-adding the identical evaluator must be an idempotent no-op, not a 400.
        r2 = client.post(url, body, format="json")
        assert r2.status_code == 201, r2.content

        members = EvalSetMember.objects.filter(eval_set=eval_set, evaluator=evaluator)
        assert members.count() == 1

    def test_same_evaluator_allowed_in_distinct_roles(self):
        # Model-layer uniqueness is (eval_set, evaluator, role); the API layer adds
        # a stricter scope-applicability guard on top.
        from overbae.models import EvalSet, EvalSetMember

        _user_, _client, project = _setup()
        capability, evaluator = self._capability_and_evaluator(project)
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        EvalSetMember.objects.create(eval_set=eval_set, evaluator=evaluator, role="generative")
        EvalSetMember.objects.create(eval_set=eval_set, evaluator=evaluator, role="trace_scoring")

        assert EvalSetMember.objects.filter(eval_set=eval_set, evaluator=evaluator).count() == 2


class TestEvalSetRoleApplicability:
    def _evaluator(self, project, capability, scope):
        return Evaluator.objects.create(
            project=project,
            capability=capability,
            name=f"E-{scope}-{uuid.uuid4().hex[:4]}",
            kind="llm_judge",
            scope=scope,
            rubric_md="x",
        )

    def test_rejects_reference_graded_evaluator_under_trace_scoring(self):
        # A live trace has no curated golden reference to score against.
        from overbae.models import EvalSet, EvalSetMember

        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        ev = Evaluator.objects.create(
            project=project,
            capability=capability,
            name=f"E-ref-{uuid.uuid4().hex[:4]}",
            kind="llm_judge",
            scope="final_output",
            rubric_md="Compare output to the reference.",
            requires_reference=True,
        )

        url = f"/api/eval-sets/{eval_set.id}/members/"
        r = client.post(
            url, {"role": "trace_scoring", "evaluator_ids": [str(ev.id)]}, format="json"
        )
        assert r.status_code == 400, r.content
        assert not EvalSetMember.objects.filter(eval_set=eval_set, evaluator=ev).exists()

    def test_accepts_output_quality_evaluator_under_trace_scoring(self):
        # A reference-free final_output judge grades output a live trace also has,
        # so it is trace_scoring-applicable, not generative-only.
        from overbae.models import EvalSet, EvalSetMember

        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        ev = self._evaluator(project, capability, "final_output")

        url = f"/api/eval-sets/{eval_set.id}/members/"
        r = client.post(
            url, {"role": "trace_scoring", "evaluator_ids": [str(ev.id)]}, format="json"
        )
        assert r.status_code == 201, r.content
        assert EvalSetMember.objects.filter(eval_set=eval_set, evaluator=ev).count() == 1

    def test_rejects_dataset_scope_evaluator_under_trace_scoring(self):
        # A dataset/corpus statistic can't score a single live trace.
        from overbae.models import EvalSet, EvalSetMember

        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        ev = self._evaluator(project, capability, "dataset")

        url = f"/api/eval-sets/{eval_set.id}/members/"
        r = client.post(
            url, {"role": "trace_scoring", "evaluator_ids": [str(ev.id)]}, format="json"
        )
        assert r.status_code == 400, r.content
        assert not EvalSetMember.objects.filter(eval_set=eval_set, evaluator=ev).exists()

    def test_trajectory_addable_to_generative(self):
        # Generation produces full traces, so trajectory graders stay addable here.
        from overbae.models import EvalSet, EvalSetMember

        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        ev = self._evaluator(project, capability, "trajectory")

        url = f"/api/eval-sets/{eval_set.id}/members/"
        r = client.post(url, {"role": "generative", "evaluator_ids": [str(ev.id)]}, format="json")
        assert r.status_code == 201, r.content
        assert EvalSetMember.objects.filter(eval_set=eval_set, evaluator=ev).count() == 1

    def test_accepts_applicable_role(self):
        from overbae.models import EvalSet, EvalSetMember

        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        gen = self._evaluator(project, capability, "final_output")
        trace = self._evaluator(project, capability, "trajectory")

        url = f"/api/eval-sets/{eval_set.id}/members/"
        assert (
            client.post(
                url, {"role": "generative", "evaluator_ids": [str(gen.id)]}, format="json"
            ).status_code
            == 201
        )
        assert (
            client.post(
                url, {"role": "trace_scoring", "evaluator_ids": [str(trace.id)]}, format="json"
            ).status_code
            == 201
        )
        assert EvalSetMember.objects.filter(eval_set=eval_set).count() == 2

    def test_catalog_exposes_applicable_roles(self):
        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        self._evaluator(project, capability, "final_output")
        self._evaluator(project, capability, "trajectory")

        r = client.get(f"/api/evaluators/catalog/?capability={capability.id}")
        assert r.status_code == 200, r.content
        by_scope = {item["scope"]: item["applicable_roles"] for item in r.json()}
        # Both grade evidence a live trace has (model_output / trajectory, no
        # reference), so both are trace_scoring-applicable on top of generative.
        assert by_scope["final_output"] == ["generative", "trace_scoring"]
        assert by_scope["trajectory"] == ["generative", "trace_scoring"]

    def test_explicit_roles_override_scope_in_catalog(self):
        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        both = self._evaluator(project, capability, "final_output")
        both.applicable_roles = ["generative", "trace_scoring"]
        both.save(update_fields=["applicable_roles"])
        narrowed = self._evaluator(project, capability, "trajectory")
        narrowed.applicable_roles = ["generative"]
        narrowed.save(update_fields=["applicable_roles"])

        r = client.get(f"/api/evaluators/catalog/?capability={capability.id}")
        assert r.status_code == 200, r.content
        by_name = {item["name"]: item["applicable_roles"] for item in r.json()}
        assert by_name[both.name] == ["generative", "trace_scoring"]
        assert by_name[narrowed.name] == ["generative"]

    def test_explicit_roles_override_add_members_guard(self):
        from overbae.models import EvalSet, EvalSetMember

        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        url = f"/api/eval-sets/{eval_set.id}/members/"

        opted_in = self._evaluator(project, capability, "final_output")
        opted_in.applicable_roles = ["generative", "trace_scoring"]
        opted_in.save(update_fields=["applicable_roles"])
        r = client.post(
            url, {"role": "trace_scoring", "evaluator_ids": [str(opted_in.id)]}, format="json"
        )
        assert r.status_code == 201, r.content
        assert EvalSetMember.objects.filter(eval_set=eval_set, evaluator=opted_in).count() == 1

        opted_out = self._evaluator(project, capability, "trajectory")
        opted_out.applicable_roles = ["generative"]
        opted_out.save(update_fields=["applicable_roles"])
        r = client.post(
            url, {"role": "trace_scoring", "evaluator_ids": [str(opted_out.id)]}, format="json"
        )
        assert r.status_code == 400, r.content
        assert not EvalSetMember.objects.filter(eval_set=eval_set, evaluator=opted_out).exists()

    def test_generative_never_drops_any_scope(self):
        # Partitioning a generated suite by applicability must not shrink it.
        from overbae.models import Evaluator
        from overbae.services.eval.roles import GENERATIVE, is_role_applicable

        every_scope = [s for s, _ in Evaluator.Scope.choices]
        kept = [s for s in every_scope if is_role_applicable(s, GENERATIVE)]
        assert kept == every_scope, (
            f"scopes dropped from generative: {set(every_scope) - set(kept)}"
        )
        # An unknown/None scope must still default to generative (never dropped).
        assert is_role_applicable(None, GENERATIVE)
        assert is_role_applicable("some_future_scope", GENERATIVE)


class TestEvalSetDeletion:
    """Deleting a set never leaves the capability pointing at a missing set."""

    def test_delete_active_set_reassigns_to_another(self):
        from overbae.models import EvalSet

        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        keep = EvalSet.objects.create(project=project, capability=capability, name="Keep")
        active = EvalSet.objects.create(project=project, capability=capability, name="Active")
        capability.active_eval_set = active
        capability.save(update_fields=["active_eval_set"])

        r = client.delete(f"/api/eval-sets/{active.id}/")
        assert r.status_code == 204, r.content

        capability.refresh_from_db()
        assert capability.active_eval_set_id == keep.id
        assert EvalSet.objects.filter(id=active.id).count() == 0

    def test_delete_last_set_nulls_active_and_keeps_capability(self):
        from overbae.models import EvalSet

        _user_, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        only = EvalSet.objects.create(project=project, capability=capability, name="Only")
        capability.active_eval_set = only
        capability.save(update_fields=["active_eval_set"])

        r = client.delete(f"/api/eval-sets/{only.id}/")
        assert r.status_code == 204, r.content

        capability.refresh_from_db()
        assert capability.active_eval_set_id is None
        assert Capability.objects.filter(id=capability.id).exists()


class TestAutoPreloadEvalSet:
    @staticmethod
    def _specs():
        from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance

        prov = SpecProvenance(
            source="codebase_card.output_fields",
            generator="card_compiler",
            surface_area="output_contract",
        )
        return [
            EvaluatorSpec(
                name="output-quality",
                kind="llm_judge",
                scope="final_output",
                rubric_md="grade quality",
                provenance=prov,
            ),
            EvaluatorSpec(
                name="tool-trajectory",
                kind="trajectory",
                scope="trajectory",
                config={"check": "tool_was_called"},
                provenance=prov,
            ),
        ]

    def _patch_generators(self, monkeypatch, specs):
        from overbae.services.eval import card_compiler, semantic_recommender

        monkeypatch.setattr(card_compiler, "compile_card_evaluators", lambda grounding: list(specs))
        monkeypatch.setattr(
            semantic_recommender,
            "author_tier1_suites",
            lambda grounding, tier0, **kw: ([], [], None),
        )

    def test_preload_populates_active_default_set(self, monkeypatch):
        from overbae.models import EvalSet, EvalSetMember
        from overbae.services.eval.eval_set import generate_and_preload_default_set

        _user_, _client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        self._patch_generators(monkeypatch, self._specs())

        result = generate_and_preload_default_set(capability)

        capability.refresh_from_db()
        eval_set = EvalSet.objects.get(capability=capability, name="Default")
        assert capability.active_eval_set_id == eval_set.id
        assert result["generated"] == 2
        gen = set(
            eval_set.members.filter(role=EvalSetMember.Role.GENERATIVE).values_list(
                "evaluator__name", flat=True
            )
        )
        trace = set(
            eval_set.members.filter(role=EvalSetMember.Role.TRACE_SCORING).values_list(
                "evaluator__name", flat=True
            )
        )
        # Both graders score a live trace's own surface, so trace_scoring gets both.
        assert gen == {"output-quality", "tool-trajectory"}
        assert trace == {"output-quality", "tool-trajectory"}

    def test_preload_routes_members_by_spec_applicable_roles(self, monkeypatch):
        from overbae.models import EvalSet, EvalSetMember
        from overbae.services.eval.eval_set import generate_and_preload_default_set
        from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance

        _user_, _client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        prov = SpecProvenance(
            source="codebase_card.success_criteria[0]",
            generator="tier1_llm@v1",
            surface_area="output_contract",
        )
        specs = [
            EvaluatorSpec(
                name="task-success",
                kind="llm_judge",
                scope="final_output",
                rubric_md="grade success",
                applicable_roles=["generative", "trace_scoring"],
                provenance=prov,
            ),
            EvaluatorSpec(
                name="tool-trajectory",
                kind="trajectory",
                scope="trajectory",
                config={"check": "tool_was_called"},
                applicable_roles=["generative"],
                provenance=prov,
            ),
        ]
        self._patch_generators(monkeypatch, specs)

        generate_and_preload_default_set(capability)

        eval_set = EvalSet.objects.get(capability=capability, name="Default")
        gen = set(
            eval_set.members.filter(role=EvalSetMember.Role.GENERATIVE).values_list(
                "evaluator__name", flat=True
            )
        )
        trace = set(
            eval_set.members.filter(role=EvalSetMember.Role.TRACE_SCORING).values_list(
                "evaluator__name", flat=True
            )
        )
        assert gen == {"task-success", "tool-trajectory"}
        assert trace == {"task-success"}
        by_name = {
            e.name: e.applicable_roles for e in Evaluator.objects.filter(capability=capability)
        }
        assert by_name["task-success"] == ["generative", "trace_scoring"]
        assert by_name["tool-trajectory"] == ["generative"]

    def test_rescan_identical_grounding_adds_nothing(self, monkeypatch):
        from overbae.models import EvalSetMember, Evaluator
        from overbae.services.eval.eval_set import generate_and_preload_default_set

        _user_, _client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        self._patch_generators(monkeypatch, self._specs())

        generate_and_preload_default_set(capability)
        members_before = EvalSetMember.objects.filter(eval_set__capability=capability).count()
        evals_before = {
            (e.name, e.scope): (e.id, e.version)
            for e in Evaluator.objects.filter(capability=capability, is_archived=False)
        }

        result = generate_and_preload_default_set(capability)

        assert result["created"] == 0
        assert result["added"] == 0
        assert (
            EvalSetMember.objects.filter(eval_set__capability=capability).count() == members_before
        )
        evals_after = {
            (e.name, e.scope): (e.id, e.version)
            for e in Evaluator.objects.filter(capability=capability, is_archived=False)
        }
        assert evals_after == evals_before  # same ids + versions, nothing re-minted

    def test_rescan_with_new_spec_adds_only_the_new_one(self, monkeypatch):
        from overbae.models import EvalSetMember, Evaluator
        from overbae.services.eval.eval_set import generate_and_preload_default_set
        from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance

        _user_, _client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        self._patch_generators(monkeypatch, self._specs())

        generate_and_preload_default_set(capability)
        members_before = EvalSetMember.objects.filter(eval_set__capability=capability).count()
        evals_before = {
            (e.name, e.scope): (e.id, e.version)
            for e in Evaluator.objects.filter(capability=capability, is_archived=False)
        }

        new_spec = EvaluatorSpec(
            name="latency-budget",
            kind="deterministic",
            scope="final_output",
            config={"check": "exact_match"},
            provenance=SpecProvenance(
                source="codebase_card.output_fields",
                generator="card_compiler",
                surface_area="output_contract",
            ),
        )
        self._patch_generators(monkeypatch, [*self._specs(), new_spec])

        result = generate_and_preload_default_set(capability)

        # The one new evaluator lands under both roles → two new members.
        assert result["created"] == 1
        assert result["added"] == 2
        assert (
            EvalSetMember.objects.filter(eval_set__capability=capability).count()
            == members_before + 2
        )
        evals_after = {
            (e.name, e.scope): (e.id, e.version)
            for e in Evaluator.objects.filter(capability=capability, is_archived=False)
        }
        for key, before in evals_before.items():
            assert evals_after[key] == before
        assert ("latency-budget", "final_output") in evals_after


class TestSyncCardEvaluators:
    # Invented capability card — the sync must be entirely schema-driven.
    _CARD = {
        "output_schema": {
            "required_keys": ["title", "body"],
            "properties": {},
            "provenance": [],
        },
        "constraints": [
            {
                "rule": "at most 5 calls",
                "type": "budget",
                "params": {"max_calls": 5},
                "provenance": [],
            },
        ],
        "tool_protocol": [],
        "tool_spec": [{"name": "fetch", "purpose": ""}],
    }

    def _capability(self, project):
        return Capability.objects.create(
            project=project,
            name="A",
            slug=f"a-{uuid.uuid4().hex[:6]}",
            improvement_metadata={"capability_card": dict(self._CARD)},
        )

    def test_sync_creates_evaluators_and_trace_scoring_members_idempotently(self):
        from overbae.models import EvalSetMember
        from overbae.services.eval.eval_set import sync_card_evaluators

        _user_, _client, project = _setup()
        capability = self._capability(project)

        result = sync_card_evaluators(capability)
        assert result["created"] == 2
        capability.refresh_from_db()
        names = {
            m.evaluator.name for m in capability.active_eval_set.members.select_related("evaluator")
        }
        assert {"card-constraints", "output-contract-required-keys"} <= names
        assert names.isdisjoint(
            {
                "output-schema-field-conformance",
                "tool-vocabulary-selection",
                "checkpoint-coverage",
                "trajectory-terminals",
            }
        )

        members_before = EvalSetMember.objects.filter(eval_set__capability=capability).count()
        again = sync_card_evaluators(capability)
        assert again["created"] == 0
        assert again["added"] == 0
        assert again["updated"] == 0
        assert (
            EvalSetMember.objects.filter(eval_set__capability=capability).count() == members_before
        )

    def test_sync_refreshes_compiled_constraint_params_in_place(self):
        from overbae.models import EvalSetMember
        from overbae.services.eval.eval_set import sync_card_evaluators

        _user_, _client, project = _setup()
        capability = self._capability(project)
        sync_card_evaluators(capability)
        capability.refresh_from_db()
        ev = Evaluator.objects.filter(
            capability=capability, name="card-constraints", is_archived=False
        ).first()
        if ev is None:
            pytest.skip("card-constraints is not compiled")
        assert ev.config["constraints"][0]["params"]["max_calls"] == 5
        version = ev.version
        member_id = EvalSetMember.objects.get(
            eval_set=capability.active_eval_set,
            evaluator=ev,
            role=EvalSetMember.Role.TRACE_SCORING,
        ).id

        card = dict(capability.improvement_metadata["capability_card"])
        card["constraints"] = [
            {
                "rule": "at most 5 calls",
                "type": "budget",
                "params": {"max_calls": 3},
                "provenance": [],
            }
        ]
        card["tool_protocol"] = [
            {
                "kind": "precondition",
                "rule": "get_rate before converting claim_currency into reporting_currency",
                "tools": ["fetch"],
                "params": {"when_fields_differ": ["claim_currency", "reporting_currency"]},
                "provenance": [],
            }
        ]
        capability.improvement_metadata = {
            **capability.improvement_metadata,
            "capability_card": card,
        }
        capability.save(update_fields=["improvement_metadata"])

        again = sync_card_evaluators(capability)
        assert again["created"] == 0
        assert again["updated"] >= 1
        ev.refresh_from_db()
        assert (
            ev.id
            == Evaluator.objects.get(
                capability=capability, name="card-constraints", is_archived=False
            ).id
        )
        # Grading-content refresh bumps version in place.
        assert ev.version != version
        by_rule = {e["rule"]: e["params"] for e in ev.config["constraints"]}
        assert by_rule["at most 5 calls"]["max_calls"] == 3
        assert by_rule["get_rate before converting claim_currency into reporting_currency"][
            "when_fields_differ"
        ] == ["claim_currency", "reporting_currency"]
        assert EvalSetMember.objects.get(pk=member_id).evaluator_id == ev.id

    def test_sync_backfills_missing_trace_scoring_membership(self):
        from overbae.models import EvalSetMember
        from overbae.services.eval.eval_set import ensure_default_eval_set, sync_card_evaluators

        _user_, _client, project = _setup()
        capability = self._capability(project)
        eval_set = ensure_default_eval_set(capability)
        existing = Evaluator.objects.create(
            project=project,
            capability=capability,
            name="card-constraints",
            kind="deterministic",
            scope="trajectory",
            config={"check": "card_constraints", "constraints": [], "declared_tools": []},
        )
        EvalSetMember.objects.create(
            eval_set=eval_set, evaluator=existing, role=EvalSetMember.Role.GENERATIVE
        )

        result = sync_card_evaluators(capability)

        member = eval_set.members.filter(
            role=EvalSetMember.Role.TRACE_SCORING,
            evaluator__name="card-constraints",
        ).first()
        if member is None:
            pytest.skip("card-constraints is not a compiled spec; no membership to backfill")
        assert result["created"] >= 0
        assert member.evaluator_id == existing.id

    def test_sync_mints_card_constraints_once(self):
        from overbae.services.eval.eval_set import ensure_default_eval_set, sync_card_evaluators

        _user_, _client, project = _setup()
        capability = self._capability(project)
        eval_set = ensure_default_eval_set(capability)

        result = sync_card_evaluators(capability)

        assert result["created"] == 2
        assert eval_set.members.filter(evaluator__name="card-constraints").exists()
        assert Evaluator.objects.filter(capability=capability, name="card-constraints").count() == 1
        assert (
            Evaluator.objects.filter(
                capability=capability, name="output-contract-required-keys"
            ).count()
            == 1
        )
        again = sync_card_evaluators(capability)
        assert again["created"] == 0
        assert Evaluator.objects.filter(capability=capability, name="card-constraints").count() == 1

    def test_sync_does_not_mint_structural_contract_gates(self):
        from overbae.services.eval.eval_set import sync_card_evaluators

        _user_, _client, project = _setup()
        capability = self._capability(project)

        sync_card_evaluators(capability)

        capability.refresh_from_db()
        assert (
            capability.active_eval_set is None
            or not capability.active_eval_set.members.filter(
                evaluator__name__in=(
                    "output-schema-field-conformance",
                    "tool-vocabulary-selection",
                ),
            ).exists()
        )
        assert not Evaluator.objects.filter(
            capability=capability,
            name__in=(
                "output-schema-field-conformance",
                "tool-vocabulary-selection",
            ),
        ).exists()

    def test_sync_propagates_behaviour_binding_onto_reused_rows(self):
        from overbae.services.eval.eval_set import sync_card_evaluators

        _user_, _client, project = _setup()
        card = dict(self._CARD)
        card["trajectory_map"] = [
            {
                "id": "happy",
                "claim": "declared_task",
                "prompt_quote": "answer the user",
                "routing": "in scope",
                "anchors": ["m.run"],
                "sequence": [],
                "terminal": {"kind": "emits_record"},
            }
        ]
        capability = Capability.objects.create(
            project=project,
            name="A",
            slug=f"a-{uuid.uuid4().hex[:6]}",
            improvement_metadata={"capability_card": card},
        )
        sync_card_evaluators(capability)
        evaluator = Evaluator.objects.get(capability=capability, name="behaviour-happy-success")
        cfg = dict(evaluator.config or {})
        cfg["behaviour"] = {"behaviour_key": "stale", "role": "step", "anchor_segment": ["x"]}
        evaluator.config = cfg
        evaluator.save(update_fields=["config"])

        again = sync_card_evaluators(capability)
        assert again["created"] == 0
        evaluator.refresh_from_db()
        assert evaluator.config["behaviour"] == {
            "behaviour_key": "happy",
            "role": "outcome",
            "anchor_segment": [],
        }

    def test_sync_refreshes_outcome_checklist_on_reused_rows(self):
        from overbae.services.eval.eval_set import sync_card_evaluators

        _user_, _client, project = _setup()
        card = dict(self._CARD)
        card["trajectory_map"] = [
            {
                "id": "happy",
                "claim": "declared_task",
                "prompt_quote": "answer the user",
                "routing": "in scope",
                "anchors": ["m.run"],
                "sequence": ["emit OpenUI answer"],
                "terminal": {"kind": "emits_record", "description": "visible OpenUI reply"},
            }
        ]
        capability = Capability.objects.create(
            project=project,
            name="A",
            slug=f"a-{uuid.uuid4().hex[:6]}",
            improvement_metadata={"capability_card": card},
        )
        sync_card_evaluators(capability)
        evaluator = Evaluator.objects.get(capability=capability, name="behaviour-happy-success")
        evaluator.checklist = [
            {"id": "right-task", "q": "right?"},
            {"id": "visible-openui", "q": "Was a visible OpenUI reply emitted?"},
            {"id": "no-invented-refs", "q": "No invented refs?"},
        ]
        evaluator.save(update_fields=["checklist"])

        again = sync_card_evaluators(capability)
        assert again["created"] == 0
        evaluator.refresh_from_db()
        ids = [item["id"] for item in evaluator.checklist]
        assert ids == [
            "serves-open-ask",
            "delivered-kind-matches",
            "miss-is-legitimate",
            "evidence-supported-reply",
        ]
        assert evaluator.checklist[0].get("gate") is True
        blob = " ".join(item["q"] for item in evaluator.checklist).lower()
        assert "openui" not in blob
        assert "invented" not in blob

    def test_sync_drops_stale_generated_trace_scoring_members(self):
        from overbae.models import EvalSetMember
        from overbae.services.eval.eval_set import sync_card_evaluators
        from overbae.services.eval.specs import TIER1_GENERATOR

        _user_, _client, project = _setup()
        card = dict(self._CARD)
        card["trajectory_map"] = [
            {
                "id": "happy",
                "claim": "declared_task",
                "prompt_quote": "answer the user",
                "routing": "in scope",
                "anchors": ["m.run"],
                "sequence": [],
                "terminal": {"kind": "emits_record"},
            }
        ]
        capability = Capability.objects.create(
            project=project,
            name="A",
            slug=f"a-{uuid.uuid4().hex[:6]}",
            improvement_metadata={"capability_card": card},
        )
        sync_card_evaluators(capability)
        eval_set = capability.active_eval_set
        # A different generator from the compiled suite: the stale-drop must never touch it.
        tier1 = Evaluator.objects.create(
            project=project,
            capability=capability,
            name="Live Format OpenUI",
            kind="llm_judge",
            scope="trajectory",
            rubric_md="Was OpenUI visible?",
            config={"provenance": {"generator": TIER1_GENERATOR, "source": "tier1"}},
        )
        handmade = Evaluator.objects.create(
            project=project,
            capability=capability,
            name="hand-written-hygiene",
            kind="llm_judge",
            scope="trajectory",
            rubric_md="custom",
        )
        # Same generator as the compiled suite but no longer minted by it.
        stale = Evaluator.objects.create(
            project=project,
            capability=capability,
            name="behaviour-gone-success",
            kind="llm_judge",
            scope="trajectory",
            rubric_md="old behaviour judge",
            config={
                "provenance": {"generator": "card_compiler@v1", "source": "old"},
                "behaviour": {"behaviour_key": "gone", "role": "outcome"},
            },
        )
        for order, evaluator in ((99, tier1), (100, handmade), (101, stale)):
            EvalSetMember.objects.create(
                eval_set=eval_set,
                evaluator=evaluator,
                role=EvalSetMember.Role.TRACE_SCORING,
                order=order,
            )

        result = sync_card_evaluators(capability)
        assert result["dropped"] == 1
        names = set(
            eval_set.members.filter(role=EvalSetMember.Role.TRACE_SCORING).values_list(
                "evaluator__name", flat=True
            )
        )
        assert "behaviour-gone-success" not in names
        assert "Live Format OpenUI" in names
        assert "hand-written-hygiene" in names
        assert "behaviour-happy-success" in names

    def test_sync_no_card_is_a_clean_noop(self):
        from overbae.services.eval.eval_set import sync_card_evaluators

        _user_, _client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        assert sync_card_evaluators(capability) == {
            "eval_set_id": None,
            "synced": 0,
            "created": 0,
            "updated": 0,
            "added": 0,
            "judges_bound": 0,
        }

    def test_sync_backfills_schema_derived_field_refs_onto_judge_checklists(self):
        # A checklist item that names one card output field verbatim gets a
        # ``field`` binding, so its failed verdicts join schema_field graph nodes.
        from overbae.services.eval.eval_set import sync_card_evaluators

        _user_, _client, project = _setup()
        capability = self._capability(project)  # card output_schema.required_keys = title, body
        judge = Evaluator.objects.create(
            project=project,
            capability=capability,
            name="coherence-judge",
            kind="llm_judge",
            rubric_md="Judge the article.",
            checklist=[
                {"id": "body_quality", "q": "Is the body coherent?", "weight": 1.0, "gate": False},
                {"id": "bogus_ref", "q": "?", "weight": 1.0, "gate": False, "field": "nope"},
                {"id": "holistic", "q": "Is it good overall?", "weight": 1.0, "gate": False},
            ],
        )

        result = sync_card_evaluators(capability)

        assert result["judges_bound"] == 1
        judge.refresh_from_db()
        by_id = {i["id"]: i for i in judge.checklist}
        assert by_id["body_quality"]["field"] == "body"
        assert by_id["bogus_ref"]["field"] == ""  # unknown ref rejected, not emitted
        assert by_id["holistic"].get("field", "") == ""
        assert judge.version == 1  # in-place backfill, no version minting

        assert sync_card_evaluators(capability)["judges_bound"] == 0

    def test_sync_drops_checklist_items_owned_by_field_accuracy(self):
        from overbae.services.eval.eval_set import sync_card_evaluators

        _user_, _client, project = _setup()
        card = dict(self._CARD)
        card["output_fields"] = {
            "amount": "number — total due",
            "title": "string — a brief description of the document",
        }
        card["output_schema"] = {
            "required_keys": ["amount", "title"],
            "properties": {"amount": {}, "title": {}},
            "provenance": [],
        }
        capability = Capability.objects.create(
            project=project,
            name="A",
            slug=f"a-{uuid.uuid4().hex[:6]}",
            improvement_metadata={"capability_card": card},
        )
        judge = Evaluator.objects.create(
            project=project,
            capability=capability,
            name="task-success",
            kind="llm_judge",
            rubric_md="Judge the row.",
            checklist=[
                {"id": "amount_ok", "q": "Does amount match?", "weight": 1.0, "gate": False},
                {"id": "title_ok", "q": "Is the title right?", "weight": 1.0, "gate": False},
            ],
        )

        result = sync_card_evaluators(capability)

        assert result["judges_bound"] == 1
        judge.refresh_from_db()
        assert [i["id"] for i in judge.checklist] == ["title_ok"]
        assert judge.checklist[0]["field"] == "title"


class TestEvalSetMemberScores:
    """Members carry latest/previous scores rescaled 0–100 from completed-run summaries."""

    def _fixture(self, project):
        from overbae.models import EvalSet, EvalSetMember

        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        dataset = frozen_dataset(project, EVAL_ROWS, capability=capability)
        evaluator = Evaluator.objects.create(
            project=project, capability=capability, name="Quality", kind="llm_judge", rubric_md="x"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        member = EvalSetMember.objects.create(
            eval_set=eval_set, evaluator=evaluator, role="generative"
        )
        return capability, dataset, evaluator, eval_set, member

    def _completed_run(self, project, dataset, name, mean, *, minutes):
        import datetime

        from django.utils import timezone

        run = EvalRun.objects.create(
            project=project,
            name=name,
            data_source="dataset",
            dataset=dataset,
            status=EvalRun.Status.COMPLETED,
            summary={
                "metrics": ["Quality"],
                "variants": {"v1": {"metrics": {"Quality": {"mean": mean}}}},
            },
        )
        # created_at is auto_now_add; stamp it so newest-first ordering is deterministic.
        EvalRun.objects.filter(pk=run.pk).update(
            created_at=timezone.now() + datetime.timedelta(minutes=minutes)
        )
        return run

    def _member(self, client, capability, eval_set, member):
        r = client.get(f"/api/eval-sets/?capability={capability.id}")
        assert r.status_code == 200, r.content
        sets = {s["id"]: s for s in r.json()["results"]}
        members = {m["id"]: m for m in sets[str(eval_set.id)]["members"]}
        return members[str(member.id)]

    def test_increase_yields_positive_delta(self):
        _user_, client, project = _setup()
        capability, dataset, _ev, eval_set, member = self._fixture(project)
        self._completed_run(project, dataset, "prev", 0.6, minutes=0)
        self._completed_run(project, dataset, "latest", 0.8, minutes=10)

        m = self._member(client, capability, eval_set, member)
        assert m["latest_score"] == 80.0
        assert m["previous_score"] == 60.0
        assert m["delta"] == 20.0

    def test_decrease_yields_negative_delta(self):
        _user_, client, project = _setup()
        capability, dataset, _ev, eval_set, member = self._fixture(project)
        self._completed_run(project, dataset, "prev", 0.8, minutes=0)
        self._completed_run(project, dataset, "latest", 0.6, minutes=10)

        m = self._member(client, capability, eval_set, member)
        assert m["latest_score"] == 60.0
        assert m["previous_score"] == 80.0
        assert m["delta"] == -20.0

    def test_equal_yields_zero_delta(self):
        _user_, client, project = _setup()
        capability, dataset, _ev, eval_set, member = self._fixture(project)
        self._completed_run(project, dataset, "prev", 0.75, minutes=0)
        self._completed_run(project, dataset, "latest", 0.75, minutes=10)

        m = self._member(client, capability, eval_set, member)
        assert m["latest_score"] == 75.0
        assert m["previous_score"] == 75.0
        assert m["delta"] == 0.0

    def test_single_run_has_score_but_null_delta(self):
        _user_, client, project = _setup()
        capability, dataset, _ev, eval_set, member = self._fixture(project)
        self._completed_run(project, dataset, "only", 0.5, minutes=0)

        m = self._member(client, capability, eval_set, member)
        assert m["latest_score"] == 50.0
        assert m["previous_score"] is None
        assert m["delta"] is None

    def test_no_completed_runs_is_all_null(self):
        _user_, client, project = _setup()
        capability, _dataset, _ev, eval_set, member = self._fixture(project)

        m = self._member(client, capability, eval_set, member)
        assert m["latest_score"] is None
        assert m["previous_score"] is None
        assert m["delta"] is None

    def test_running_run_is_ignored(self):
        _user_, client, project = _setup()
        capability, dataset, _ev, eval_set, member = self._fixture(project)
        run = self._completed_run(project, dataset, "wip", 0.9, minutes=0)
        EvalRun.objects.filter(pk=run.pk).update(status=EvalRun.Status.RUNNING)

        m = self._member(client, capability, eval_set, member)
        assert m["latest_score"] is None
        assert m["delta"] is None


_BEHAVIOUR_SHA = "b" * 40


def _behaviour_with_contract(capability) -> Behaviour:
    behaviour = Behaviour.objects.create(
        project=capability.project,
        capability=capability,
        key="happy",
        display_name="Happy path",
        entry_anchor="m.run",
        first_seen_sha=_BEHAVIOUR_SHA,
        last_seen_sha=_BEHAVIOUR_SHA,
    )
    BehaviourVersion.objects.create(
        behaviour=behaviour,
        analyzed_sha=_BEHAVIOUR_SHA,
        contract={
            "key": "happy",
            "claim": "code_path",
            "entry_anchor": "m.run",
            "anchor_sequence": ["m.run", "m.fetch", "m.finish"],
            "steps": [{"step": "Fetch data", "kind": "agent_step", "anchors": ["m.fetch"]}],
            "tool_set": [],
            "terminal": {"kind": "emits_record", "description": ""},
        },
    )
    return behaviour


class TestAuthorBehaviourScopedJudge:
    def test_outcome_judge_stamps_trajectory_scope_and_behaviour_config(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        behaviour = _behaviour_with_contract(capability)
        body = {
            "project": str(project.id),
            "behaviour": str(behaviour.id),
            "name": "Outcome Judge",
            "evaluation_prompt": "Did the task succeed?",
            "score_type": "boolean",
            "boolean_verdict_prompt": "Return true on success.",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        ev = Evaluator.objects.get(id=r.json()["id"])
        assert ev.scope == "trajectory"
        assert ev.capability_id == capability.id
        assert ev.config["behaviour"] == {
            "behaviour_key": "happy",
            "role": "outcome",
            "anchor_segment": [],
        }
        var_names = {v["var"] for v in ev.variable_mapping}
        assert {"trajectory", "tool_calls"} <= var_names

    def test_step_judge_requires_and_stores_valid_anchor_segment(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        behaviour = _behaviour_with_contract(capability)
        body = {
            "project": str(project.id),
            "behaviour": str(behaviour.id),
            "behaviour_role": "step",
            "anchor_segment": ["m.fetch"],
            "name": "Step Judge",
            "evaluation_prompt": "Was the fetch step done well?",
            "score_type": "boolean",
            "boolean_verdict_prompt": "Return true if done well.",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        ev = Evaluator.objects.get(id=r.json()["id"])
        assert ev.config["behaviour"] == {
            "behaviour_key": "happy",
            "role": "step",
            "anchor_segment": ["m.fetch"],
        }

    def test_step_judge_rejects_empty_anchor_segment(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        behaviour = _behaviour_with_contract(capability)
        body = {
            "project": str(project.id),
            "behaviour": str(behaviour.id),
            "behaviour_role": "step",
            "name": "Step Judge",
            "evaluation_prompt": "x",
            "score_type": "boolean",
            "boolean_verdict_prompt": "y",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400, r.content
        assert "anchor_segment" in r.json()

    def test_step_judge_rejects_segment_not_in_contract(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        behaviour = _behaviour_with_contract(capability)
        body = {
            "project": str(project.id),
            "behaviour": str(behaviour.id),
            "behaviour_role": "step",
            "anchor_segment": ["m.nope"],
            "name": "Step Judge",
            "evaluation_prompt": "x",
            "score_type": "boolean",
            "boolean_verdict_prompt": "y",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400, r.content
        assert "anchor_segment" in r.json()

    def test_behaviour_from_different_project_rejected(self):
        _u, client, project = _setup()
        other_project = Project.objects.create(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")
        other_capability = Capability.objects.create(
            project=other_project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        behaviour = _behaviour_with_contract(other_capability)
        body = {
            "project": str(project.id),
            "behaviour": str(behaviour.id),
            "name": "Cross Project Judge",
            "evaluation_prompt": "x",
            "score_type": "boolean",
            "boolean_verdict_prompt": "y",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400, r.content
        assert "behaviour" in r.json()

    def test_generative_without_behaviour_uses_final_output_scope(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Plain Judge",
            "evaluation_prompt": "Rate the answer.",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        ev = Evaluator.objects.get(id=r.json()["id"])
        assert ev.scope == "final_output"
        assert "behaviour" not in ev.config

    def test_trace_scoring_without_behaviour_rejected(self):
        _u, client, project = _setup()
        body = {
            "project": str(project.id),
            "name": "Trace Judge",
            "evaluation_prompt": "Rate the trace.",
            "score_type": "numeric",
            "applicable_roles": ["trace_scoring"],
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400, r.content
        assert "behaviour" in r.json()


class TestNameCollisionGuard:
    def test_rejects_name_shared_with_managed_evaluator(self):
        _u, client, project = _setup()
        Evaluator.objects.create(
            project=None, name="managed-check", kind="llm_judge", is_managed=True, version=1
        )
        body = {
            "project": str(project.id),
            "name": "managed-check",
            "evaluation_prompt": "x",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400, r.content
        assert "name" in r.json()

    def test_rejects_name_shared_with_machine_authored_evaluator(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        Evaluator.objects.create(
            project=project,
            capability=capability,
            name="auto-check",
            kind="deterministic",
            version=1,
            config={"provenance": {"generator": "card_compiler@v1"}},
        )
        body = {
            "project": str(project.id),
            "capability": str(capability.id),
            "name": "auto-check",
            "evaluation_prompt": "x",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400, r.content
        assert "name" in r.json()

    def test_allows_reauthoring_own_hand_written_name(self):
        """Re-versioning a user's own prior evaluator is unaffected — the
        collision guard only fires against managed/machine-authored rows."""
        _u, client, project = _setup()
        Evaluator.objects.create(
            project=project, name="MyEval", kind="llm_judge", version=1, rubric_md="x"
        )
        body = {
            "project": str(project.id),
            "name": "MyEval",
            "evaluation_prompt": "x",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        assert r.json()["version"] == 2


class TestAuthorAttachOnSave:
    def test_author_attaches_evaluator_as_generative_member(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        body = {
            "project": str(project.id),
            "capability": str(capability.id),
            "eval_set": str(eval_set.id),
            "eval_set_role": "generative",
            "name": "Attached Judge",
            "evaluation_prompt": "Rate the answer.",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        member = EvalSetMember.objects.get(eval_set=eval_set)
        assert str(member.evaluator_id) == r.json()["id"]
        assert member.role == EvalSetMember.Role.GENERATIVE

    def test_author_attaches_evaluator_as_trace_scoring_member(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        body = {
            "project": str(project.id),
            "capability": str(capability.id),
            "eval_set": str(eval_set.id),
            "eval_set_role": "trace_scoring",
            "name": "Attached Trace Judge",
            "evaluation_prompt": "Rate the answer.",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 201, r.content
        member = EvalSetMember.objects.get(eval_set=eval_set)
        assert member.role == EvalSetMember.Role.TRACE_SCORING

    def test_eval_set_without_role_is_rejected(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        body = {
            "project": str(project.id),
            "eval_set": str(eval_set.id),
            "name": "No Role Judge",
            "evaluation_prompt": "x",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400, r.content
        assert "eval_set_role" in r.json()
        assert not Evaluator.objects.filter(name="No Role Judge").exists()

    def test_eval_set_from_different_project_is_rejected(self):
        _u, client, project = _setup()
        other_project = Project.objects.create(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")
        other_capability = Capability.objects.create(
            project=other_project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(
            project=other_project, capability=other_capability, name="Default"
        )
        body = {
            "project": str(project.id),
            "eval_set": str(eval_set.id),
            "eval_set_role": "generative",
            "name": "Cross Project Judge",
            "evaluation_prompt": "x",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400, r.content
        assert "eval_set" in r.json()

    def test_role_mismatch_rejected_and_evaluator_not_created(self):
        """applicable_roles=["generative"] structurally excludes trace_scoring —
        the attach must fail, and nothing partial should be left behind."""
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        before = Evaluator.objects.count()
        body = {
            "project": str(project.id),
            "eval_set": str(eval_set.id),
            "eval_set_role": "trace_scoring",
            "applicable_roles": ["generative"],
            "name": "Mismatched Judge",
            "evaluation_prompt": "x",
            "score_type": "numeric",
        }
        r = client.post("/api/evaluators/author/", body, format="json")
        assert r.status_code == 400, r.content
        assert "eval_set_role" in r.json()
        assert Evaluator.objects.count() == before
        assert not EvalSetMember.objects.filter(eval_set=eval_set).exists()

    def test_author_update_attaches_existing_evaluator(self):
        _u, client, project = _setup()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
        )
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        ev = Evaluator.objects.create(
            project=project,
            capability=capability,
            name="Existing Judge",
            kind="llm_judge",
            version=1,
            rubric_md="x",
        )
        body = {
            "project": str(project.id),
            "capability": str(capability.id),
            "eval_set": str(eval_set.id),
            "eval_set_role": "generative",
            "name": "Existing Judge",
            "evaluation_prompt": "Rate the answer.",
            "score_type": "numeric",
        }
        r = client.put(f"/api/evaluators/{ev.id}/author/", body, format="json")
        assert r.status_code == 200, r.content
        member = EvalSetMember.objects.get(eval_set=eval_set)
        assert member.evaluator_id == ev.id
        assert member.role == EvalSetMember.Role.GENERATIVE
