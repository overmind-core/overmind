"""Evidence-requirement classification + evaluator×mode compatibility.

Only ``harness_artifact`` is mode-incompatible: it needs the capability's assembled
output, which generate mode (model-only + replay) cannot reproduce.
``reference`` is scorable in both modes but needs a curated golden reference in
the dataset, handled at runtime by abstain-on-absent.
"""

from __future__ import annotations

import re
from typing import Any

MODEL_OUTPUT = "model_output"
HARNESS_ARTIFACT = "harness_artifact"
TRAJECTORY = "trajectory"
REFERENCE = "reference"

# Placed in a not-applicable Score's ``sub_scores`` so aggregation counts it
# apart from genuine abstains and from real 0.0 failures.
NOT_APPLICABLE_MARKER = "_not_applicable"

_TRAJECTORY_SOURCES = frozenset(
    {
        "trajectory",
        "tool_calls",
        "tool_call",
        "tool_definitions",
        "tool_spec",
        "available_tools",
        "messages",
        "conversation",
    }
)

# Used only by the reference classifier, to tell "grades the output" apart from
# "grades a golden reference".
_OUTPUT_SOURCES = frozenset({"output", "final_output", "structured"})

# Only the live harness can assemble ``structured``. The model's own
# ``output``/``final_output`` — even stringified JSON a jsonpath traverses — is
# reproduced by a generate-mode candidate, so it stays model_output.
_HARNESS_OUTPUT_SOURCES = frozenset({"structured"})

_REFERENCE_SOURCES = frozenset({"reference", "expected", "expected_output", "ground_truth"})


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _targets_harness_artifact(variable_mapping: list[dict[str, Any]]) -> bool:
    for entry in variable_mapping or []:
        if isinstance(entry, dict) and _norm(entry.get("source")) in _HARNESS_OUTPUT_SOURCES:
            return True
    return False


def infer_evidence_requirement(
    *,
    kind: str,
    scope: str,
    variable_mapping: list[dict[str, Any]] | None = None,
    requires_reference: bool = False,
    config: dict[str, Any] | None = None,
) -> str:
    """Must stay deterministic and pure — it drives both the data-migration
    backfill and the default for newly-created evaluators."""
    vm = variable_mapping or []
    cfg = config or {}
    sources = {_norm(e.get("source")) for e in vm if isinstance(e, dict)}
    check = _norm(cfg.get("check"))

    if scope in ("trajectory", "step", "turn"):
        return TRAJECTORY
    if check == "tool_selection" or sources & _TRAJECTORY_SOURCES:
        return TRAJECTORY

    if _targets_harness_artifact(vm):
        return HARNESS_ARTIFACT

    # Reference grounding is not mode-gated; it abstains at runtime when the
    # dataset carries no reference.
    has_reference = requires_reference or bool(sources & _REFERENCE_SOURCES)
    if has_reference and (
        requires_reference or kind == "statistical" or not (sources & _OUTPUT_SOURCES)
    ):
        return REFERENCE

    return MODEL_OUTPUT


GENERATE = "generate"
EXISTING = "existing"


def is_applicable(evidence_requirement: str, mode: str) -> bool:
    return not (evidence_requirement == HARNESS_ARTIFACT and mode == GENERATE)


def not_applicable_reason(evidence_requirement: str, mode: str) -> str:
    return (
        f"not applicable: requires the capability's harness-produced output "
        f"(evidence_requirement={evidence_requirement!r}), which '{mode}' mode "
        f"(model + replay, no live harness) cannot produce — run this evaluator "
        f"in 'existing' mode over captured traces."
    )


def compatibility_warnings(
    evaluators: list[dict[str, Any]],
    variant_modes: list[dict[str, str]],
    *,
    reference_available: bool = False,
) -> list[dict[str, Any]]:
    """Non-fatal incompatibilities for run creation.

    ``evaluators`` items: ``{name, evidence_requirement, requires_reference}``.
    ``variant_modes`` items: ``{label, mode}``. ``reference_available`` suppresses
    the ``needs_reference`` warning — those evaluators will resolve real evidence
    from the dataset's collected context instead of abstaining.
    """
    warnings: list[dict[str, Any]] = []
    generate_variants = [v for v in variant_modes if v.get("mode") == GENERATE]
    for ev in evaluators:
        requirement = ev.get("evidence_requirement") or MODEL_OUTPUT
        name = ev.get("name") or "evaluator"
        for variant in generate_variants:
            if not is_applicable(requirement, GENERATE):
                warnings.append(
                    {
                        "evaluator": name,
                        "variant": variant.get("label", ""),
                        "variant_mode": GENERATE,
                        "evidence_requirement": requirement,
                        "severity": "incompatible",
                        "message": (
                            f"'{name}' grades the capability's harness-produced output, which "
                            f"generate-mode variant '{variant.get('label', '')}' cannot produce; "
                            f"it will be marked not-applicable. Score it in existing mode."
                        ),
                    }
                )
        # Only the reference-CLASSIFIED evaluators truly depend on a golden
        # reference; one that merely carries ``requires_reference`` but classifies
        # as trajectory/harness gets its grounding elsewhere.
        if requirement == REFERENCE and not reference_available:
            warnings.append(
                {
                    "evaluator": name,
                    "variant": "",
                    "variant_mode": "",
                    "evidence_requirement": requirement,
                    "severity": "needs_reference",
                    "message": (
                        f"'{name}' grades against a curated golden reference; it will "
                        f"abstain on samples whose dataset carries no reference."
                    ),
                }
            )
    return warnings
