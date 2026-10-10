from __future__ import annotations

import hashlib
import json

from django.db.models import Prefetch

from overbae.models import Behaviour, BehaviourVersion
from overbae.services.codebase.flow import build_capability_flow
from overbae.services.datasets import paths, store
from overbae.services.datasets.profile import profile_records

CONSUMERS = {
    "decision_training": {
        "reads": ["decision"],
        "target": "Interpret targets through Workshop exploration and source/user evidence. Record per-family target meaning and evidence in retained transformation code and output metadata; known supervision uses decision.target_semantics and decision.target_provenance. Unknown meaning stays unknown. Preserve full probability distributions, tied maxima and weights; mean-only ordinal supervision uses target_mean plus an explicit option_values scale. Never invent votes or collapse soft targets to argmax. Decision/Jev training consumes typed decisions, not assistant answer messages.",
        "wire": "decision is an object with state (text), question (nonempty text), kind (choice/noul/score), options (2–255 distinct strings), target_probabilities (matching finite probabilities summing to one), and optional positive weight, target_semantics and target_provenance. Alternatively score rows use target_mean, increasing option_values and target_semantics=ordinal_mean, without target_probabilities. Known meanings are categorical_gold, annotator_distribution, posterior, ordinal_histogram, pairwise_preference and teacher_distribution. Preserve annotation counts in target_provenance when known. Flat noul targets expand to No/Yes options (false/true semantic order) and [1-p,p].",
        "model_specific": "Training uses the pinned Unsloth decision encoder and retains question spans and named-option mappings. Keep model tokens out of Workshop data. Preserve source/license/group identities outside decision for auditing and splitting.",
    },
    "decision_evaluation": {
        "reads": ["input.decision"],
        "target": "expected_output.probabilities retains the full publisher distribution outside the request; mean-only references use expected_output={mean, values} without inventing a distribution. Use a native probability evaluator; chat generation is not a probability prediction.",
        "wire": "input.decision contains only state, question, kind and options. Option order must match reference probabilities. Blank state is structurally valid; whether the question supplies enough evidence is a separate semantic claim.",
        "quality": "Validate schema, probability range/sum/dimensions and preservation over every row. Do not normalize, harden, deduplicate, rebalance or drop valid blank states automatically. Publisher references are not verified truth; semantic checks are opt-in and uncertainty remains visible.",
    },
    "sft": {
        "reads": ["messages", "tools (optional)"],
        "target": "Assistant turns in each conversation; coverage columns are not model input.",
        "instructions": "Preserve task-specific system/developer prompts. Binding a capability does not authorise replacing them.",
        "wire": "messages and tools are arrays, tool schemas are objects; tool-call arguments are JSON strings. JSON-encoded arrays are normalised at the shared handoff.",
        "model_specific": "Training applies the selected model's chat template, tokenization, loss masks and context checks. The workshop neither selects a model nor repairs data during training.",
    },
    "model_evaluation": {
        "reads": ["input.messages", "input.tools (optional)"],
        "target": "expected_output is the grading reference; model_expected_output separates a model response from an application-level reference when both exist.",
        "execution": "Calls the model, not the application. It cannot resolve record IDs or fetch documents. Include all evidence before the target; never copy the target into the request.",
        "tools": "Tool schemas advertise callable interfaces, not implementations. Tool replay needs recorded calls and results; it does not execute application tools. Missing replay evidence must be reported, never invented.",
    },
    "shared": {
        "workshop_execution": "The calling coding agent authors transformations and semantic checks. Save deterministic pipelines or import locally produced rows bound to source cells and fingerprints. derive_dataset copies the complete source; explore_dataset measures sampling allocations. Select samples externally and import their pinned source identities. Sampling does not establish train/eval disjointness.",
        "task_scope": "Infer distinct source tasks from prompts, payloads, targets and tool context. A selected capability is the intended target, not evidence that every row already performs it. Transform each family using supported evidence; a prompt-only relabel is not a task transformation.",
        "split": "Keep case, content, conversation and synthetic-seed families disjoint across train/eval. Do not combine evidence across held-out boundaries.",
        "readiness": "Technical format errors require repair. Semantic quality findings are advisory; apply supported repairs, then allow progression with remaining warnings.",
    },
}


def workshop_context(dataset, *, measure_missing=True) -> dict:
    versions = dataset.versions()
    profiles = {}
    for name, cell in (("source", dataset.source), ("active", dataset.active_cell)):
        if cell is None or not cell.ran:
            continue
        if name == "active" and profiles.get("source", {}).get("cell") == str(cell.id):
            continue
        path = paths.cell_path(dataset.id, cell.id)
        if not path.exists():
            profiles[name] = {"cell": str(cell.id), "error": "Frame unavailable"}
            continue
        profiles[name] = {
            "cell": str(cell.id),
            "version": versions.get(cell.id),
            "fingerprint": cell.fingerprint,
            **(
                cell.stats.get("preparation_profile")
                or (
                    profile_records(store.iter_rows(path))
                    if measure_missing
                    else {
                        "status": "unmeasured",
                        "next_action": "explore_dataset",
                        "source_cell": str(cell.id),
                    }
                )
            ),
        }
    return {
        "consumers": CONSUMERS,
        "profiles": profiles,
        "preparation_plan": dataset.preparation_plan,
    }


def preparation_context(capability) -> dict:
    if capability is None:
        return {"capability": None, "behaviours": []}
    flow = build_capability_flow(capability)
    metadata = capability.improvement_metadata or {}
    card = metadata.get("capability_card") or {}
    behaviours = capability.behaviours.filter(status=Behaviour.Status.ACTIVE).prefetch_related(
        Prefetch(
            "versions",
            queryset=BehaviourVersion.objects.order_by("-created_at")[:1],
            to_attr="latest_versions",
        )
    )
    return {
        "capability": str(capability.id),
        "name": capability.name,
        "description": capability.description,
        "decision_logic": capability.decision_logic,
        "policy": capability.policy_markdown,
        "output_schema": card.get("output_schema") or {},
        **{
            key: flow.get(key)
            for key in (
                "task",
                "domain",
                "modality",
                "system_prompt",
                "input_schema",
                "expected_output",
                "tool_spec",
                "success_criteria",
                "failure_modes",
            )
        },
        "behaviours": [
            {
                "key": behaviour.key,
                "name": behaviour.display_name,
                "version": str(versions[0].id) if versions else None,
                "contract": versions[0].contract if versions else {},
            }
            for behaviour in behaviours
            for versions in [behaviour.latest_versions]
        ],
    }


def context_fingerprint(capability) -> str:
    raw = json.dumps(preparation_context(capability), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()
