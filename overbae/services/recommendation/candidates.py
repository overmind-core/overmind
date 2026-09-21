"""One candidate row: what a model would cost, how it would be trained, and how it grades.

Every number is derived — pricing, policy and the committed benchmark artifact. Nothing
here reads the database.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from overbae.modal.training_type import dataset_training_type
from overbae.services.benchmarks import artifact
from overbae.services.benchmarks.schema import BenchmarkScore, Provenance
from overbae.services.datasets.text import approx_tokens_from_chars
from overbae.services.finetuning_policy import qlora_learning_rate
from overbae.services.finetuning_pricing import (
    estimate_training_cost,
    estimate_training_time_s,
    humanize_duration,
)

from .catalog import active_backend, catalog_backend, gpu_config
from .hyperparams import compute_hyperparams, hyperparam_provenance
from .ranking import MIN_COHORT, Ranked

# Every score links to the benchmark's own publication, so the label names the host that
# publication sits on. Anything unlisted shows its bare domain.
_REFERENCE_LABELS = {
    "arxiv.org": "arXiv",
    "github.com": "GitHub",
    "huggingface.co": "HuggingFace",
}


def dataset_total_tokens(stats: Mapping[str, Any]) -> int:
    """Whole-dataset token estimate from the stored per-row character averages."""
    num_examples = int(stats.get("num_examples") or 0)
    avg_chars = int(stats.get("avg_input_chars") or 0) + int(stats.get("avg_output_chars") or 0)
    return approx_tokens_from_chars(num_examples * avg_chars)


def build_candidate(
    stats: Mapping[str, Any],
    *,
    model_entry: dict[str, Any],
    tier: str,
    ranked: Ranked | None = None,
) -> dict[str, Any]:
    """Everything the wizard shows for one (dataset, model) pair.

    ``ranked`` absent means the model was never graded — the row states that rather than
    carrying a fabricated number.
    """
    backend = active_backend()
    num_examples = int(stats.get("num_examples") or 0)
    max_tokens = 0 if backend == "modal" else int(stats.get("max_token_length") or 0)

    from overbae.modal.model_registry import context_headroom

    training_type = dataset_training_type(
        model_entry, max_tokens=max_tokens or None, headroom=context_headroom("baseten")
    )
    supports_lora = training_type["lora"]["enabled"]
    supports_full = training_type["full"]["enabled"]
    if not supports_lora and not supports_full:
        raise ValueError(
            f"Model {model_entry.get('id')} supports neither LoRA nor full fine-tuning"
            + (f" for a dataset with rows up to {max_tokens:,} tokens" if max_tokens else "")
        )
    # Full only when LoRA is unsupported; the catalog also disables full on models the
    # UI and API refuse to full-tune.
    use_lora = supports_lora

    params_b = model_entry.get("total_params_b")
    if params_b is None:
        raise ValueError(
            f"Model entry {model_entry.get('id')!r} is missing 'total_params_b'. "
            "Add it to the model registry."
        )

    hyperparams = compute_hyperparams(
        num_examples,
        model_entry=model_entry,
        use_lora=use_lora,
        max_row_tokens=max_tokens,
    )
    trained_tokens = dataset_total_tokens(stats) * int(hyperparams.get("n_epochs") or 1)
    time_s = estimate_training_time_s(trained_tokens, total_params_b=params_b, use_lora=use_lora)

    return {
        "tier": tier,
        "model": model_entry["id"],
        "display_name": model_entry["display"],
        "params": model_entry["params"],
        "total_params_b": params_b,
        "context_length_sft": model_entry.get("context_length_sft"),
        "max_batch_size": model_entry.get("max_batch_size"),
        "min_batch_size": model_entry.get("min_batch_size"),
        # Standing among the graded candidates in this request — the headline, because a
        # percentile over every model the artifact tracks answers a question nobody asked.
        "match": ranked.match if ranked is not None else None,
        "match_rank": ranked.match_rank if ranked is not None else None,
        "match_pool": ranked.match_pool if ranked is not None else 0,
        # The audit trail behind that placing: the evidence grade, the same grade shrunk
        # toward the cohort prior, and the bound the order is actually taken on.
        "grade": _round(ranked.grade if ranked is not None else None),
        "adjusted_grade": _round(ranked.adjusted_grade if ranked is not None else None),
        "lower_bound": _round(ranked.lower_bound if ranked is not None else None),
        "confidence": ranked.confidence if ranked is not None else "none",
        "n_benchmarks": ranked.n_benchmarks if ranked is not None else 0,
        "skill_scores": _skill_scores(ranked),
        "evidence": evidence_rows(model_entry["id"], _graded_weights(ranked)),
        "selected": False,
        "hyperparams": hyperparams,
        "use_lora": use_lora,
        "training_type": training_type,
        # gpu_config reads the Baseten inference rows because models.json has no "modal"
        # ones; the cost estimate keeps the literal backend so Modal's branch wins.
        "gpu_config": gpu_config(model_entry["id"], catalog_backend()),
        "cost_estimate": estimate_training_cost(
            trained_tokens,
            total_params_b=params_b,
            use_lora=use_lora,
            backend=backend,
        ),
        "time_estimate": {"seconds": time_s, "human": humanize_duration(time_s)},
        # Both rates pre-computed so the frontend never re-implements the heuristic.
        "learning_rate_lora": qlora_learning_rate(params_b, num_examples, use_lora=True),
        "learning_rate_full": qlora_learning_rate(params_b, num_examples, use_lora=False),
        "hyperparam_reasons": hyperparam_provenance(
            hyperparams,
            num_examples=num_examples,
            params_b=params_b,
            use_lora=use_lora,
            backend=backend,
        ),
    }


def evidence_rows(model_id: str, weights: Mapping[str, float]) -> list[dict[str, Any]]:
    """The benchmarks behind the grade, one row per benchmark.

    Selection mirrors ``grading.skill_index`` exactly — thin cohorts dropped, and a
    lab-claimed score retired by any measured score on the same skill — so the row count
    equals the candidate's ``n_benchmarks``.
    """
    by_axis: dict[str, list[BenchmarkScore]] = defaultdict(list)
    for score in artifact.scores_for(model_id):
        if score.cohort_n < MIN_COHORT:
            continue
        for axis in (*score.skills, *score.domains):
            if axis in weights:
                by_axis[axis].append(score)

    # A benchmark tagged with several graded skills is one piece of evidence; it is
    # attributed to the skill the task weights most.
    best: dict[str, tuple[float, str, BenchmarkScore]] = {}
    for axis, scores in by_axis.items():
        measured = [s for s in scores if s.provenance == Provenance.MEASURED]
        for score in measured or scores:
            current = best.get(score.benchmark)
            if current is None or (weights[axis], axis) > (current[0], current[1]):
                best[score.benchmark] = (weights[axis], axis, score)

    rows = [
        {
            "benchmark": score.benchmark,
            "skill": axis,
            "percentile": round(score.percentile, 1),
            "cohort_n": score.cohort_n,
            "source": _reference_label(score.source_url),
            "url": score.source_url,
            "provenance": score.provenance,
        }
        for _weight, axis, score in best.values()
    ]
    rows.sort(key=lambda row: (row["provenance"] != Provenance.MEASURED, -row["percentile"]))
    return rows


def _reference_label(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return _REFERENCE_LABELS.get(host, host)


def _graded_weights(ranked: Ranked | None) -> dict[str, float]:
    """The blend the grade actually used — the task's weights renormalised over the
    skills the model has data for. An ungraded model has none, so it shows no evidence.
    """
    if ranked is None:
        return {}
    return {c.skill: c.weight for c in ranked.contributions}


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _skill_scores(ranked: Ranked | None) -> list[dict[str, Any]]:
    if ranked is None:
        return []
    return [asdict(standing) for standing in ranked.skill_standings]


__all__ = ["build_candidate", "dataset_total_tokens", "evidence_rows"]
