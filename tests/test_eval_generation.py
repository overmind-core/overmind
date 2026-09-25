from __future__ import annotations

import copy
import json
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from overbae.services.eval import card_compiler
from overbae.services.eval.grounding import EvalGroundingContext, render_grounding_pack
from overbae.services.eval.specs import (
    EvaluatorSpec,
    validate_spec_payloads,
)

DATA_VERSION = "27b69c0ff49aef35"
CODEBASE_COMMIT = "c5732e345cff21eac58b26a983d76cddf9f386f9"

DATASET_CARD = {
    "_fallback": False,
    "format": "classification",
    "summary": "Langfuse export of the data-quality capability's own evaluation runs (159 rows).",
    "io_mapping": {"input": "input", "expected_output": "reference", "metadata": "metadata"},
    "reference": {
        "columns": ["expected_output"],
        "description": "The capability's produced artifact for each pipeline stage; JSON-valid and grounded.",
    },
    "volume_and_tokens": {"total_rows": 159},
    "label_space": {"column": "createdAt", "balance": "skewed"},
    "quality_signals": [
        {
            "signal": "No pk-lf-* credentials in metadata",
            "severity": "gate",
            "detection_pattern": "pk-lf-",
            "baseline_match_rate": 1.0,
        },
        {"signal": "rows are well-formed", "severity": "weighted"},
    ],
    "failure_modes": [
        {
            "description": "Stale upstream echoes (dolly.jsonl, 15000 rows) in expected_output",
            "detection_pattern": r"dolly\.jsonl|\b15000\b|open_qa",
            "baseline_match_rate": 0.195,
            "example_row_ids": [],
        },
        {
            "description": "Mirrored duplicate templates across sourceTraceId runs",
            "detection_pattern": "",
            "example_row_ids": [],
        },
        {
            "description": "Broken pattern entry",
            "detection_pattern": "([",
            "example_row_ids": [],
        },
    ],
    "provenance": {"data_version": DATA_VERSION, "columns": ["input", "expected_output"]},
}

CODEBASE_CARD = {
    "_fallback": False,
    "task": "Analyze an uploaded ML dataset against a user agenda and emit a quality report.",
    "output_fields": {"summary": "", "clusters": "", "recommendations": ""},
    "output_schema": {
        "required_keys": ["summary", "clusters", "recommendations"],
        "properties": {},
        "provenance": [],
    },
    "tool_spec": [
        {"name": "sample_rows", "purpose": ""},
        {"name": "get_rows_by_id", "purpose": ""},
        {"name": "report_stage", "purpose": ""},
    ],
    "success_criteria": ["passes parse_and_validate with all required keys present"],
    "failure_modes": ["Invents row counts instead of reading dataset_facts.json"],
    "expected_output": {
        "description": "A complete analysis_compact.json row.",
        "quality_signals": ["summary.rows equals authoritative total_rows"],
    },
    "vocabulary": {"__row_id__": "stable content signature"},
}

SCALAR_LABEL_CODEBASE_CARD = {
    "_fallback": False,
    "task": "Classify the user's message into one e-commerce intent label.",
    "output_fields": {"intent_label": ""},
    "output_schema": {"required_keys": [], "properties": {}},
    "success_criteria": [
        "Returns exactly one snake_case intent label from the taxonomy",
    ],
    "expected_output": {
        "description": "A single snake_case intent label",
        "example": "cancel_order",
    },
    "vocabulary": {
        "cancel_order": "user wants to cancel",
        "track_shipment": "user asks for tracking",
    },
}

REPORT = {
    "conversation_anatomy": {"tool_calls": {"total": 0}, "format": "flat"},
    "format_compliance": {"expected_format": "json", "valid_pct": 0.8239, "invalid_count": 28},
    "compatibility": {
        "eval_ready": False,
        "missing_for_eval": ["a non-empty expected_output on every row"],
    },
    "agenda_coverage": {"uncovered_intents": ["tool-call vocabulary grounding"]},
    "eval_readiness": {
        "score": 25.0,
        "issues": [{"id": "redact_pii", "row_signatures": ["a" * 40]}],
    },
    "validation_issues": [{"issue": "All 159 rows expose pk-lf-…"}],
}


def _grounding(**overrides) -> EvalGroundingContext:
    defaults = {
        "dataset_card": copy.deepcopy(DATASET_CARD),
        "data_version": DATA_VERSION,
        "codebase_card": copy.deepcopy(CODEBASE_CARD),
        "codebase_commit": CODEBASE_COMMIT,
        "report": copy.deepcopy(REPORT),
        "evaluator_inventory": [],
    }
    defaults.update(overrides)
    return EvalGroundingContext(**defaults)


def _provenance(**overrides) -> dict:
    base = {
        "source": "dataset_card.failure_modes[0]",
        "data_version": DATA_VERSION,
        "generator": "test@v1",
        "surface_area": "failure_mode",
    }
    base.update(overrides)
    return base


def _spec_payload(**overrides) -> dict:
    base = {
        "name": "no-stale-upstream-echoes",
        "kind": "deterministic",
        "scope": "final_output",
        "score_type": "boolean",
        "config": {"check": "regex", "pattern": "^(?:(?!dolly)[\\s\\S])*$"},
        "provenance": _provenance(),
    }
    base.update(overrides)
    return base


class TestEvaluatorSpec:
    def test_valid_deterministic_regex_spec(self):
        spec = EvaluatorSpec.model_validate(_spec_payload())
        assert spec.kind == "deterministic"
        assert spec.provenance_key() == ("dataset_card.failure_modes[0]", DATA_VERSION)

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValidationError, match="unknown kind"):
            EvaluatorSpec.model_validate(_spec_payload(kind="vibes"))

    def test_rejects_unknown_scope(self):
        with pytest.raises(ValidationError, match="unknown scope"):
            EvaluatorSpec.model_validate(_spec_payload(scope="paragraph"))

    def test_rejects_unknown_deterministic_check(self):
        payload = _spec_payload(config={"check": "telepathy"})
        with pytest.raises(ValidationError, match="check"):
            EvaluatorSpec.model_validate(payload)

    def test_rejects_invalid_regex_pattern(self):
        payload = _spec_payload(config={"check": "regex", "pattern": "(["})
        with pytest.raises(ValidationError, match="invalid regex"):
            EvaluatorSpec.model_validate(payload)

    def test_rejects_unknown_statistical_metric(self):
        payload = _spec_payload(
            kind="statistical", scope="dataset", config={"metric": "vibe_distance"}
        )
        with pytest.raises(ValidationError, match="metric"):
            EvaluatorSpec.model_validate(payload)

    def test_statistical_requires_dataset_scope(self):
        payload = _spec_payload(
            kind="statistical", scope="final_output", config={"metric": "rouge_l"}
        )
        with pytest.raises(ValidationError, match="scope='dataset'"):
            EvaluatorSpec.model_validate(payload)

    def test_judge_requires_rubric_or_checklist(self):
        payload = _spec_payload(kind="llm_judge", config={})
        with pytest.raises(ValidationError, match="rubric_md or a checklist"):
            EvaluatorSpec.model_validate(payload)

    def test_rejects_unknown_variable_source(self):
        payload = _spec_payload(
            kind="llm_judge",
            rubric_md="Grade {{output}}.",
            config={},
            variable_mapping=[{"var": "output", "source": "telemetry_feed"}],
        )
        with pytest.raises(ValidationError, match="unknown variable source"):
            EvaluatorSpec.model_validate(payload)

    def test_rejects_unknown_surface_area(self):
        payload = _spec_payload(provenance=_provenance(surface_area="vibes"))
        with pytest.raises(ValidationError):
            EvaluatorSpec.model_validate(payload)

    def test_to_evaluator_kwargs_folds_provenance_into_config(self):
        spec = EvaluatorSpec.model_validate(_spec_payload())
        kwargs = spec.to_evaluator_kwargs()
        assert kwargs["config"]["check"] == "regex"
        assert kwargs["config"]["provenance"]["source"] == "dataset_card.failure_modes[0]"
        assert kwargs["config"]["provenance"]["data_version"] == DATA_VERSION
        assert kwargs["name"] == "no-stale-upstream-echoes"

    def test_rejects_unknown_applicable_role(self):
        with pytest.raises(ValidationError, match="unknown applicable_roles"):
            EvaluatorSpec.model_validate(_spec_payload(applicable_roles=["vibes"]))

    def test_applicable_roles_deduped_and_persisted(self):
        payload = _spec_payload(applicable_roles=["generative", "generative", "trace_scoring"])
        spec = EvaluatorSpec.model_validate(payload)
        assert spec.applicable_roles == ["generative", "trace_scoring"]
        assert spec.to_evaluator_kwargs()["applicable_roles"] == ["generative", "trace_scoring"]

    def test_reference_graded_spec_stripped_to_generative_only(self):
        # A live trace has no curated reference: explicit flag or inferred evidence both strip it.
        explicit = EvaluatorSpec.model_validate(
            _spec_payload(
                kind="llm_judge",
                rubric_md="Compare {{output}} to {{reference}}.",
                config={},
                requires_reference=True,
                applicable_roles=["generative", "trace_scoring"],
            )
        )
        assert explicit.applicable_roles == ["generative"]

        inferred = EvaluatorSpec.model_validate(
            _spec_payload(
                kind="llm_judge",
                rubric_md="Compare to {{reference}}.",
                config={},
                variable_mapping=[{"var": "reference", "source": "reference"}],
                applicable_roles=["generative", "trace_scoring"],
            )
        )
        assert inferred.applicable_roles == ["generative"]

    def test_output_quality_judge_is_dual_role(self):
        # A live trace also carries the model's own output, so no-reference judges are dual-role.
        from overbae.services.eval.roles import roles_for_spec

        spec = EvaluatorSpec.model_validate(
            _spec_payload(
                kind="llm_judge",
                scope="final_output",
                rubric_md="Grade whether {{output}} is complete and well-typed.",
                config={},
                variable_mapping=[{"var": "output", "source": "output"}],
            )
        )
        assert spec.effective_evidence() == "model_output"
        assert set(roles_for_spec(spec)) == {"generative", "trace_scoring"}

    def test_model_surface_grader_is_generative_only(self):
        # surface='model' enforces the RAW extraction contract (keys the harness may drop);
        # trace scoring grades the harness deliverable, so it drops despite a dual-role shape.
        from overbae.services.eval.roles import roles_for_spec

        surfaceless = EvaluatorSpec.model_validate(
            _spec_payload(config={"check": "json_schema_valid", "required_keys": ["isInvoice"]})
        )
        assert set(roles_for_spec(surfaceless)) == {"generative", "trace_scoring"}

        spec = EvaluatorSpec.model_validate(
            _spec_payload(
                config={"check": "json_schema_valid", "required_keys": ["isInvoice"]},
                surface="model",
            )
        )
        assert roles_for_spec(spec) == ("generative",)
        assert spec.to_evaluator_kwargs()["surface"] == "model"

    def test_harness_surface_grader_is_trace_only(self):
        # surface='harness' needs the assembled capability record the generate path can't produce.
        from overbae.services.eval.roles import roles_for_spec

        spec = EvaluatorSpec.model_validate(
            _spec_payload(
                kind="llm_judge",
                scope="final_output",
                rubric_md="Grade whether {{output}} is a well-formed delivered record.",
                config={},
                variable_mapping=[{"var": "output", "source": "output"}],
                surface="harness",
            )
        )
        assert roles_for_spec(spec) == ("trace_scoring",)

    def test_unknown_surface_rejected(self):
        with pytest.raises(ValidationError, match="unknown surface"):
            EvaluatorSpec.model_validate(_spec_payload(surface="frontend"))

    def test_harness_and_corpus_judges_are_generative_only(self):
        # Neither a harness-assembled ``structured`` object nor a corpus statistic exists
        # for a single live trace.
        from overbae.services.eval.roles import roles_for_spec

        harness = EvaluatorSpec.model_validate(
            _spec_payload(
                kind="llm_judge",
                scope="final_output",
                rubric_md="Grade the assembled {{structured}} trace object.",
                config={},
                evidence_requirement="harness_artifact",
                variable_mapping=[{"var": "structured", "source": "structured"}],
            )
        )
        assert roles_for_spec(harness) == ("generative",)

        corpus = EvaluatorSpec.model_validate(
            _spec_payload(
                kind="statistical",
                scope="dataset",
                config={"metric": "rouge_l"},
            )
        )
        assert roles_for_spec(corpus) == ("generative",)

    def test_validate_spec_payloads_drops_invalid_keeps_valid(self):
        specs, errors = validate_spec_payloads(
            [_spec_payload(), _spec_payload(kind="vibes", name="bad-spec")]
        )
        assert len(specs) == 1
        assert len(errors) == 1
        assert "bad-spec" in errors[0]


class TestTypedParamContract:
    def test_source_types_cover_every_variable_source(self):
        from overbae.services.eval.specs import SOURCE_TYPES, VARIABLE_SOURCES

        assert set(SOURCE_TYPES) == set(VARIABLE_SOURCES)

    def test_text_sources_are_the_conversation_surfaces(self):
        from overbae.services.eval.specs import TEXT_SOURCES

        assert (
            frozenset({"input", "last_user_input", "all_user_messages", "conversation"})
            == TEXT_SOURCES
        )
        # output/final_output may carry stringified JSON → NOT text-only.
        assert "output" not in TEXT_SOURCES

    def test_rejects_jsonpath_on_text_source(self):
        payload = _spec_payload(
            kind="llm_judge",
            rubric_md="Grade {{n}}.",
            config={},
            variable_mapping=[
                {"var": "n", "source": "input", "jsonpath": "$.dataset_facts.total_rows_exact"}
            ],
        )
        with pytest.raises(ValidationError, match="cannot traverse"):
            EvaluatorSpec.model_validate(payload)

    def test_allows_jsonpath_on_structured_output_source(self):
        payload = _spec_payload(
            kind="llm_judge",
            rubric_md="Grade {{recs}}.",
            config={},
            variable_mapping=[{"var": "recs", "source": "output", "jsonpath": "$.recommendations"}],
        )
        spec = EvaluatorSpec.model_validate(payload)
        assert spec.variable_mapping[0].jsonpath == "$.recommendations"

    def test_allows_text_source_without_jsonpath(self):
        payload = _spec_payload(
            kind="llm_judge",
            rubric_md="Grade {{q}}.",
            config={},
            variable_mapping=[{"var": "q", "source": "input"}],
        )
        spec = EvaluatorSpec.model_validate(payload)
        assert spec.variable_mapping[0].source == "input"


class TestManagedEvaluatorsObeyTypedContract:
    def test_no_managed_mapping_binds_jsonpath_to_text_source(self):
        from overbae.services.eval.managed import MANAGED_EVALUATORS
        from overbae.services.eval.specs import TEXT_SOURCES

        offenders = [
            f"{tpl.get('name')}: {entry}"
            for tpl in MANAGED_EVALUATORS
            for entry in (tpl.get("variable_mapping") or [])
            if entry.get("jsonpath") and entry.get("source") in TEXT_SOURCES
        ]
        assert offenders == [], f"managed evaluators violate B2: {offenders}"


class TestCardCompiler:
    def _by_name(self, specs):
        return {spec.name: spec for spec in specs}

    def _card_constraints(self, specs):
        spec = self._by_name(specs).get("card-constraints")
        if spec is None:
            pytest.skip("card-constraints is not compiled")
        return spec

    def _tool_vocabulary(self, specs):
        spec = self._by_name(specs).get("tool-vocabulary-selection")
        if spec is None:
            pytest.skip("tool-vocabulary-selection is not compiled")
        return spec

    def test_failure_mode_compiles_to_negated_regex_gate(self):
        specs = card_compiler.compile_card_evaluators(_grounding())
        named = self._by_name(specs)
        spec = named["no-stale-upstream-echoes-dolly-jsonl-15000-rows-in"]
        assert spec.kind == "deterministic"
        assert spec.score_type == "boolean"
        assert spec.config["check"] == "regex"
        assert spec.config["pattern"] == r"^(?:(?!dolly\.jsonl|\b15000\b|open_qa)[\s\S])*$"
        assert spec.provenance.source == "dataset_card.failure_modes[0]"
        assert spec.provenance.baseline_match_rate == 0.195
        assert spec.provenance.data_version == DATA_VERSION
        assert spec.provenance.codebase_commit == CODEBASE_COMMIT
        assert spec.provenance.surface_area == "failure_mode"

    def test_patternless_and_broken_failure_modes_are_skipped(self):
        specs = card_compiler.compile_card_evaluators(_grounding())
        sources = {spec.provenance.source for spec in specs}
        assert "dataset_card.failure_modes[1]" not in sources  # no pattern
        assert "dataset_card.failure_modes[2]" not in sources  # invalid regex

    def test_gate_quality_signal_compiles_weighted_does_not(self):
        specs = card_compiler.compile_card_evaluators(_grounding())
        sources = {spec.provenance.source for spec in specs}
        assert "dataset_card.quality_signals[0]" in sources
        assert "dataset_card.quality_signals[1]" not in sources
        gate = next(s for s in specs if s.provenance.source == "dataset_card.quality_signals[0]")
        assert gate.config["pattern"] == r"^(?:(?!pk-lf-)[\s\S])*$"
        assert gate.provenance.baseline_match_rate == 1.0

    def test_output_contract_uses_output_schema_required_keys(self):
        specs = card_compiler.compile_card_evaluators(_grounding())
        spec = self._by_name(specs)["output-contract-required-keys"]
        assert spec.config == {
            "check": "json_schema_valid",
            "required_keys": ["summary", "clusters", "recommendations"],
            "gate_only": True,
        }
        assert spec.provenance.surface_area == "output_contract"
        # Enforces the RAW extraction schema → stays off live trace scoring.
        assert spec.surface == "model"
        from overbae.services.eval.roles import roles_for_spec

        assert roles_for_spec(spec) == ("generative",)

    def test_scalar_label_skips_json_schema_valid(self):
        specs = card_compiler.compile_card_evaluators(
            _grounding(codebase_card=copy.deepcopy(SCALAR_LABEL_CODEBASE_CARD))
        )
        assert "output-contract-required-keys" not in self._by_name(specs)

    def test_json_object_from_dict_example_without_required_keys(self):
        card = {
            "_fallback": False,
            "output_fields": {"summary": "", "clusters": "", "recommendations": ""},
            "output_schema": {"required_keys": []},
            "expected_output": {
                "example": {"summary": "ok", "clusters": [], "recommendations": []},
            },
        }
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        spec = self._by_name(specs)["output-contract-required-keys"]
        assert spec.config == {
            "check": "json_schema_valid",
            "required_keys": ["summary", "clusters", "recommendations"],
            "gate_only": True,
        }

    def test_tool_selection_blocked_when_stored_rows_have_no_tool_calls(self):
        specs = card_compiler.compile_card_evaluators(_grounding())
        spec = self._tool_vocabulary(specs)
        assert spec.config["check"] == "tool_selection"
        assert spec.config["expected_tools"] == ["sample_rows", "get_rows_by_id", "report_stage"]
        assert spec.scope == "trajectory"
        assert "precision" in spec.description.lower()
        assert spec.provenance.blocked_on  # generate-mode-only marker

    def test_tool_selection_unblocked_when_tool_calls_present(self):
        report = copy.deepcopy(REPORT)
        report["conversation_anatomy"]["tool_calls"]["total"] = 12
        specs = card_compiler.compile_card_evaluators(_grounding(report=report))
        spec = self._tool_vocabulary(specs)
        assert spec.provenance.blocked_on == []

    def test_tool_vocabulary_gated_when_observed_tools_dont_intersect(self):
        report = copy.deepcopy(REPORT)
        report["conversation_anatomy"]["tool_calls"] = {
            "total": 30,
            "unique_tools": ["read", "glob", "shell", "edit"],
        }
        specs = card_compiler.compile_card_evaluators(_grounding(report=report))
        spec = self._tool_vocabulary(specs)
        assert any("does not intersect" in b for b in spec.provenance.blocked_on)

    def test_tool_vocabulary_not_gated_when_observed_tools_intersect(self):
        report = copy.deepcopy(REPORT)
        report["conversation_anatomy"]["tool_calls"] = {
            "total": 30,
            "unique_tools": ["sample_rows", "shell"],
        }
        specs = card_compiler.compile_card_evaluators(_grounding(report=report))
        spec = self._tool_vocabulary(specs)
        assert not any("does not intersect" in b for b in spec.provenance.blocked_on)

    def test_schema_conformance_is_not_a_compiled_card_eval(self):
        report = copy.deepcopy(REPORT)
        report["conversation_anatomy"]["tool_calls"]["total"] = 12
        specs = card_compiler.compile_card_evaluators(_grounding(report=report))
        names = set(self._by_name(specs))
        assert "output-schema-field-conformance" not in names
        assert "tool-vocabulary-selection" in names

    def test_card_constraints_compile_from_a_constraint_rich_card(self):
        card = copy.deepcopy(CODEBASE_CARD)
        card["constraints"] = [
            {
                "rule": "at most 3 calls",
                "type": "budget",
                "params": {"max_calls": 3},
                "provenance": [],
            },
        ]
        card["tool_protocol"] = [
            {
                "rule": "sample before report",
                "kind": "ordering",
                "tools": ["sample_rows", "report_stage"],
                "provenance": [],
            },
        ]
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        assert "card-constraints" in self._by_name(specs)

    def test_card_constraints_compiled_with_stable_ids(self):
        from overbae.services.eval.card_compiler import stable_ref_id
        from overbae.services.eval.roles import roles_for_spec

        card = copy.deepcopy(CODEBASE_CARD)
        card["constraints"] = [
            {
                "rule": "at most 3 calls",
                "type": "budget",
                "params": {"max_calls": 3},
                "provenance": [],
            },
        ]
        card["tool_protocol"] = [
            {
                "rule": "sample before report",
                "kind": "ordering",
                "tools": ["sample_rows", "report_stage"],
                "provenance": [],
            },
        ]
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        by_name = self._by_name(specs)
        if "card-constraints" not in by_name:
            pytest.skip("card-constraints is not compiled")
        spec = by_name["card-constraints"]
        assert spec.kind == "deterministic"
        assert spec.scope == "trajectory"
        assert spec.config["check"] == "card_constraints"
        assert spec.config["declared_tools"] == ["sample_rows", "get_rows_by_id", "report_stage"]
        entries = {e["id"]: e for e in spec.config["constraints"]}
        assert stable_ref_id("at most 3 calls") in entries
        proto = entries[stable_ref_id("sample before report")]
        assert proto["type"] == "ordering"
        assert proto["tools"] == ["sample_rows", "report_stage"]
        assert proto["params"] == {}
        assert "trace_scoring" in roles_for_spec(spec)

    def test_protocol_params_pass_through_and_json_format_inferred(self):
        from overbae.services.eval.card_compiler import stable_ref_id

        card = copy.deepcopy(CODEBASE_CARD)
        card["constraints"] = [
            {
                "rule": "return JSON only",
                "type": "output_format",
                "params": {},
                "provenance": [],
            },
        ]
        card["tool_protocol"] = [
            {
                "rule": "post_decision exactly once",
                "kind": "precondition",
                "tools": ["post_decision"],
                "params": {"tool": "post_decision", "max_calls": 1},
                "provenance": [],
            },
        ]
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        spec = self._card_constraints(specs)
        entries = {e["id"]: e for e in spec.config["constraints"]}
        assert entries[stable_ref_id("return JSON only")]["params"] == {"format": "json"}
        proto = entries[stable_ref_id("post_decision exactly once")]
        assert proto["params"] == {"tool": "post_decision", "max_calls": 1}
        assert proto["tools"] == ["post_decision"]

    def test_last_position_inferred_from_ordering_rule(self):
        from overbae.services.eval.card_compiler import stable_ref_id

        card = copy.deepcopy(CODEBASE_CARD)
        card["constraints"] = []
        card["tool_protocol"] = [
            {
                "rule": "emit is the last tool call",
                "kind": "ordering",
                "tools": ["report_stage"],
                "params": {},
                "provenance": [],
            },
        ]
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        spec = self._card_constraints(specs)
        entry = {e["id"]: e for e in spec.config["constraints"]}[
            stable_ref_id("emit is the last tool call")
        ]
        assert entry["params"]["position"] == "last"
        assert entry["tools"] == ["report_stage"]

    def test_situational_rule_compiles_when_field_from_schema(self):
        from overbae.services.eval.card_compiler import stable_ref_id

        card = copy.deepcopy(CODEBASE_CARD)
        card["output_fields"] = {**card["output_fields"], "clause": "string — cited clause id"}
        card["constraints"] = []
        card["tool_protocol"] = [
            {
                "rule": "lookup before citing a clause",
                "kind": "precondition",
                "tools": ["get_rows_by_id"],
                "params": {},
                "provenance": [],
            },
        ]
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        spec = self._card_constraints(specs)
        entry = {e["id"]: e for e in spec.config["constraints"]}[
            stable_ref_id("lookup before citing a clause")
        ]
        assert entry["params"]["when_field"] == "clause"

    def test_situational_rule_compiles_when_values_from_declared_enum(self):
        from overbae.services.eval.card_compiler import stable_ref_id

        card = copy.deepcopy(CODEBASE_CARD)
        card["output_fields"] = {
            **card["output_fields"],
            "verdict": "enum: pass|fail|defer — closed outcome",
        }
        card["constraints"] = []
        card["tool_protocol"] = [
            {
                "rule": "sample_rows before pass or defer",
                "kind": "precondition",
                "tools": ["sample_rows"],
                "params": {},
                "provenance": [],
            },
        ]
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        spec = self._card_constraints(specs)
        entry = {e["id"]: e for e in spec.config["constraints"]}[
            stable_ref_id("sample_rows before pass or defer")
        ]
        assert entry["params"]["when_field"] == "verdict"
        assert set(entry["params"]["when_values"]) == {"pass", "defer"}

    def test_situational_rule_compiles_when_values_from_json_schema_enum(self):
        from overbae.services.eval.card_compiler import stable_ref_id

        card = copy.deepcopy(CODEBASE_CARD)
        card["output_fields"] = {**card["output_fields"], "verdict": "string — outcome"}
        card["output_schema"] = {
            **card["output_schema"],
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "fail", "defer"]},
            },
        }
        card["constraints"] = []
        card["tool_protocol"] = [
            {
                "rule": "sample_rows before pass or defer",
                "kind": "precondition",
                "tools": ["sample_rows"],
                "params": {},
                "provenance": [],
            },
        ]
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        spec = self._card_constraints(specs)
        entry = {e["id"]: e for e in spec.config["constraints"]}[
            stable_ref_id("sample_rows before pass or defer")
        ]
        assert entry["params"]["when_field"] == "verdict"
        assert set(entry["params"]["when_values"]) == {"pass", "defer"}

    def test_two_schema_fields_in_a_situational_rule_are_not_guessed(self):
        from overbae.services.eval.card_compiler import stable_ref_id

        card = copy.deepcopy(CODEBASE_CARD)
        card["output_fields"] = {
            **card["output_fields"],
            "claimed_amount": "number",
            "reporting_currency": "string",
        }
        card["constraints"] = []
        card["tool_protocol"] = [
            {
                "rule": "get_rate before converting claimed_amount into reporting_currency",
                "kind": "precondition",
                "tools": ["get_rows_by_id"],
                "params": {},
                "provenance": [],
            },
        ]
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        spec = self._card_constraints(specs)
        entry = {e["id"]: e for e in spec.config["constraints"]}[
            stable_ref_id("get_rate before converting claimed_amount into reporting_currency")
        ]
        assert "when_field" not in entry["params"]
        assert "when_fields_differ" not in entry["params"]

    def test_declared_when_fields_differ_is_forwarded(self):
        from overbae.services.eval.card_compiler import stable_ref_id

        card = copy.deepcopy(CODEBASE_CARD)
        card["output_fields"] = {
            **card["output_fields"],
            "claim_currency": "string",
            "reporting_currency": "string",
        }
        card["constraints"] = []
        card["tool_protocol"] = [
            {
                "rule": "get_rate before converting claim_currency into reporting_currency",
                "kind": "precondition",
                "tools": ["get_rows_by_id"],
                "params": {"when_fields_differ": ["claim_currency", "reporting_currency"]},
                "provenance": [],
            },
        ]
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        spec = self._card_constraints(specs)
        entry = {e["id"]: e for e in spec.config["constraints"]}[
            stable_ref_id("get_rate before converting claim_currency into reporting_currency")
        ]
        assert entry["params"]["when_fields_differ"] == [
            "claim_currency",
            "reporting_currency",
        ]
        assert "when_field" not in entry["params"]

    def test_tool_spec_arguments_compile_as_constraint_entries(self):
        from overbae.services.eval.card_compiler import stable_ref_id

        card = copy.deepcopy(CODEBASE_CARD)
        card["tool_spec"] = [
            {
                "name": "report_stage",
                "arguments": [
                    {"name": "stage", "type": "string", "required": True},
                    {
                        "name": "count",
                        "type": "number|null",
                        "required": True,
                        "description": "row count",
                    },
                    {
                        "name": "as_of",
                        "type": "string",
                        "required": False,
                        "description": "snapshot date YYYY-MM-DD",
                    },
                ],
            },
            {"name": "sample_rows", "purpose": ""},
        ]
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        spec = self._card_constraints(specs)
        entry = {e["id"]: e for e in spec.config["constraints"]}[
            stable_ref_id("report_stage arguments match the declared schema")
        ]
        assert entry["type"] == "tool_arguments"
        assert entry["params"]["tool"] == "report_stage"
        by_name = {a["name"]: a for a in entry["params"]["arguments"]}
        assert by_name["stage"]["required"] is True
        assert "kind" not in by_name["stage"]
        assert by_name["count"]["kind"] == "number"
        assert by_name["count"]["nullable"] is True
        assert by_name["as_of"]["kind"] == "date"
        assert by_name["as_of"]["required"] is False
        ids = {e["id"] for e in spec.config["constraints"]}
        assert stable_ref_id("sample_rows arguments match the declared schema") not in ids

    def test_card_without_constraints_compiles_no_constraints_spec(self):
        specs = card_compiler.compile_card_evaluators(_grounding())
        assert "card-constraints" not in self._by_name(specs)
        managed = card_compiler.compile_managed_card_evaluators(_grounding())
        assert "card-constraints" not in {s.name for s in managed}
        assert "tool-vocabulary-selection" not in {s.name for s in managed}

    def _properties_card(self, properties, output_fields=None):
        card = copy.deepcopy(CODEBASE_CARD)
        card["output_schema"]["properties"] = properties
        if output_fields is not None:
            card["output_fields"] = output_fields
        return card

    def test_schema_conformance_is_not_compiled(self):
        card = self._properties_card(
            {"confidence": "float in [0, 1] expressing extraction certainty"},
            output_fields={},
        )
        specs = card_compiler.compile_card_evaluators(_grounding(codebase_card=card))
        managed = card_compiler.compile_managed_card_evaluators(_grounding(codebase_card=card))
        assert "output-schema-field-conformance" not in self._by_name(specs)
        assert "output-schema-field-conformance" not in {s.name for s in managed}

    def test_cardless_grounding_mints_no_managed_evaluators(self):
        assert card_compiler.compile_managed_card_evaluators(_grounding(codebase_card=None)) == []

    def test_spine_total_rows_grounding_regex(self):
        specs = card_compiler.compile_card_evaluators(_grounding())
        spec = self._by_name(specs)["summary-rows-authoritative"]
        assert spec.config["pattern"] == r'"rows"\s*:\s*159\b'
        assert spec.provenance.source == "dataset_card.volume_and_tokens.total_rows"

    def test_reference_rollups_emitted_with_blockers(self):
        specs = card_compiler.compile_card_evaluators(_grounding())
        named = self._by_name(specs)
        rouge = named["reference-similarity-rouge-l"]
        cosine = named["reference-similarity-embedding-cosine"]
        for spec in (rouge, cosine):
            assert spec.kind == "statistical"
            assert spec.scope == "dataset"
            assert spec.requires_reference is True
            assert spec.provenance.surface_area == "reference"
            assert spec.provenance.blocked_on == ["a non-empty expected_output on every row"]
        assert rouge.config == {"metric": "rouge_l"}
        assert cosine.config == {"metric": "embedding_cosine"}

    def test_format_gate_carries_report_baseline(self):
        specs = card_compiler.compile_card_evaluators(_grounding())
        spec = self._by_name(specs)["output-parses-as-json"]
        assert spec.config == {"check": "json_schema_valid"}
        assert spec.provenance.baseline_match_rate == 0.8239
        assert spec.provenance.source == "report.format_compliance"

    def test_fallback_card_skips_pattern_compilation_keeps_spine(self):
        card = copy.deepcopy(DATASET_CARD)
        card["_fallback"] = True
        specs = card_compiler.compile_card_evaluators(_grounding(dataset_card=card))
        sources = {spec.provenance.source for spec in specs}
        assert not any(s.startswith("dataset_card.failure_modes") for s in sources)
        assert not any(s.startswith("dataset_card.quality_signals") for s in sources)
        assert "dataset_card.volume_and_tokens.total_rows" in sources
        assert any(s.startswith("dataset_card.io_mapping") for s in sources)
        assert "codebase_card.output_fields" in sources

    def test_no_grounding_compiles_nothing(self):
        specs = card_compiler.compile_card_evaluators(EvalGroundingContext())
        assert specs == []

    def test_dedup_against_existing_evaluator_inventory(self):
        existing = SimpleNamespace(
            config={
                "provenance": {
                    "source": "dataset_card.failure_modes[0]",
                    "data_version": DATA_VERSION,
                }
            }
        )
        specs = card_compiler.compile_card_evaluators(_grounding(evaluator_inventory=[existing]))
        sources = {spec.provenance.source for spec in specs}
        assert "dataset_card.failure_modes[0]" not in sources
        stale = SimpleNamespace(
            config={
                "provenance": {
                    "source": "dataset_card.quality_signals[0]",
                    "data_version": "other-version",
                }
            }
        )
        specs = card_compiler.compile_card_evaluators(_grounding(evaluator_inventory=[stale]))
        sources = {spec.provenance.source for spec in specs}
        assert "dataset_card.quality_signals[0]" in sources

    def test_negated_regex_passes_only_when_pattern_absent(self):
        import re

        pattern = card_compiler.negated_regex(r"dolly\.jsonl|\b15000\b")
        assert re.search(pattern, "clean output with 159 rows")
        assert not re.search(pattern, "summary says 15000 rows from dolly.jsonl")


class TestBindChecklistFields:
    FIELDS = ["char_interval", "extraction_text", "text"]

    def test_card_output_field_names_ladder(self):
        card = {"output_schema": {"properties": {"a": {}, "b": {}}, "required_keys": ["b", "c"]}}
        assert card_compiler.card_output_field_names(card) == ["a", "b", "c"]
        assert card_compiler.card_output_field_names({"output_fields": {"x": ""}}) == ["x"]
        assert card_compiler.card_output_field_names({}) == []
        assert card_compiler.card_output_field_names(None) == []

    def test_item_naming_exactly_one_field_gets_bound(self):
        items = [{"id": "grounding", "q": "Is char_interval consistent with the source text span?"}]
        # The question names both char_interval and text ("source text span") → ambiguous.
        bound = card_compiler.bind_checklist_fields(items, self.FIELDS)
        assert bound[0].get("field", "") == ""
        one = card_compiler.bind_checklist_fields(
            [{"id": "spans", "q": "Does each char_interval match the evidence?"}], self.FIELDS
        )
        assert one[0]["field"] == "char_interval"

    def test_compound_identifier_does_not_bind_a_shorter_field(self):
        # `decision` is not a whole identifier inside `post_decision`.
        bound = card_compiler.bind_checklist_fields(
            [{"id": "uses_post_decision", "q": "Did it finish with post_decision?"}],
            ["decision", "policy_clause"],
        )
        assert bound[0].get("field", "") == ""

    def test_tool_named_item_is_not_bound_to_an_output_field(self):
        bound = card_compiler.bind_checklist_fields(
            [
                {
                    "id": "history_before_approve",
                    "q": "Was get_history called before an approve decision?",
                }
            ],
            ["decision"],
            tool_names=["get_history"],
        )
        assert bound[0].get("field", "") == ""

    def test_prepare_drops_items_a_deterministic_check_owns(self):
        card = {
            "output_fields": {
                "amount": "number — total due",
                "summary": "string — free text",
            },
            "output_schema": {"properties": {"amount": {}, "summary": {}}, "required_keys": []},
        }
        kept, dropped = card_compiler.prepare_judge_checklist(
            [
                {"id": "amount_ok", "q": "Does amount match the reference?"},
                {"id": "summary_ok", "q": "Is the summary faithful?"},
            ],
            card,
        )
        assert [i["id"] for i in kept] == ["summary_ok"]
        assert dropped == ["amount_ok"]
        assert kept[0].get("field") == "summary"

    def test_prepare_drops_pure_reference_equality_and_keeps_situational(self):
        card = {
            "output_fields": {
                "policy_clause": "string — cited clause id",
                "decision": "string — outcome",
            },
            "output_schema": {
                "properties": {"policy_clause": {}, "decision": {}},
                "required_keys": [],
            },
        }
        kept, dropped = card_compiler.prepare_judge_checklist(
            [
                {
                    "id": "clause_eq",
                    "q": (
                        "PASS only if {output.policy_clause} equals "
                        "{reference.policy_clause} (trimmed string equality)."
                    ),
                },
                {
                    "id": "reject_when_stale",
                    "q": ("If the claim is stale, PASS only if {output.decision} equals reject."),
                },
            ],
            card,
        )
        assert [i["id"] for i in kept] == ["reject_when_stale"]
        assert dropped == ["clause_eq"]

    def test_prepare_drops_items_that_restate_compiled_constraints(self):
        card = {
            "output_fields": {
                "decision": "enum: approve|partial|reject",
                "policy_clause": "string",
            },
            "output_schema": {
                "properties": {"decision": {}, "policy_clause": {}},
                "required_keys": [],
            },
            "constraints": [
                {
                    "rule": "call post_decision exactly once per claim",
                    "type": "tool_discipline",
                    "params": {"tool": "post_decision", "max_calls": 1},
                }
            ],
            "tool_protocol": [
                {
                    "kind": "precondition",
                    "rule": "lookup_policy before citing a policy_clause",
                    "tools": ["lookup_policy"],
                },
                {
                    "kind": "precondition",
                    "rule": "get_submitter_history before approve or partial",
                    "tools": ["get_submitter_history"],
                },
                {
                    "kind": "ordering",
                    "rule": "post_decision is the last tool call; stop the tool loop after it",
                    "tools": ["post_decision"],
                },
            ],
            "tool_spec": [
                {
                    "name": "post_decision",
                    "arguments": [
                        {"name": "approved_amount", "type": "number|null", "required": True}
                    ],
                }
            ],
        }
        kept, dropped = card_compiler.prepare_judge_checklist(
            [
                {
                    "id": "post_decision_called_once",
                    "q": "Is there exactly one post_decision invocation?",
                },
                {
                    "id": "stop_calling_tools_after_post_decision",
                    "q": "Does it stop calling tools after post_decision?",
                },
                {
                    "id": "post_decision_after_lookups",
                    "q": "Is post_decision issued after the lookups?",
                },
                {
                    "id": "lookup_policy_before_decision",
                    "q": "Was lookup_policy called before citing a policy_clause?",
                },
                {
                    "id": "history_before_approve_or_partial",
                    "q": "Was get_submitter_history called prior to approve or partial?",
                },
                {
                    "id": "correct_tool_arguments",
                    "q": "Are tool arguments well-formed with the expected keys?",
                },
                {
                    "id": "batch_lookups_in_single_round",
                    "q": (
                        "Were lookups batched in a single round, invoked before any post_decision?"
                    ),
                },
            ],
            card,
        )
        assert [i["id"] for i in kept] == [
            "post_decision_after_lookups",
            "batch_lookups_in_single_round",
        ]
        assert set(dropped) == {
            "post_decision_called_once",
            "stop_calling_tools_after_post_decision",
            "lookup_policy_before_decision",
            "history_before_approve_or_partial",
            "correct_tool_arguments",
        }

    def test_nested_field_names_bind_the_longest_match(self):
        # 'extraction_text' contains 'text' — the longer name binds alone, not a double hit.
        bound = card_compiler.bind_checklist_fields(
            [{"id": "verbatim", "q": "Is extraction_text copied verbatim?"}], self.FIELDS
        )
        assert bound[0]["field"] == "extraction_text"

    def test_declared_field_is_kept_and_prefixed_form_resolves(self):
        items = [
            {"id": "a", "q": "?", "field": "char_interval"},
            {"id": "b", "q": "?", "field": "output_schema.extraction_text"},
        ]
        bound = card_compiler.bind_checklist_fields(items, self.FIELDS)
        assert bound[0]["field"] == "char_interval"
        assert bound[1]["field"] == "output_schema.extraction_text"

    def test_unknown_declared_field_is_dropped_with_warning(self, caplog):
        items = [{"id": "a", "q": "?", "field": "not_a_real_field"}]
        with caplog.at_level("WARNING"):
            bound = card_compiler.bind_checklist_fields(items, self.FIELDS)
        assert bound[0]["field"] == ""
        assert any("unknown field" in r.message for r in caplog.records)

    def test_pure_and_idempotent(self):
        items = [{"id": "spans", "q": "char_interval ok?"}]
        once = card_compiler.bind_checklist_fields(items, self.FIELDS)
        assert items[0].get("field") is None
        assert card_compiler.bind_checklist_fields(once, self.FIELDS) == once

    def test_no_field_names_is_a_noop(self):
        items = [{"id": "a", "q": "char_interval ok?", "field": "bogus"}]
        assert card_compiler.bind_checklist_fields(items, []) == items

    def test_coverage_rollup_shape_and_score(self):
        grounding = _grounding()
        specs = card_compiler.compile_card_evaluators(grounding)
        coverage = card_compiler.compute_coverage(grounding, specs)
        areas = {entry["area"]: entry for entry in coverage["areas"]}
        assert set(areas) == {
            "output_contract",
            "failure_mode",
            "tool_surface",
            "cohort",
            "reference",
            "trajectory",
        }
        assert areas["trajectory"]["cells"] == []  # fixture card carries no map
        contract = areas["output_contract"]
        assert set(contract["covered"]) == {"summary", "clusters", "recommendations"}
        assert contract["uncovered"] == []
        tools = areas["tool_surface"]
        assert set(tools["cells"]) == {"sample_rows", "get_rows_by_id", "report_stage"}
        assert set(tools["covered"]) == {"sample_rows", "get_rows_by_id", "report_stage"}
        assert tools["uncovered"] == []
        failure = areas["failure_mode"]
        assert any(c.startswith("dataset: Stale upstream echoes") for c in failure["covered"])
        assert 0.0 < coverage["score"] <= 1.0
        assert all(
            set(entry["cells"]) == set(entry["covered"]) | set(entry["uncovered"])
            for entry in coverage["areas"]
        )


TRAJECTORY_MAP = [
    {
        "id": "invoice-extraction",
        "name": "Payable invoice extraction",
        "routing": "LLM extraction returns isInvoice=true",
        "sequence": ["compose email text", "LLM classify+extract", "assemble record"],
        "tools": ["analyze_email_with_llm"],
        "terminal": {"kind": "emits_record", "description": "returns the invoice record"},
        "divergences": ["non-invoice-refusal"],
        "provenance": ["python-agent/agent.py#L273-L327"],
    },
    {
        "id": "non-invoice-refusal",
        "name": "Non-invoice refusal",
        "routing": "LLM extraction returns isInvoice=false",
        "sequence": ["compose email text", "LLM classify+extract", "return None"],
        "tools": ["analyze_email_with_llm"],
        "terminal": {"kind": "returns_empty", "description": "returns None"},
        "divergences": ["invoice-extraction"],
        "provenance": ["python-agent/agent.py#L302-L304"],
    },
]


def _trajectory_card() -> dict:
    card = copy.deepcopy(CODEBASE_CARD)
    card["trajectory_map"] = copy.deepcopy(TRAJECTORY_MAP)
    return card


class TestTrajectoryMapAuthoring:
    def test_grounding_pack_renders_trajectory_section(self):
        pack = render_grounding_pack(_grounding(codebase_card=_trajectory_card()))
        assert "Trajectory map" in pack
        assert "codebase_card.trajectory_map[i]" in pack
        assert "[0] invoice-extraction — routing: LLM extraction returns isInvoice=true" in pack
        assert "compose email text -> LLM classify+extract -> assemble record" in pack
        assert "terminal: returns_empty (returns None)" in pack
        assert "diverges to: invoice-extraction" in pack

    def test_grounding_pack_omits_section_without_map(self):
        assert "Trajectory map" not in render_grounding_pack(_grounding())

    def test_coverage_cells_one_per_named_path(self):
        grounding = _grounding(codebase_card=_trajectory_card())
        cells = card_compiler._coverage_cells(grounding)
        assert cells["trajectory"] == ["invoice-extraction", "non-invoice-refusal"]

    def test_trajectory_cells_covered_by_rubric_citation(self):
        grounding = _grounding(codebase_card=_trajectory_card())
        specs = card_compiler.compile_card_evaluators(grounding)
        judge = EvaluatorSpec(
            name="Route Adherence",
            kind="llm_judge",
            scope="trajectory",
            rubric_md="1) Did the run follow invoice-extraction as its input called for?",
            provenance=_provenance(
                source="codebase_card.trajectory_map[0]", surface_area="trajectory"
            ),
        )
        coverage = card_compiler.compute_coverage(grounding, [*specs, judge])
        area = next(a for a in coverage["areas"] if a["area"] == "trajectory")
        assert "invoice-extraction" in area["covered"]
        assert "non-invoice-refusal" in area["uncovered"]

    def test_tier1_templates_carry_trajectory_instruction(self):
        from overbae.services.eval import semantic_recommender

        template = semantic_recommender._TIER1_TEMPLATE
        assert "TRAJECTORY COVERAGE" in template
        assert "ROUTE SELECTION" in template
        assert "ROUTE ADHERENCE" in template
        assert "codebase_card.trajectory_map[i]" in template
        flat = " ".join(template.split())
        assert "never as malformed/broken output" in flat
        assert "observable as TOOL CALLS" in flat
        assert '{{"output_present": true}}' in flat


class TestTier1BudgetAndFallback:
    """generate-evals runs Tier 1 synchronously in the request — it must never
    hang and must always leave Tier 0 output standing on any failure."""

    @pytest.fixture(autouse=True)
    def _neutralize_key_guard(self, monkeypatch):
        """Without a provider key ``resolve_model`` raises before ``call_llm``, so the
        monkeypatched ``call_llm`` would never run."""
        from overbae.services.eval import semantic_recommender

        monkeypatch.setattr(semantic_recommender, "resolve_model", lambda *a, **k: "test-model")

    def _judge_json(self, name: str) -> str:
        return (
            f'{{"name": "{name}", "rubric_md": "Grade the output for groundedness.", '
            '"grounding_citation": "codebase_card.success_criteria[0]"}'
        )

    def test_hung_llm_call_returns_empty_within_budget(self, monkeypatch):
        import threading
        import time

        from overbae.services.eval import semantic_recommender

        # Block forever without sleeping — the budget timeout must still abort.
        hang_gate = threading.Event()

        def hang(*args, **kwargs):
            hang_gate.wait(timeout=30)
            return "{}", {}

        monkeypatch.setattr(semantic_recommender, "call_llm", hang)
        start = time.monotonic()
        specs = semantic_recommender.author_grounded_judges(_grounding(), [], time_budget_s=0.2)
        elapsed = time.monotonic() - start
        hang_gate.set()
        assert specs == []
        assert elapsed < 1.5  # returned at the budget, not the LLM's pace

    def test_raising_llm_call_returns_empty(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        def boom(*args, **kwargs):
            raise RuntimeError("Error calling LLM: provider exploded")

        monkeypatch.setattr(semantic_recommender, "call_llm", boom)
        assert semantic_recommender.author_grounded_judges(_grounding(), []) == []

    def test_truncated_json_salvages_complete_judges(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        # One complete judge, then a max_tokens cut-off mid-object.
        truncated = (
            '{"evals": [' + self._judge_json("Ref Groundedness") + ', {"name": "cut-off-judg'
        )

        monkeypatch.setattr(semantic_recommender, "call_llm", lambda *a, **k: (truncated, {}))
        specs = semantic_recommender.author_grounded_judges(_grounding(), [])
        assert [s.name for s in specs] == ["Ref Groundedness"]
        assert specs[0].kind == "llm_judge"

    def test_valid_response_still_parses(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        raw = f'{{"evals": [{self._judge_json("Output Specificity")}]}}'
        monkeypatch.setattr(semantic_recommender, "call_llm", lambda *a, **k: (raw, {}))
        specs = semantic_recommender.author_grounded_judges(_grounding(), [])
        assert [s.name for s in specs] == ["Output Specificity"]

    def test_leaked_internal_symbol_is_sanitized_from_authored_spec(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        raw = (
            '{"evals": [{"name": "json-contract", '
            '"rubric_md": "Valid JSON with the _LLM_OUTPUT_KEYS present.", '
            '"checklist": [{"id": "rows", "q": "Does summary.rows equal {total_rows}?", '
            '"gate": true}], '
            '"grounding_citation": "codebase_card.success_criteria[0]"}]}'
        )
        monkeypatch.setattr(semantic_recommender, "call_llm", lambda *a, **k: (raw, {}))
        specs = semantic_recommender.author_grounded_judges(_grounding(), [])
        assert len(specs) == 1
        spec = specs[0]
        assert "_LLM_OUTPUT_KEYS" not in spec.rubric_md
        assert "{total_rows}" not in spec.checklist[0].q
        assert set(spec.config["_sanitized_tokens"]) == {"_LLM_OUTPUT_KEYS", "{total_rows}"}

    def test_authored_judge_roles_pinned_to_suite(self, monkeypatch):
        from overbae.services.eval import semantic_recommender
        from overbae.services.eval.roles import roles_for_spec

        raw = (
            '{"evals": ['
            '{"name": "Output Quality", "rubric_md": "Grade the tone of {{output}}.", '
            '"grounding_citation": "codebase_card.success_criteria[0]"}, '
            '{"name": "Ref Correctness", "rubric_md": "Compare to the reference.", '
            '"requires_reference": true, '
            '"grounding_citation": "dataset_card.reference"}'
            "]}"
        )
        monkeypatch.setattr(semantic_recommender, "call_llm", lambda *a, **k: (raw, {}))
        specs = {s.name: s for s in semantic_recommender.author_grounded_judges(_grounding(), [])}
        assert specs["Output Quality"].applicable_roles == ["generative"]
        assert roles_for_spec(specs["Output Quality"]) == ("generative",)
        # A reference-graded judge stays generative-only (no reference on a live trace).
        assert roles_for_spec(specs["Ref Correctness"]) == ("generative",)

    def test_authored_judge_binds_referenced_placeholders(self, monkeypatch):
        from overbae.services.eval import semantic_recommender
        from overbae.services.eval.specs import VARIABLE_SOURCES

        raw = (
            '{"evals": [{"name": "contract-semantics", '
            '"rubric_md": "Does {{summary}} agree with {{output}} and cite {{reference}}?", '
            '"variable_mapping": [{"var": "output", "source": "output"}], '
            '"grounding_citation": "codebase_card.success_criteria[0]"}]}'
        )
        monkeypatch.setattr(semantic_recommender, "call_llm", lambda *a, **k: (raw, {}))
        specs = semantic_recommender.author_grounded_judges(_grounding(), [])
        assert len(specs) == 1
        bound = {entry.var: entry for entry in specs[0].variable_mapping}
        assert {"summary", "output", "reference"} <= set(bound)
        assert all(entry.source in VARIABLE_SOURCES for entry in specs[0].variable_mapping)
        assert bound["summary"].source == "output"
        assert bound["reference"].source == "reference"

    def test_discovery_prompt_shows_variables_like_author_path(self, monkeypatch):
        from types import SimpleNamespace

        from overbae.services.eval import semantic_recommender
        from overbae.services.eval.rubric_compiler import (
            build_judge_prompt_display,
            compose_judge_evaluator_kwargs,
        )

        evaluation_prompt = "Does {{summary}} faithfully condense {{output}}?"
        raw = (
            '{"evals": [{"name": "summary-faithfulness", '
            f'"rubric_md": "{evaluation_prompt}", '
            '"grounding_citation": "codebase_card.success_criteria[0]"}]}'
        )
        monkeypatch.setattr(semantic_recommender, "call_llm", lambda *a, **k: (raw, {}))
        spec = semantic_recommender.author_grounded_judges(_grounding(), [])[0]

        assert "{{summary}}" in spec.rubric_md
        assert "{{output}}" in spec.rubric_md

        mapped = {entry.var for entry in spec.variable_mapping}
        assert {"summary", "output", "input", "reference"} <= mapped

        # The composed prompt is what the serializer's ``judge_prompt`` shows the UI.
        kwargs = spec.to_evaluator_kwargs()
        evaluator = SimpleNamespace(
            kind="llm_judge",
            rubric_md=kwargs["rubric_md"],
            checklist=kwargs["checklist"],
            variable_mapping=kwargs["variable_mapping"],
            score_type=kwargs["score_type"],
            choices=[],
            score_min=kwargs["score_min"],
            score_max=kwargs["score_max"],
            pass_threshold=kwargs["pass_threshold"],
        )
        prompt = build_judge_prompt_display(evaluator)
        assert "{{summary}}" in prompt
        for var in ("summary", "output", "input", "reference"):
            assert f"{var}:" in prompt  # Inputs section lists each mapped variable

        # Parity: align_variable_mapping ⇄ _mapping_from_prompt must bind the same set.
        author_kwargs = compose_judge_evaluator_kwargs(
            {
                "project": None,
                "capability": None,
                "name": "summary-faithfulness",
                "evaluation_prompt": evaluation_prompt,
                "score_type": "numeric",
            }
        )
        author_vars = {entry["var"] for entry in author_kwargs["variable_mapping"]}
        assert {"summary", "output", "input", "reference"} <= author_vars

    def test_per_call_timeout_passed_to_llm(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        seen: dict = {}

        def capture(*args, **kwargs):
            seen.update(kwargs)
            return '{"evals": []}', {}

        monkeypatch.setattr(semantic_recommender, "call_llm", capture)
        semantic_recommender.author_grounded_judges(_grounding(), [])
        assert seen["request_kwargs"]["timeout"] == semantic_recommender._TIER1_LLM_TIMEOUT_S
        assert seen["max_tokens"] == semantic_recommender._TIER1_MAX_TOKENS


class TestManagedRubricSanitation:
    def test_no_managed_rubric_ships_a_leaked_placeholder(self):
        from overbae.services.eval.managed import MANAGED_EVALUATORS
        from overbae.services.eval.sanitation import contains_leaked_token

        offenders: list[str] = []
        for tpl in MANAGED_EVALUATORS:
            name = tpl.get("name", "?")
            if contains_leaked_token(tpl.get("rubric_md", "")):
                offenders.append(f"{name}: rubric_md")
            for item in tpl.get("checklist", []):
                if contains_leaked_token(item.get("q", "")):
                    offenders.append(f"{name}: checklist[{item.get('id')}]")
        assert offenders == [], f"managed rubrics leak internal tokens: {offenders}"


class TestGroundingPack:
    def test_pack_renders_all_sections(self):
        pack = render_grounding_pack(
            _grounding(), sample_rows=[{"input": "q", "expected_output": "a"}]
        )
        assert "Dataset capability card" in pack
        assert "Capability capability card" in pack
        assert "Workshop analysis report signals" in pack
        assert "Representative rows" in pack
        assert "No pk-lf-* credentials in metadata" in pack  # quality signal lifted verbatim
        assert "passes parse_and_validate" in pack  # success criterion lifted verbatim

    def test_pack_empty_without_grounding(self):
        assert render_grounding_pack(EvalGroundingContext()) == ""

    def test_codebase_failure_modes_render_with_citation_paths(self):
        pack = render_grounding_pack(_grounding())
        assert "Invents row counts instead of reading dataset_facts.json" in pack
        assert "codebase_card.failure_modes[" in pack
        assert "codebase_card.expected_output.quality_signals[" in pack
        assert "codebase_card.success_criteria[" in pack


class TestExampleUnitRendering:
    def test_renders_concrete_output_keys_and_caveat(self):
        from overbae.services.eval.grounding import render_example_unit

        unit = SimpleNamespace(
            trajectory={"final_output": '{"summary": {"rows": 22}, "recommendations": ["x"]}'},
            expected={"label": "ok"},
            structured={"tool_graph": {"nodes": [{"tool": "sample_rows"}]}},
        )
        rendered = render_example_unit(unit)
        assert "Example sample" in rendered
        assert "summary" in rendered and "recommendations" in rendered
        assert "sample_rows" in rendered
        assert "not dataset-wide totals" in rendered
        assert "can NEVER traverse" in rendered

    def test_plain_text_output_is_described_as_text(self):
        from overbae.services.eval.grounding import render_example_unit

        unit = SimpleNamespace(
            trajectory={"final_output": "The capital of France is Paris."},
            expected=None,
            structured={},
        )
        rendered = render_example_unit(unit)
        assert "plain text" in rendered
        assert "reference (source 'reference') is absent" in rendered

    def test_none_unit_renders_empty(self):
        from overbae.services.eval.grounding import render_example_unit

        assert render_example_unit(None) == ""

    def test_nested_reference_paths_and_surface_table(self):
        # The judge author must bind OBSERVED nested gold leaves, not card-flat guesses.
        from overbae.services.eval.grounding import render_example_unit

        unit = SimpleNamespace(
            trajectory={"final_output": '{"amount": 100.5, "vendor": "Acme"}'},
            expected={
                "amount": {"value": 100.5, "currency": "USD", "raw": "$100.50"},
                "vendor": "Acme Corp",
            },
            structured={},
        )
        rendered = render_example_unit(unit, output_field_names=["amount", "vendor", "tax"])
        assert "$.amount.value" in rendered
        assert "$.amount.currency" in rendered
        assert "Surface / shape table" in rendered
        assert "NESTED" in rendered
        assert "never the flat '$.amount'" in rendered
        assert "reference '$.vendor' (str)" in rendered
        assert "tax → no matching reference key observed" in rendered

    def test_surface_table_omitted_without_structured_reference(self):
        from overbae.services.eval.grounding import render_example_unit

        unit = SimpleNamespace(
            trajectory={"final_output": "plain text answer"},
            expected="a plain-string reference",
            structured={},
        )
        rendered = render_example_unit(unit, output_field_names=["amount"])
        assert "Surface / shape table" not in rendered


class TestTier1PromptContract:
    def test_to_spec_preserves_authored_name(self):
        from overbae.services.eval import semantic_recommender

        judge = semantic_recommender._AuthoredJudge(
            name="  Failure Mode Avoidance  ",
            rubric_md="Prefer totals over subtotals.",
            grounding_citation="codebase_card.failure_modes[0]",
        )
        spec = semantic_recommender._to_spec(judge, _grounding())
        assert spec is not None
        assert spec.name == "Failure Mode Avoidance"

    def test_to_spec_drops_items_a_deterministic_check_owns(self):
        from overbae.services.eval import semantic_recommender

        card = copy.deepcopy(CODEBASE_CARD)
        card["output_fields"] = {
            **card["output_fields"],
            "amount": "number — total due",
            "summary": "string — free text",
        }
        card["output_schema"] = {
            "required_keys": ["amount", "summary"],
            "properties": {"amount": {}, "summary": {}},
        }
        judge = semantic_recommender._AuthoredJudge(
            name="Task Success",
            rubric_md="Grade the row.",
            checklist=[
                {"id": "amount_ok", "q": "Does amount match the reference?", "weight": 1.0},
                {"id": "summary_ok", "q": "Is the summary faithful?", "weight": 1.0},
            ],
        )
        spec = semantic_recommender._to_spec(judge, _grounding(codebase_card=card))
        assert spec is not None
        assert [i.id for i in spec.checklist] == ["summary_ok"]
        assert spec.config["_dropped_items"] == ["amount_ok"]

    def test_to_spec_discards_a_judge_that_only_restated_exact_fields(self):
        from overbae.services.eval import semantic_recommender

        card = copy.deepcopy(CODEBASE_CARD)
        card["output_fields"] = {"amount": "number — total due"}
        card["output_schema"] = {"required_keys": ["amount"], "properties": {"amount": {}}}
        judge = semantic_recommender._AuthoredJudge(
            name="Amount Extraction",
            rubric_md="Grade amount.",
            checklist=[{"id": "amount_ok", "q": "Does amount match?", "weight": 1.0}],
        )
        assert semantic_recommender._to_spec(judge, _grounding(codebase_card=card)) is None

    def test_coverage_block_names_fields_already_checked_exactly(self):
        from overbae.services.eval.semantic_recommender import _coverage_block
        from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance

        provenance = SpecProvenance(
            source="test", generator="tier1_llm@v1", surface_area="output_contract"
        )
        field_check = EvaluatorSpec(
            name="output-field-accuracy",
            kind="deterministic",
            scope="final_output",
            description="d",
            rubric_md="r",
            checklist=[{"id": "q1", "q": "?", "weight": 1.0}],
            provenance=provenance,
            config={
                "check": "canonical_fields",
                "fields": [
                    {"kind": "number", "name": "amount"},
                    {"kind": "date", "name": "dueDate"},
                ],
            },
        )
        calibration = EvaluatorSpec(
            name="confidence-calibration",
            kind="statistical",
            scope="dataset",
            description="d",
            rubric_md="r",
            checklist=[{"id": "q1", "q": "?", "weight": 1.0}],
            provenance=provenance,
            config={"metric": "calibration", "prediction_field": "confidence"},
        )
        block = _coverage_block([field_check, calibration, field_check])
        assert "already covers: amount, dueDate" in block
        assert "already covers: confidence" in block
        assert block.count("output-field-accuracy") == 1
        assert "checked EXACTLY and for free" in block
        assert "never graded per sample" in block


class TestTier1GenerativeAuthoring:
    @pytest.fixture(autouse=True)
    def _neutralize_key_guard(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        monkeypatch.setattr(semantic_recommender, "resolve_model", lambda *a, **k: "test-model")

    def test_author_tier1_suites_returns_generative_only(self, monkeypatch):
        from overbae.services.eval import semantic_recommender
        from overbae.services.eval.roles import GENERATIVE, roles_for_spec

        calls: list[str] = []

        def fake_call(prompt, *, system_prompt="", **kwargs):
            calls.append("generative")
            raw = (
                '{"evals": [{"name": "Task Success", '
                '"rubric_md": "Compare {{output}} to {{reference}}.", '
                '"requires_reference": true, '
                '"grounding_citation": "codebase_card.success_criteria[0]"}]}'
            )
            return raw, {}

        monkeypatch.setattr(semantic_recommender, "call_llm", fake_call)
        specs, suites_timed_out, _allocation = semantic_recommender.author_tier1_suites(
            _grounding(), []
        )
        assert suites_timed_out == []
        assert calls == ["generative"]
        assert len(specs) == 1
        gen = specs[0]
        assert gen.name == "Task Success"
        assert gen.requires_reference is True
        assert GENERATIVE in roles_for_spec(gen)
        assert gen.applicable_roles == ["generative"]

    def test_author_tier1_suites_names_timed_out_suite(self, monkeypatch):
        from concurrent.futures import TimeoutError as FutureTimeoutError

        from overbae.services.eval import semantic_recommender

        def fake_author(grounding, tier0, **kw):
            raise FutureTimeoutError

        monkeypatch.setattr(semantic_recommender, "author_grounded_judges", fake_author)
        specs, timed_out, _allocation = semantic_recommender.author_tier1_suites(_grounding(), [])
        assert timed_out == [semantic_recommender.SUITE_GENERATIVE]
        assert specs == []

    def test_author_grounded_judges_raise_on_timeout(self, monkeypatch):
        import time
        from concurrent.futures import TimeoutError as FutureTimeoutError

        from overbae.services.eval import semantic_recommender

        def slow_llm(*a, **k):
            time.sleep(0.5)
            return '{"evals": []}'

        monkeypatch.setattr(semantic_recommender, "_call_authoring_llm", slow_llm)
        with pytest.raises(FutureTimeoutError):
            semantic_recommender.author_grounded_judges(
                _grounding(), [], time_budget_s=0.05, raise_on_timeout=True
            )
        # Default behaviour still swallows the timeout for standalone callers.
        assert (
            semantic_recommender.author_grounded_judges(_grounding(), [], time_budget_s=0.05) == []
        )

    def test_authoring_schemas_have_no_freeform_objects(self):
        # Strict structured output 400s on any object node without explicit properties.
        from overbae.services.eval import semantic_recommender
        from overbae.services.eval.rubric_compiler import _Checklist

        def walk(node):
            if isinstance(node, dict):
                assert not (node.get("type") == "object" and "properties" not in node), node
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(semantic_recommender._AuthoredJudgeSuite.model_json_schema())
        walk(_Checklist.model_json_schema())

    def test_authored_checklist_carries_applies_when(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        raw = (
            '{"evals": [{"name": "Branch Conditional Quality", '
            '"rubric_md": "Judge {{output}} for branch-specific quality.", '
            '"checklist": ['
            '{"id": "invoice_fields", "q": "Are invoice fields complete?", '
            '"applies_when": {"context_equals": {"key": "is_invoice", "value": true}}}, '
            '{"id": "coherent", "q": "Is the output coherent?"}], '
            '"grounding_citation": "codebase_card.success_criteria[0]"}]}'
        )
        monkeypatch.setattr(semantic_recommender, "call_llm", lambda *a, **k: (raw, {}))
        specs = semantic_recommender.author_grounded_judges(_grounding(), [])
        assert len(specs) == 1
        by_id = {item.id: item for item in specs[0].checklist}
        assert by_id["invoice_fields"].applies_when == {
            "context_equals": {"key": "is_invoice", "value": True}
        }
        assert by_id["coherent"].applies_when is None
        # None must not serialize into the persisted checklist payload.
        payload = specs[0].to_evaluator_kwargs()["checklist"]
        assert "applies_when" not in next(i for i in payload if i["id"] == "coherent")

    def test_align_variable_mapping_can_omit_reference_seed(self):
        from overbae.services.eval.rubric_compiler import align_variable_mapping

        with_ref = align_variable_mapping("Grade {{output}}.", [], [])
        assert "reference" in {e["var"] for e in with_ref}
        without = align_variable_mapping(
            "Grade {{output}} vs {{reference}}.",
            [],
            [{"var": "reference", "source": "reference"}],
            seed_reference=False,
        )
        assert "reference" not in {e["var"] for e in without}
        assert "reference" not in {e["source"] for e in without}
        assert {"input", "output"} <= {e["var"] for e in without}


@pytest.mark.django_db
class TestPreloadTier1GenerativeOnly:
    def test_preload_persists_tier1_on_generative_only(self, monkeypatch):
        from overbae.models import Capability, EvalSet, EvalSetMember, Project
        from overbae.services.eval import card_compiler, semantic_recommender
        from overbae.services.eval.eval_set import generate_and_preload_default_set
        from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance

        project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
        capability = Capability.objects.create(
            project=project,
            name="A",
            slug=f"a-{uuid.uuid4().hex[:8]}",
            improvement_metadata={
                "capability_card": {
                    "task": "Extract fields.",
                    "failure_modes": ["Invents amounts"],
                    "success_criteria": ["Amounts match the source"],
                }
            },
        )
        prov = SpecProvenance(
            source="codebase_card.success_criteria[0]",
            generator="tier1_llm@v1",
            surface_area="output_contract",
        )
        gen_spec = EvaluatorSpec(
            name="Task Success",
            kind="llm_judge",
            scope="final_output",
            rubric_md="Compare output to reference.",
            requires_reference=True,
            applicable_roles=["generative"],
            provenance=prov,
        )
        monkeypatch.setattr(card_compiler, "compile_card_evaluators", lambda g: [])
        monkeypatch.setattr(card_compiler, "compile_managed_card_evaluators", lambda g: [])
        monkeypatch.setattr(
            semantic_recommender,
            "author_tier1_suites",
            lambda g, tier0, **kw: ([gen_spec], [], None),
        )

        result = generate_and_preload_default_set(capability)
        eval_set = EvalSet.objects.get(capability=capability, name="Default")
        gen_names = set(
            eval_set.members.filter(role=EvalSetMember.Role.GENERATIVE).values_list(
                "evaluator__name", flat=True
            )
        )
        trace_names = set(
            eval_set.members.filter(role=EvalSetMember.Role.TRACE_SCORING).values_list(
                "evaluator__name", flat=True
            )
        )
        assert "Task Success" in gen_names
        assert "Task Success" not in trace_names
        assert "Card Criteria Compliance" not in gen_names
        assert "Card Criteria Compliance" not in trace_names
        assert trace_names == set()
        assert result["empty_tier1_suites"] == []
        assert result["floor_judge"] is False
        assert result["generative"] >= 1
        assert result["trace_scoring"] == 0


@pytest.mark.django_db
class TestAgentPathExampleUnit:
    def _capability(self):
        from overbae.models import Capability, Project

        project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
        return Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
        )

    def test_capability_only_grounding_samples_richest_linked_dataset(self):
        from conftest import frozen_dataset

        from overbae.models import Dataset
        from overbae.services.eval import semantic_recommender

        capability = self._capability()
        Dataset.objects.create(
            capability=capability, project=capability.project, name="empty"
        )  # never run — must not win
        frozen_dataset(
            capability.project,
            [
                {
                    "input": "Invoice #42, total due $100.50 from Acme Corp",
                    "expected_output": {
                        "amount": {"value": 100.5, "currency": "USD", "raw": "$100.50"},
                        "vendor": "Acme Corp",
                    },
                }
            ],
            capability=capability,
            name="rich",
        )
        grounding = _grounding(dataset=None, capability=capability)
        rendered = semantic_recommender._render_example_unit_section(grounding)
        assert "Example sample" in rendered
        assert "$.amount.value" in rendered
        assert "Surface / shape table" in rendered

    def test_capability_with_no_dataset_rows_renders_empty(self):
        from overbae.models import Dataset
        from overbae.services.eval import semantic_recommender

        capability = self._capability()
        Dataset.objects.create(capability=capability, project=capability.project, name="empty")
        grounding = _grounding(dataset=None, capability=capability)
        assert semantic_recommender._render_example_unit_section(grounding) == ""


@pytest.mark.django_db
class TestWorkshopFactsCollection:
    def test_v4_report_profile_surfaces_as_dataset_facts(self):
        from overbae.services.eval.grounding import (
            EvalGroundingContext,
            _extract_reference_context,
        )

        report = {
            "version": 4,
            "profile": {
                "_internal": "noise that must be stripped",
                "rows": 15000,
                "tokens": {"input": {"p50": 42.0}},
            },
        }
        ctx = _extract_reference_context(EvalGroundingContext(report=report))
        assert ctx["dataset_facts"]["rows"] == 15000
        assert "_internal" not in ctx["dataset_facts"]

    def test_card_spine_and_report_profile_merge(self):
        from overbae.services.eval.grounding import (
            EvalGroundingContext,
            _extract_reference_context,
        )

        grounding = EvalGroundingContext(
            dataset_card=copy.deepcopy(DATASET_CARD),
            report={"version": 4, "profile": {"rows": 159}},
        )
        ctx = _extract_reference_context(grounding)
        facts = ctx["dataset_facts"]
        assert facts["rows"] == 159  # from the run profile
        assert facts["volume_and_tokens"] == {"total_rows": 159}  # from the card spine

    def test_legacy_report_yields_no_facts(self):
        from overbae.services.eval.grounding import _workshop_facts_from_report

        assert _workshop_facts_from_report(None) is None
        assert _workshop_facts_from_report({"eval_readiness": {"score": 1}}) is None


@pytest.mark.django_db
class TestResolveGrounding:
    def _dataset(self, *, with_capability: bool = True, capability_metadata: dict | None = None):
        from overbae.models import Capability, Dataset, Project

        project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
        capability = None
        if with_capability:
            capability = Capability.objects.create(
                project=project,
                name="A",
                slug=f"a-{uuid.uuid4().hex[:8]}",
                improvement_metadata=capability_metadata or {},
            )
        return Dataset.objects.create(capability=capability, project=project, name="d")

    def test_dataset_without_capability_has_no_codebase_card(self):
        from overbae.services.eval.grounding import resolve_grounding

        ctx = resolve_grounding(self._dataset(with_capability=False))
        assert ctx.dataset_card is None
        assert ctx.codebase_card is None
        assert ctx.report is None

    def test_codebase_card_comes_from_the_synced_capability_card(self):
        from overbae.services.eval.grounding import resolve_grounding

        dataset = self._dataset(capability_metadata={"capability_card": CODEBASE_CARD})
        assert resolve_grounding(dataset).codebase_card == CODEBASE_CARD

    def test_capability_without_card_has_no_codebase_card(self):
        from overbae.services.eval.grounding import resolve_grounding

        dataset = self._dataset(capability_metadata={})
        assert resolve_grounding(dataset).codebase_card is None

    def test_evaluator_inventory_scoped_to_project(self):
        from overbae.models import Evaluator

        dataset = self._dataset()
        from overbae.services.eval.grounding import resolve_grounding

        Evaluator.objects.create(
            project=dataset.project, name="mine", kind="deterministic", config={}
        )
        Evaluator.objects.create(project=None, name="managed", kind="llm_judge", is_managed=True)
        ctx = resolve_grounding(dataset)
        names = {e.name for e in ctx.evaluator_inventory}
        assert {"mine", "managed"} <= names


# Two-layer card: output_fields (harness deliverable) drops the model layer's isBillable,
# tool_spec is empty, and trajectory_map names an internal callable.
DUAL_LAYER_CARD = {
    "_fallback": False,
    "task": "Extract billing records from inbound documents.",
    "output_fields": {
        "recordId": "string — composite key",
        "amount": "MoneyAmount|null — {value, currency, raw}",
        "vendor": "string",
        "dueDate": "string|null — YYYY-MM-DD",
        "summary": "string",
        "confidence": "float in [0, 1]",
    },
    "output_schema": {
        "required_keys": ["isBillable", "confidence"],
        "properties": {
            "isBillable": "boolean (required)",
            "confidence": "float in [0, 1]",
            "amount": "number|null",
            "currency": "string|null",
            "vendor": "string|null",
        },
    },
    "tool_spec": [],
    "success_criteria": ["Extracts the billed amount"],
    "expected_output": {"description": "An array of billing records."},
    "trajectory_map": [
        {
            "id": "extract-record",
            "tools": ["classify_with_llm"],
            "routing": "classifier returns billable",
            "sequence": ["classify", "assemble record"],
            "terminal": {"kind": "emits_record"},
        },
        {
            "id": "refuse-non-billable",
            "tools": ["classify_with_llm"],
            "routing": "classifier returns not billable",
            "sequence": ["classify", "return None"],
            "terminal": {"kind": "returns_empty"},
        },
    ],
}


class TestSurfaceBindingValidation:
    """Every repair rule derives from card facts, never from a specific agent."""

    def _enforce(
        self, *, checklist=None, variable_mapping=None, rubric_md="", card=None, live=True
    ):
        from overbae.services.eval.surface_binding import enforce_surface_bindings

        return enforce_surface_bindings(
            checklist=checklist or [],
            variable_mapping=variable_mapping or [],
            rubric_md=rubric_md,
            card=copy.deepcopy(DUAL_LAYER_CARD) if card is None else card,
            grades_live_surface=live,
        )

    # applies_when keyed on an output field can never hold as a context lookup.
    @pytest.mark.parametrize(
        "pred",
        [
            {"context_equals": {"key": "output.isBillable", "value": True}},
            {"context_equals": {"key": "isBillable", "value": True}},
            {"context_present": "output.isBillable"},
            {"context_equals": {"key": "$.isBillable", "value": True}},
            {"context_equals": {"key": "output_present", "value": True}},
            {"context_present": "output_present"},
        ],
    )
    def test_output_field_applies_when_rebound_to_output_present(self, pred):
        items = [
            {"id": "amt", "q": "Is {output.amount.value} the total due?", "applies_when": pred}
        ]
        checklist, _, notes, drop = self._enforce(checklist=items)
        assert not drop
        assert checklist[0]["applies_when"] == {"output_present": True}
        assert any("rebound to output_present" in n for n in notes)

    def test_output_field_applies_when_with_falsy_value_made_unconditional(self):
        pred = {"context_equals": {"key": "output.isBillable", "value": False}}
        items = [{"id": "amt", "q": "Is the record omitted?", "applies_when": pred}]
        checklist, _, notes, _ = self._enforce(checklist=items)
        assert "applies_when" not in checklist[0]
        assert any("made unconditional" in n for n in notes)

    def test_real_runtime_context_key_untouched(self):
        pred = {"context_equals": {"key": "document_type", "value": "statement"}}
        items = [{"id": "amt", "q": "Is the amount right?", "applies_when": pred}]
        checklist, _, notes, _ = self._enforce(checklist=items)
        assert checklist[0]["applies_when"] == pred
        assert notes == []

    def test_undeclared_callable_requirement_dropped(self):
        items = [
            {"id": "route", "q": "Did the run take a declared route?"},
            {"id": "tool", "q": "Was 'classify_with_llm' called as a tool?"},
        ]
        checklist, _, notes, drop = self._enforce(checklist=items)
        assert [i["id"] for i in checklist] == ["route"]
        assert not drop
        assert any("classify_with_llm" in n and "tool_spec" in n for n in notes)

    def test_declared_tool_requirement_kept(self):
        card = copy.deepcopy(DUAL_LAYER_CARD)
        card["tool_spec"] = [{"name": "classify_with_llm", "purpose": ""}]
        items = [{"id": "tool", "q": "Was 'classify_with_llm' called as a tool?"}]
        checklist, _, notes, _ = self._enforce(checklist=items, card=card)
        assert [i["id"] for i in checklist] == ["tool"]
        assert notes == []

    @pytest.mark.parametrize(
        "item",
        [
            {"id": "cls", "q": "Does {output.isBillable} reflect the document?"},
            {"id": "cls", "q": "Is the classification right?", "field": "isBillable"},
            {"id": "cls", "q": "Is $.isBillable set for billable docs?"},
            {"id": "cls", "q": "Does output.isBillable reflect the document?"},
            # sanitation remnant: "{output}" stripped and the gap closed
            {"id": "cls", "q": "When.isBillable is true, is the record kept?"},
            {"id": "cls", "q": "1).isBillable must be true only for billable docs."},
            {"id": "cls", "q": "Is {output.currency} an ISO code?"},
        ],
    )
    def test_trace_item_bound_to_model_only_field_dropped(self, item):
        items = [dict(item), {"id": "amt", "q": "Is {output.amount.value} the total?"}]
        checklist, _, notes, drop = self._enforce(checklist=items)
        assert [i["id"] for i in checklist] == ["amt"]
        assert not drop
        assert any("model-layer-only" in n for n in notes)

    def test_generative_item_may_grade_model_layer(self):
        items = [{"id": "cls", "q": "Does {output.isBillable} reflect the document?"}]
        checklist, _, notes, drop = self._enforce(checklist=items, live=False)
        assert [i["id"] for i in checklist] == ["cls"]
        assert notes == [] and not drop

    def test_generative_item_bound_to_harness_only_field_dropped(self):
        items = [
            {"id": "rid", "q": "Is {output.recordId} the composite key?"},
            {"id": "cls", "q": "Does {output.isBillable} reflect the document?"},
        ]
        checklist, _, notes, drop = self._enforce(checklist=items, live=False)
        assert [i["id"] for i in checklist] == ["cls"]
        assert not drop
        assert any("harness-only" in n for n in notes)

    def test_generative_metadata_jsonpath_outside_runner_vocab_dropped(self):
        items = [
            {"id": "route", "q": "When {metadata.report_type} is deep, is coverage broader?"},
            {"id": "cls", "q": "Does {output.isBillable} reflect the document?"},
        ]
        mapping = [
            {"var": "report_type", "source": "metadata", "jsonpath": "$.report_type"},
            {"var": "output", "source": "output", "jsonpath": ""},
        ]
        checklist, kept, notes, drop = self._enforce(
            checklist=items, variable_mapping=mapping, live=False
        )
        assert [i["id"] for i in checklist] == ["cls"]
        assert [e["var"] for e in kept] == ["output"]
        assert not drop
        assert any("report_type" in n for n in notes)

    def test_harness_field_refs_kept_on_trace_surface(self):
        items = [{"id": "amt", "q": "Is {output.amount.value} correct?", "field": "amount"}]
        checklist, _, notes, _ = self._enforce(checklist=items)
        assert [i["id"] for i in checklist] == ["amt"]
        assert notes == []

    def test_model_only_name_nested_under_harness_head_kept(self):
        # Only the path HEAD binds a surface: amount.currency lives inside the
        # harness amount object even though top-level currency is model-only.
        items = [{"id": "cur", "q": "Is {output.amount.currency} an ISO code?"}]
        checklist, _, notes, _ = self._enforce(checklist=items)
        assert [i["id"] for i in checklist] == ["cur"]
        assert notes == []

    def test_variable_mapping_model_only_jsonpath_dropped(self):
        mapping = [
            {"var": "flag", "source": "output", "jsonpath": "$.isBillable"},
            {"var": "amount_value", "source": "output", "jsonpath": "$.amount.value"},
            {"var": "input", "source": "input", "jsonpath": ""},
        ]
        _, kept, notes, _ = self._enforce(variable_mapping=mapping)
        assert [e["var"] for e in kept] == ["amount_value", "input"]
        assert any("model-layer-only" in n for n in notes)

    def test_spec_dropped_when_every_item_misbinds(self):
        items = [{"id": "cls", "q": "Does {output.isBillable} reflect the document?"}]
        _, _, notes, drop = self._enforce(checklist=items)
        assert drop
        assert any("every checklist item was dropped" in n for n in notes)

    def test_rubric_only_spec_bound_to_model_layer_dropped(self):
        _, _, notes, drop = self._enforce(rubric_md="Grade {output.isBillable} for accuracy.")
        assert drop

    def test_single_layer_card_has_no_model_only_fields(self):
        card = copy.deepcopy(DUAL_LAYER_CARD)
        card["output_fields"] = {}
        items = [{"id": "cls", "q": "Does {output.isBillable} reflect the document?"}]
        checklist, _, notes, drop = self._enforce(checklist=items, card=card)
        assert [i["id"] for i in checklist] == ["cls"]
        assert not drop

    def test_cardless_grounding_is_a_noop(self):
        items = [{"id": "x", "q": "Is {output.anything} fine?"}]
        checklist, mapping, notes, drop = self._enforce(checklist=items, card={})
        assert checklist == items and notes == [] and not drop


class TestSurfaceBindingInAuthoringPipeline:
    """The validator runs inside ``_to_spec``, so a misbound authored judge is
    repaired or dropped before it can persist."""

    @pytest.fixture(autouse=True)
    def _neutralize_key_guard(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        monkeypatch.setattr(semantic_recommender, "resolve_model", lambda *a, **k: "test-model")

    def _authored(self, raw, monkeypatch, suite="generative"):
        from overbae.services.eval import semantic_recommender

        monkeypatch.setattr(semantic_recommender, "call_llm", lambda *a, **k: (raw, {}))
        return semantic_recommender.author_grounded_judges(
            _grounding(codebase_card=copy.deepcopy(DUAL_LAYER_CARD)), [], suite=suite
        )

    def test_output_field_applies_when_repaired_and_recorded(self, monkeypatch):
        raw = (
            '{"evals": [{"name": "Amount Extraction", '
            '"rubric_md": "Judge {{output}} amount extraction.", '
            '"checklist": [{"id": "amt", "q": "Is the billed total due rather than a line-item subtotal?", '
            '"applies_when": {"context_equals": {"key": "output.isBillable", "value": true}}}], '
            '"grounding_citation": "codebase_card.success_criteria[0]"}]}'
        )
        specs = self._authored(raw, monkeypatch)
        assert len(specs) == 1
        assert specs[0].checklist[0].applies_when == {"output_present": True}
        assert any("output-field binding" in n for n in specs[0].config["_surface_repairs"])

    def test_generative_judge_keeps_model_layer_checklist(self, monkeypatch):
        """Tier-1 authors generative judges only; model-layer fields are valid there."""
        raw = (
            '{"evals": [{"name": "Classification Flag", '
            '"rubric_md": "Judge the flag.", '
            '"checklist": [{"id": "cls", "q": "When the document is a bill, is {output.isBillable} true?"}], '
            '"grounding_citation": "codebase_card.success_criteria[0]"}]}'
        )
        specs = self._authored(raw, monkeypatch)
        assert len(specs) == 1
        assert specs[0].applicable_roles == ["generative"]
        assert specs[0].checklist[0].q == (
            "When the document is a bill, is {output.isBillable} true?"
        )

    def test_authored_output_present_leaf_survives_to_spec(self, monkeypatch):
        raw = (
            '{"evals": [{"name": "Amount Extraction", '
            '"rubric_md": "Judge {{output}} amount extraction.", '
            '"checklist": [{"id": "amt", "q": "Is the billed total due rather than a line-item subtotal?", '
            '"applies_when": {"output_present": true}}], '
            '"grounding_citation": "codebase_card.success_criteria[0]"}]}'
        )
        specs = self._authored(raw, monkeypatch)
        assert len(specs) == 1
        assert specs[0].checklist[0].applies_when == {"output_present": True}

    def test_grounding_pack_declares_tool_surface_and_layers(self):
        pack = render_grounding_pack(_grounding(codebase_card=copy.deepcopy(DUAL_LAYER_CARD)))
        assert "Declared tools (tool_spec): NONE" in pack
        assert "internal model layer" in pack
        assert "LIVE deliverable surface" in pack
        assert "internal callables" in pack
        # returns_empty terminals must carry the empty-shape equivalence the judge grades against.
        assert "ANY empty deliverable ('', [], {}, null) IS this terminal" in pack


_CONFIDENCE_CALIBRATION_ITEMS = [
    "Confidence score calibration: higher when explicit invoice cues exist; lower when evidence is weak",
    "4) Confidence score in [0,1] and calibrated to evidence — does its magnitude reflect the evidence strength",
    "4) confidence spread reflects evidence strength — Does {output.confidence} vary appropriately (not always 0.9+)",
]
_CONFIDENCE_CONTRACT = "{output.confidence} is a number in [0,1]"
_MECHANICAL_AMOUNT = "Compare {output.amount} to {reference.amount} within abs tol 0.01"
_AMOUNT_DUE_JUDGEMENT = (
    "amount matches an explicit 'amount due' line in the email rather than a subtotal"
)


def _authored_judge(name: str, questions: list[str], **kwargs):
    from overbae.services.eval import semantic_recommender

    return semantic_recommender._AuthoredJudge(
        name=name,
        rubric_md=kwargs.pop("rubric_md", "Grade the output."),
        checklist=[{"id": f"i{i}", "q": q} for i, q in enumerate(questions)],
        grounding_citation="codebase_card.success_criteria[0]",
        **kwargs,
    )


def _field_coverage(*names: str):
    return EvaluatorSpec(
        name="output-field-accuracy",
        kind="deterministic",
        config={
            "check": "canonical_fields",
            "fields": [{"name": n, "kind": "number"} for n in names],
        },
        provenance=_provenance(source="codebase_card.output_fields", generator="card_compiler@v1"),
    )


class TestAuthoredItemFilter:
    def test_whole_blob_anchor_survives_authoring(self):
        from overbae.services.eval import semantic_recommender

        spec = semantic_recommender._to_spec(
            _authored_judge("Groundedness", ["Is the claim present in {input}?"]),
            _grounding(),
        )
        assert spec is not None
        assert spec.checklist[0].q == "Is the claim present in {input}?"
        assert "_sanitized_tokens" not in spec.config

    def test_confidence_calibration_items_are_dropped(self):
        from overbae.services.eval import semantic_recommender

        questions = [*_CONFIDENCE_CALIBRATION_ITEMS, _CONFIDENCE_CONTRACT]
        spec = semantic_recommender._to_spec(
            _authored_judge("Task Success", questions), _grounding()
        )
        assert spec is not None
        assert [i.q for i in spec.checklist] == [_CONFIDENCE_CONTRACT]
        assert spec.config["_dropped_items"] == list(_CONFIDENCE_CALIBRATION_ITEMS)

    def test_mechanical_covered_field_compare_is_dropped(self):
        from overbae.services.eval import semantic_recommender

        spec = semantic_recommender._to_spec(
            _authored_judge("Task Success", [_MECHANICAL_AMOUNT, _AMOUNT_DUE_JUDGEMENT]),
            _grounding(),
            covered_fields={"amount"},
        )
        assert spec is not None
        assert [i.q for i in spec.checklist] == [_AMOUNT_DUE_JUDGEMENT]
        assert spec.config["_dropped_items"] == [_MECHANICAL_AMOUNT]

    def test_emptied_judge_is_not_an_evaluator(self):
        from overbae.services.eval import semantic_recommender

        spec = semantic_recommender._to_spec(
            _authored_judge("Task Success", [_MECHANICAL_AMOUNT, *_CONFIDENCE_CALIBRATION_ITEMS]),
            _grounding(),
            covered_fields={"amount"},
        )
        assert spec is None

    @pytest.fixture(autouse=True)
    def _neutralize_key_guard(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        monkeypatch.setattr(semantic_recommender, "resolve_model", lambda *a, **k: "test-model")

    def test_coverage_from_tier0_reaches_the_filter(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        raw = (
            '{"evals": [{"name": "Task Success", "rubric_md": "Grade the extraction.", '
            '"checklist": ['
            f'{{"id": "amt", "q": "{_MECHANICAL_AMOUNT}"}}, '
            f'{{"id": "due", "q": "{_AMOUNT_DUE_JUDGEMENT}"}}'
            '], "grounding_citation": "codebase_card.success_criteria[0]"}]}'
        )
        monkeypatch.setattr(semantic_recommender, "call_llm", lambda *a, **k: (raw, {}))
        specs = semantic_recommender.author_grounded_judges(
            _grounding(), [_field_coverage("amount")]
        )
        assert len(specs) == 1
        assert [i.q for i in specs[0].checklist] == [_AMOUNT_DUE_JUDGEMENT]

    def test_emptied_authored_judge_is_omitted_from_the_suite(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        raw = (
            '{"evals": [{"name": "Task Success", "rubric_md": "Grade the extraction.", '
            '"checklist": ['
            f'{{"id": "amt", "q": "{_MECHANICAL_AMOUNT}"}}'
            '], "grounding_citation": "codebase_card.success_criteria[0]"}]}'
        )
        monkeypatch.setattr(semantic_recommender, "call_llm", lambda *a, **k: (raw, {}))
        specs = semantic_recommender.author_grounded_judges(
            _grounding(), [_field_coverage("amount")]
        )
        assert specs == []

    def test_vendor_compare_is_dropped_once_string_fields_are_covered(self):
        from overbae.services.eval import semantic_recommender
        from overbae.services.eval.card_compiler import canonical_output_fields
        from overbae.services.eval.semantic_recommender import covered_field_names

        declared = {
            "amount": "number|null — total amount due as a number without currency symbols",
            "confidence": (
                "float in [0,1] — model confidence in the full triage + extraction decision"
            ),
            "currency": "string|null — ISO currency code (USD, GBP, EUR) when known",
            "dueDate": "string|null — payment due date as YYYY-MM-DD when known",
            "invoiceNumber": "string|null — invoice or bill reference number when present",
            "isInvoice": (
                "boolean — true only for unpaid/payable invoices, bills, or statements "
                "with amount owed"
            ),
            "summary": "string|null — brief description of the invoice or email",
            "vendor": "string|null — vendor or sender name when known",
        }
        fields = canonical_output_fields({"output_fields": declared})
        coverage = EvaluatorSpec(
            name="output-field-accuracy",
            kind="deterministic",
            config={"check": "canonical_fields", "fields": fields},
            provenance=_provenance(
                source="codebase_card.output_fields", generator="card_compiler@v1"
            ),
        )
        covered = covered_field_names(coverage)
        assert "vendor" in covered
        assert "summary" not in covered
        spec = semantic_recommender._to_spec(
            _authored_judge(
                "Extraction Faithfulness",
                ["Compare {output.vendor} to {reference.vendor}"],
            ),
            _grounding(),
            covered_fields=covered,
        )
        assert spec is None

    def test_dropped_field_compares_leave_the_rubric(self):
        from overbae.services.eval import semantic_recommender

        summary = "summary is relevant to the email"
        authored = (
            "1. Compare {output.isInvoice} to {reference.isInvoice}\n"
            "2. Compare {output.vendor} to {reference.vendor}\n"
            "3. Compare {output.invoiceNumber} to {reference.invoiceNumber}\n"
            "4. Compare {output.amount} to {reference.amount}\n"
            "5. Compare {output.dueDate} to {reference.dueDate}\n"
            f"6. {summary}"
        )
        questions = [
            "Compare {output.isInvoice} to {reference.isInvoice}",
            "Compare {output.vendor} to {reference.vendor}",
            "Compare {output.invoiceNumber} to {reference.invoiceNumber}",
            "Compare {output.amount} to {reference.amount}",
            "Compare {output.dueDate} to {reference.dueDate}",
            summary,
        ]
        spec = semantic_recommender._to_spec(
            _authored_judge("Task Success", questions, rubric_md=authored),
            _grounding(),
            covered_fields={"isInvoice", "vendor", "invoiceNumber", "amount", "dueDate"},
        )
        assert spec is not None
        assert summary in spec.rubric_md
        assert "Compare {output.isInvoice}" not in spec.rubric_md
        assert "reference.isInvoice" not in spec.rubric_md

    def test_unfiltered_judge_keeps_its_authored_rubric(self):
        from overbae.services.eval import semantic_recommender

        authored = "Grade whether the extraction is faithful to the email."
        spec = semantic_recommender._to_spec(
            _authored_judge(
                "Groundedness",
                ["Is the claim present in {input}?"],
                rubric_md=authored,
            ),
            _grounding(),
        )
        assert spec is not None
        assert spec.rubric_md == authored


_FMA_ITEMS = [
    "Marks paid receipts, newsletters, or shipping-only notices as non-invoice",
    "Amount matches an explicit 'amount due'/'total'/'balance due' line rather than a subtotal/tax line",
    "Does not invent vendor, amount, or due date absent from the email",
    "Confidence is a decimal in [0.0, 1.0], not a percentage",
]
_QS_ITEMS = [
    "isInvoice=false for paid receipts, newsletters, and shipping-only notices",
    "Amount corresponds to an explicit 'amount due'/'total'/'balance due' line",
    "dueDate is YYYY-MM-DD when present or null when absent",
    "No invented vendor, amount, or due date that cannot be grounded in the email",
]
_SUMMARY_ITEM = "summary is a brief relevant description of the invoice or email"


def _cluster_spec(name: str, questions: list[str]):
    from overbae.services.eval.specs import TIER1_GENERATOR, SpecProvenance

    return EvaluatorSpec(
        name=name,
        kind="llm_judge",
        rubric_md="Grade the output.",
        checklist=[{"id": f"i{i}", "q": q} for i, q in enumerate(questions)],
        provenance=SpecProvenance(
            source="codebase_card.failure_modes[0]",
            generator=TIER1_GENERATOR,
            surface_area="failure_mode",
        ),
    )


class TestChecklistCluster:
    def test_later_cluster_duplicate_is_dropped(self):
        from overbae.services.eval.semantic_recommender import overlapping_prior

        fma = _cluster_spec("Failure Mode Avoidance", _FMA_ITEMS)
        qs = _cluster_spec("Quality Signals", _QS_ITEMS)
        hit = overlapping_prior(qs, [fma])
        assert hit is not None
        assert hit[0].name == "Failure Mode Avoidance"
        assert overlapping_prior(fma, [qs]) is not None

    def test_distinct_summary_judge_survives_beside_failure_modes(self):
        from overbae.services.eval.semantic_recommender import overlapping_prior

        fma = _cluster_spec("Failure Mode Avoidance", _FMA_ITEMS)
        success = _cluster_spec("Task Success", [_SUMMARY_ITEM])
        assert overlapping_prior(success, [fma]) is None
        assert overlapping_prior(fma, [success]) is None

    def test_empty_checklists_are_not_a_cluster(self):
        from overbae.services.eval.semantic_recommender import overlapping_prior

        first = _cluster_spec("Output Quality", [])
        second = _cluster_spec("Ref Correctness", [])
        assert overlapping_prior(second, [first]) is None

    @pytest.fixture(autouse=True)
    def _neutralize_key_guard(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        monkeypatch.setattr(semantic_recommender, "resolve_model", lambda *a, **k: "test-model")

    def test_authored_suite_keeps_the_first_of_a_cluster(self, monkeypatch):
        from overbae.services.eval import semantic_recommender

        payload = {
            "evals": [
                {
                    "name": "Failure Mode Avoidance",
                    "rubric_md": "Grade failure modes.",
                    "checklist": [{"id": f"f{i}", "q": q} for i, q in enumerate(_FMA_ITEMS)],
                    "grounding_citation": "codebase_card.failure_modes[0]",
                },
                {
                    "name": "Quality Signals",
                    "rubric_md": "Grade quality signals.",
                    "checklist": [{"id": f"q{i}", "q": q} for i, q in enumerate(_QS_ITEMS)],
                    "grounding_citation": "codebase_card.quality_signals[0]",
                },
                {
                    "name": "Task Success",
                    "rubric_md": "Grade task success.",
                    "checklist": [{"id": "s0", "q": _SUMMARY_ITEM}],
                    "grounding_citation": "codebase_card.success_criteria[0]",
                },
            ]
        }
        monkeypatch.setattr(
            semantic_recommender, "call_llm", lambda *a, **k: (json.dumps(payload), {})
        )
        specs = semantic_recommender.author_grounded_judges(_grounding(), [])
        assert [s.name for s in specs] == ["Failure Mode Avoidance", "Task Success"]
