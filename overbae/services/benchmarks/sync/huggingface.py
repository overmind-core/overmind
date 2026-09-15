"""HuggingFace ``evalResults``, read over the raw REST API.

``HfApi.model_info(expand=["evalResults"])`` raises ``KeyError: 'id'`` and loses the whole
model whenever the Hub itself failed to parse one result entry. The REST payload carries
those entries as ``{"error": ...}`` instead, so they can be stepped over one at a time.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypedDict
from urllib.parse import urlsplit

import requests

from ..schema import Provenance
from ..taxonomy import Domain, Skill

logger = logging.getLogger(__name__)

SOURCE = "huggingface"
_API_URL = "https://huggingface.co/api/models/{model_id}?expand[]=evalResults"
_HEADERS = {"User-Agent": "overmind-benchmark-sync"}
_TIMEOUT_SECONDS = 20.0
_RETRY_DELAY_SECONDS = 2.0
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_MIN_ORG_TOKEN = 3

# Percentiles are computed per slug across every source, so a slug the leaderboard also
# carries has to use the same unit: scores land normalised to 0-1.
SCALE = "accuracy_0_1"


class BenchmarkRow(TypedDict):
    hf_model_id: str
    eval_slug: str
    raw_score: float
    scale: str
    skills: tuple[str, ...]
    domains: tuple[str, ...]
    provenance: str
    source: str
    source_url: str
    observed_at: str


Fetcher = Callable[[str], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Benchmark:
    slug: str
    skills: tuple[Skill, ...] = ()
    domains: tuple[Domain, ...] = ()
    native_max: float = 100.0


# Keyed by the Hub's ``(dataset id, task id)``. A dataset id alone is not an identity: the
# task id separates whole benchmarks from their sub-metrics, and separate splits of one
# benchmark score differently enough that merging their cohorts would misrank both.
BENCHMARKS: dict[tuple[str, str], Benchmark] = {
    ("Idavidrein/gpqa", "diamond"): Benchmark(
        "gpqa-diamond", (Skill.REASONING,), (Domain.SCIENCE,)
    ),
    ("TIGER-Lab/MMLU-Pro", "mmlu_pro"): Benchmark(
        "mmlu-pro",
        (Skill.REASONING, Skill.INSTRUCTION_FOLLOWING),
        (
            Domain.BUSINESS,
            Domain.LEGAL,
            Domain.MEDICAL,
            Domain.SCIENCE,
            Domain.MATH,
            Domain.HUMANITIES,
        ),
    ),
    ("cais/hle", "hle"): Benchmark(
        "humanitys-last-exam",
        (Skill.REASONING,),
        (Domain.MEDICAL, Domain.SCIENCE, Domain.MATH, Domain.HUMANITIES),
    ),
    ("MMMU/MMMU_Pro", "mmmu_pro_vision"): Benchmark(
        "mmmu-pro-vision",
        (Skill.REASONING, Skill.MULTIMODAL),
        (Domain.SCIENCE, Domain.HUMANITIES),
    ),
    ("LiquidAI/ifstruct-v1.0", "ifstruct_v1"): Benchmark(
        "ifstruct", (Skill.INSTRUCTION_FOLLOWING,)
    ),
    ("llamaindex/ParseBench", "mean"): Benchmark(
        "parsebench",
        (Skill.MULTIMODAL, Skill.FAITHFULNESS, Skill.INSTRUCTION_FOLLOWING),
    ),
    ("likaixin/ScreenSpot-Pro", "overall"): Benchmark("screenspot-pro", (Skill.MULTIMODAL,)),
    ("MathArena/aime_2026", "MathArena/aime_2026"): Benchmark(
        "aime-2026", (Skill.REASONING,), (Domain.MATH,)
    ),
    ("MathArena/hmmt_feb_2026", "MathArena/hmmt_feb_2026"): Benchmark(
        "hmmt-feb-2026", (Skill.REASONING,), (Domain.MATH,)
    ),
    ("openai/gsm8k", "gsm8k"): Benchmark("gsm8k", (Skill.REASONING,), (Domain.MATH,)),
    ("SWE-bench/SWE-bench_Verified", "swe_bench_%_resolved"): Benchmark(
        "swe-bench-verified", (Skill.CODING, Skill.AGENTIC), (Domain.CODING,)
    ),
    ("SWE-bench/SWE-bench_Multilingual", "swe_bench_multilingual_%_resolved"): Benchmark(
        "swe-bench-multilingual",
        (Skill.CODING, Skill.AGENTIC),
        (Domain.CODING, Domain.MULTILINGUAL),
    ),
    ("ScaleAI/SWE-bench_Pro", "SWE_Bench_Pro"): Benchmark(
        "swe-bench-pro", (Skill.CODING, Skill.AGENTIC), (Domain.CODING,)
    ),
    ("harborframework/terminal-bench-2.0", "terminalbench_2"): Benchmark(
        "terminal-bench-2",
        (Skill.CODING, Skill.AGENTIC, Skill.TOOL_USE),
        (Domain.CODING,),
    ),
    ("LEXam-Benchmark/LEXam", "open_question"): Benchmark(
        "lexam-open-question", (Skill.REASONING,), (Domain.LEGAL,)
    ),
    ("LEXam-Benchmark/LEXam", "mcq_4_choices"): Benchmark(
        "lexam-mcq", (Skill.REASONING,), (Domain.LEGAL,)
    ),
}

_MAPPED_DATASET_IDS = frozenset(dataset_id for dataset_id, _ in BENCHMARKS)
_PROVENANCE_RANK = {Provenance.MEASURED: 0, Provenance.LAB_CLAIMED: 1}


@dataclass(slots=True)
class SyncReport:
    rows: list[BenchmarkRow] = field(default_factory=list)
    fetch_failures: dict[str, str] = field(default_factory=dict)
    client_parse_failures: list[str] = field(default_factory=list)
    models_without_results: list[str] = field(default_factory=list)
    unmapped_datasets: dict[str, list[str]] = field(default_factory=dict)
    unused_subtasks: dict[str, int] = field(default_factory=dict)
    malformed_entries: int = 0
    duplicates_dropped: int = 0


def collect(model_ids: Iterable[str], *, fetcher: Fetcher | None = None) -> SyncReport:
    """Benchmark rows for each model, plus every skip the run made and why."""
    fetch = fetcher or fetch_eval_results
    report = SyncReport()
    for model_id in model_ids:
        try:
            payload = fetch(model_id)
        except Exception as exc:
            report.fetch_failures[model_id] = f"{type(exc).__name__}: {exc}"
            logger.warning("HuggingFace eval results unavailable for %s: %s", model_id, exc)
            continue
        entries = payload.get("evalResults") if isinstance(payload, Mapping) else None
        if not isinstance(entries, list) or not entries:
            report.models_without_results.append(model_id)
            continue
        kept, dropped = _dedupe(_rows_for_model(model_id, entries, report))
        report.rows.extend(kept)
        report.duplicates_dropped += dropped
    for models in report.unmapped_datasets.values():
        models.sort()
    return report


def fetch_eval_results(model_id: str) -> Mapping[str, Any]:
    """One GET per model, retried once unless the Hub answered with a permanent error."""
    url = _API_URL.format(model_id=model_id)
    try:
        return _get(url)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status is not None and status not in _RETRYABLE_STATUS:
            raise
    except requests.RequestException:
        pass
    time.sleep(_RETRY_DELAY_SECONDS)
    return _get(url)


def _get(url: str) -> Mapping[str, Any]:
    response = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError(f"expected a JSON object from {url}")
    return payload


def _rows_for_model(model_id: str, entries: list[Any], report: SyncReport) -> list[BenchmarkRow]:
    rows: list[BenchmarkRow] = []
    unparseable = False
    for entry in entries:
        if not isinstance(entry, Mapping):
            report.malformed_entries += 1
            continue
        data = entry.get("data")
        data = data if isinstance(data, Mapping) else {}
        dataset = data.get("dataset")
        dataset = dataset if isinstance(dataset, Mapping) else {}
        dataset_id = str(dataset.get("id") or "")
        task_id = str(dataset.get("task_id") or "")
        if not dataset_id or not task_id:
            unparseable = True
            continue
        benchmark = BENCHMARKS.get((dataset_id, task_id))
        if benchmark is None:
            _record_skip(report, model_id, dataset_id, task_id)
            continue
        value = data.get("value")
        if isinstance(value, bool) or not isinstance(value, int | float):
            report.malformed_entries += 1
            continue
        source = data.get("source")
        source = source if isinstance(source, Mapping) else {}
        rows.append(
            BenchmarkRow(
                hf_model_id=model_id,
                eval_slug=benchmark.slug,
                raw_score=float(value) / benchmark.native_max,
                scale=SCALE,
                skills=tuple(str(skill) for skill in benchmark.skills),
                domains=tuple(str(domain) for domain in benchmark.domains),
                provenance=str(_provenance(model_id, entry, source)),
                source=SOURCE,
                source_url=str(source.get("url") or ""),
                observed_at=_observed_at(data.get("date")),
            )
        )
    if unparseable:
        report.client_parse_failures.append(model_id)
    return rows


def _record_skip(report: SyncReport, model_id: str, dataset_id: str, task_id: str) -> None:
    if dataset_id in _MAPPED_DATASET_IDS:
        key = f"{dataset_id}#{task_id}"
        report.unused_subtasks[key] = report.unused_subtasks.get(key, 0) + 1
        return
    models = report.unmapped_datasets.setdefault(dataset_id, [])
    if model_id not in models:
        models.append(model_id)


def _provenance(model_id: str, entry: Mapping[str, Any], source: Mapping[str, Any]) -> Provenance:
    """A number the lab published about its own model is a claim; anything else is a run."""
    if entry.get("verified") is True:
        return Provenance.MEASURED
    name = str(source.get("name") or "").lower()
    url = str(source.get("url") or "")
    if "model card" in name:
        return Provenance.LAB_CLAIMED
    if url.lower().startswith(f"https://huggingface.co/{model_id.lower()}"):
        return Provenance.LAB_CLAIMED
    if _is_org_domain(model_id, url):
        return Provenance.LAB_CLAIMED
    return Provenance.MEASURED


def _is_org_domain(model_id: str, url: str) -> bool:
    org = _NON_ALNUM_RE.sub("", model_id.split("/", 1)[0].lower())
    if len(org) < _MIN_ORG_TOKEN:
        return False
    host = _NON_ALNUM_RE.sub("", urlsplit(url).netloc.lower())
    return org in host


def _observed_at(value: Any) -> str:
    text = str(value or "")
    return text[:10] if _DATE_RE.match(text) else ""


def _dedupe(rows: list[BenchmarkRow]) -> tuple[list[BenchmarkRow], int]:
    """One row per benchmark: measured beats lab-claimed, newer beats older, and a tie keeps
    the lower score — the weaker configuration is the one a non-thinking fine-tune inherits.
    """
    by_slug: dict[str, list[BenchmarkRow]] = {}
    for row in rows:
        by_slug.setdefault(row["eval_slug"], []).append(row)
    kept = [min(group, key=_preference) for group in by_slug.values()]
    return kept, len(rows) - len(kept)


def _preference(row: BenchmarkRow) -> tuple[int, int, float]:
    digits = row["observed_at"].replace("-", "")
    recency = -int(digits) if digits.isdigit() else 0
    return _PROVENANCE_RANK[Provenance(row["provenance"])], recency, row["raw_score"]
