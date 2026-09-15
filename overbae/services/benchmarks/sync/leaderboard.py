"""Third-party evaluation runs, read out of one aggregate leaderboard page.

The leaderboard embeds the whole model x evaluation matrix in its Next.js RSC payload, so
a single ~10 MB fetch covers every model and every benchmark. Scores come out raw:
percentiles need the complete cohort and belong to the step that composes the artifact.

Every benchmark in ``BENCHMARKS`` names the work that defines it, so a score is always
attributable to the people who published the benchmark rather than to the aggregator that
ran it. A benchmark with no such reference is not carried.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from ..schema import Provenance
from ..taxonomy import Domain, Skill

DEFAULT_URL = "https://artificialanalysis.ai/evaluations/gpqa-diamond"
SOURCE = "leaderboard"

# The host serves a Next.js app shell to non-browser capabilities; the RSC payload only arrives
# with a browser User-Agent.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

_RSC_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', re.S)

# Present on every per-model record and nowhere else, so it anchors the backward scan that
# recovers each record's opening brace out of the concatenated payload.
_RECORD_ANCHOR = "estimated_intelligence_index_v4_1"
_MAX_BRACE_SCAN = 400


@dataclass(frozen=True, slots=True)
class Benchmark:
    """One evaluation: where to read it, what it measures, and who defined it.

    ``path`` walks the nested per-model record; ``scale`` records the native units so
    nothing is averaged across scales downstream. ``reference`` is the benchmark's own
    publication — paper, repository or dataset — and is what a score links to.
    """

    slug: str
    path: tuple[str, ...]
    scale: str
    reference: str
    skills: tuple[Skill, ...] = ()
    domains: tuple[Domain, ...] = ()
    reduce: str | None = None


BENCHMARKS: tuple[Benchmark, ...] = (
    Benchmark(
        "gdpval",
        ("gdpval_v2",),
        "elo",
        "https://arxiv.org/abs/2510.04374",
        (
            Skill.AGENTIC,
            Skill.INSTRUCTION_FOLLOWING,
            Skill.MULTIMODAL,
            Skill.REASONING,
            Skill.TOOL_USE,
            Skill.WRITING,
        ),
        (Domain.BUSINESS, Domain.FINANCE, Domain.HUMANITIES, Domain.LEGAL, Domain.MEDICAL),
    ),
    Benchmark(
        "apex-capabilities",
        ("apex_capabilities",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2601.14242",
        (Skill.AGENTIC, Skill.CODING, Skill.REASONING, Skill.TOOL_USE),
        (Domain.CODING,),
    ),
    Benchmark(
        "automationbench",
        ("automation_bench_breakdown", "summary", "completion"),
        "accuracy_0_1",
        "https://arxiv.org/abs/2604.18934",
        (Skill.AGENTIC, Skill.INSTRUCTION_FOLLOWING, Skill.TOOL_USE),
        (Domain.BUSINESS,),
    ),
    Benchmark(
        "harvey-lab",
        ("harvey_lab_breakdown", "criteria_pass"),
        "accuracy_0_1",
        "https://github.com/harveyai/harvey-labs",
        (
            Skill.AGENTIC,
            Skill.INSTRUCTION_FOLLOWING,
            Skill.LONG_CONTEXT,
            Skill.REASONING,
            Skill.WRITING,
        ),
        (Domain.LEGAL,),
    ),
    Benchmark(
        "enterprise-ops-gym",
        ("enterprise_ops_gym_breakdown", "summary", "success_rate"),
        "accuracy_0_1",
        "https://arxiv.org/abs/2603.13594",
        (Skill.AGENTIC, Skill.INSTRUCTION_FOLLOWING, Skill.TOOL_USE, Skill.USER_INTERACTION),
        (Domain.BUSINESS,),
    ),
    Benchmark(
        "tau3-banking",
        ("tau_banking",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2603.04370",
        (Skill.AGENTIC, Skill.INSTRUCTION_FOLLOWING, Skill.TOOL_USE, Skill.USER_INTERACTION),
        (Domain.BUSINESS, Domain.FINANCE),
    ),
    Benchmark(
        "terminalbench-v2-1",
        ("terminalbench_v2_1",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2601.11868",
        (
            Skill.AGENTIC,
            Skill.CODING,
            Skill.INSTRUCTION_FOLLOWING,
            Skill.REASONING,
            Skill.TOOL_USE,
        ),
        (Domain.CODING,),
    ),
    Benchmark(
        "omniscience",
        ("omniscience",),
        "index_signed",
        "https://arxiv.org/abs/2511.13029",
        (Skill.FAITHFULNESS, Skill.REASONING),
        (
            Domain.BUSINESS,
            Domain.FINANCE,
            Domain.HUMANITIES,
            Domain.LEGAL,
            Domain.MEDICAL,
            Domain.SCIENCE,
        ),
    ),
    Benchmark(
        "scicode",
        ("scicode",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2407.13168",
        (Skill.CODING, Skill.REASONING),
        (Domain.CODING, Domain.MATH, Domain.SCIENCE),
    ),
    Benchmark(
        "humanitys-last-exam",
        ("hle",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2501.14249",
        (Skill.REASONING,),
        (Domain.HUMANITIES, Domain.MATH, Domain.MEDICAL, Domain.SCIENCE),
    ),
    Benchmark(
        "critpt",
        ("critpt",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2509.26574",
        (Skill.REASONING,),
        (Domain.SCIENCE,),
    ),
    Benchmark(
        "gpqa-diamond",
        ("gpqa",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2311.12022",
        (Skill.REASONING,),
        (Domain.SCIENCE,),
    ),
    Benchmark(
        "itbench",
        ("it_bench_sre",),
        "accuracy_0_1",
        "https://github.com/itbench-hub/ITBench",
        (Skill.AGENTIC, Skill.REASONING, Skill.TOOL_USE),
        (Domain.CODING,),
    ),
    Benchmark(
        "mmmu-pro",
        ("mmmu_pro",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2409.02813",
        (Skill.MULTIMODAL, Skill.REASONING),
        (Domain.HUMANITIES, Domain.SCIENCE),
    ),
    Benchmark(
        "ifbench",
        ("ifbench",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2507.02833",
        (Skill.INSTRUCTION_FOLLOWING,),
    ),
    Benchmark(
        "terminalbench-hard",
        ("terminalbench_hard",),
        "accuracy_0_1",
        "https://github.com/laude-institute/terminal-bench",
        (
            Skill.AGENTIC,
            Skill.CODING,
            Skill.INSTRUCTION_FOLLOWING,
            Skill.LONG_CONTEXT,
            Skill.REASONING,
            Skill.TOOL_USE,
        ),
        (Domain.CODING,),
    ),
    Benchmark(
        "tau2-bench",
        ("tau2",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2506.07982",
        (
            Skill.AGENTIC,
            Skill.FAITHFULNESS,
            Skill.INSTRUCTION_FOLLOWING,
            Skill.TOOL_USE,
            Skill.USER_INTERACTION,
        ),
        (Domain.BUSINESS,),
    ),
    Benchmark(
        "mmlu-pro",
        ("mmlu_pro",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2406.01574",
        (Skill.INSTRUCTION_FOLLOWING, Skill.REASONING),
        (
            Domain.BUSINESS,
            Domain.HUMANITIES,
            Domain.LEGAL,
            Domain.MATH,
            Domain.MEDICAL,
            Domain.SCIENCE,
        ),
    ),
    Benchmark(
        "livecodebench",
        ("livecodebench",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2403.07974",
        (Skill.CODING, Skill.REASONING),
        (Domain.CODING, Domain.MATH),
    ),
    Benchmark(
        "math-500",
        ("math_500",),
        "accuracy_0_1",
        "https://arxiv.org/abs/2305.20050",
        (Skill.REASONING,),
        (Domain.MATH,),
    ),
    Benchmark(
        "aime-2025",
        ("aime25",),
        "accuracy_0_1",
        "https://artofproblemsolving.com/wiki/index.php/2025_AIME_I",
        (Skill.REASONING,),
        (Domain.MATH,),
    ),
    Benchmark(
        "global-mmlu-lite",
        ("global_mmlu_lite_json",),
        "accuracy_0_1",
        "https://huggingface.co/datasets/CohereLabs/Global-MMLU-Lite",
        (Skill.INSTRUCTION_FOLLOWING, Skill.REASONING),
        (
            Domain.BUSINESS,
            Domain.HUMANITIES,
            Domain.MATH,
            Domain.MULTILINGUAL,
            Domain.SCIENCE,
        ),
        reduce="mean_of_lang_scores",
    ),
)

BY_SLUG: dict[str, Benchmark] = {benchmark.slug: benchmark for benchmark in BENCHMARKS}

# The labs' own published numbers, carried by the leaderboard beside its own runs. They
# land on the same benchmark under a lower provenance so grading keeps the tiers apart.
LAB_CLAIMED_FIELDS: dict[str, str] = {
    "lab_claimed_aime": "aime-2025",
    "lab_claimed_gpqa": "gpqa-diamond",
    "lab_claimed_hle": "humanitys-last-exam",
    "lab_claimed_livecodebench": "livecodebench",
    "lab_claimed_math_500": "math-500",
    "lab_claimed_mmlu_pro": "mmlu-pro",
    "lab_claimed_scicode": "scicode",
}
LAB_CLAIMED_SCALE = "accuracy_0_1"

Fetcher = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class EvalScore:
    model_slug: str
    model_name: str
    hf_model_id: str | None
    is_open_weights: bool
    eval_slug: str
    raw_score: float
    scale: str
    skills: tuple[str, ...]
    domains: tuple[str, ...]
    provenance: str


def fetch(url: str = DEFAULT_URL, *, timeout: float = 120.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def collect(*, url: str = DEFAULT_URL, fetcher: Fetcher = fetch) -> list[EvalScore]:
    return parse(fetcher(url))


def parse(html: str) -> list[EvalScore]:
    return list(iter_scores(model_records(extract_rsc(html))))


def extract_rsc(html: str) -> str:
    """The RSC payload, reassembled from the streamed ``self.__next_f.push`` chunks."""
    return "".join(json.loads(f'"{chunk}"') for chunk in _RSC_CHUNK.findall(html))


def model_records(rsc: str) -> dict[str, dict[str, Any]]:
    """Every per-model record in the payload, keyed by upstream model slug."""
    decoder = json.JSONDecoder()
    found: dict[str, dict[str, Any]] = {}
    for anchor in re.finditer(rf'"{_RECORD_ANCHOR}":', rsc):
        start, tries = rsc.rfind("{", 0, anchor.start()), 0
        while start >= 0 and tries < _MAX_BRACE_SCAN:
            try:
                obj, end = decoder.raw_decode(rsc, start)
            except ValueError:
                obj, end = None, -1
            if isinstance(obj, dict) and _RECORD_ANCHOR in obj and end > anchor.start():
                if obj.get("slug"):
                    found.setdefault(obj["slug"], obj)
                break
            start, tries = rsc.rfind("{", 0, start), tries + 1
    return found


def iter_scores(records: Mapping[str, Mapping[str, Any]]) -> Iterator[EvalScore]:
    """One row per model per benchmark that carries a number, measured and lab-claimed."""
    for model_slug, record in sorted(records.items()):
        model = _model_fields(model_slug, record)
        for benchmark in BENCHMARKS:
            raw = _as_float(_read_field(record, benchmark))
            if raw is not None:
                yield _score(model, benchmark, raw, benchmark.scale, Provenance.MEASURED)
        for field_name, slug in LAB_CLAIMED_FIELDS.items():
            raw = _as_float(record.get(field_name))
            if raw is not None:
                yield _score(model, BY_SLUG[slug], raw, LAB_CLAIMED_SCALE, Provenance.LAB_CLAIMED)


def hf_model_id(url: str | None) -> str | None:
    if not url or "huggingface.co" not in url:
        return None
    parts = url.split("huggingface.co/", 1)[1].strip("/").split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else None


def _model_fields(model_slug: str, record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model_slug": model_slug,
        "model_name": str(record.get("name") or model_slug),
        "hf_model_id": hf_model_id(record.get("model_weights_source_url")),
        "is_open_weights": bool(record.get("is_open_weights")),
    }


def _score(
    model: dict[str, Any],
    benchmark: Benchmark,
    raw_score: float,
    scale: str,
    provenance: Provenance,
) -> EvalScore:
    return EvalScore(
        **model,
        eval_slug=benchmark.slug,
        raw_score=raw_score,
        scale=scale,
        skills=tuple(str(skill) for skill in benchmark.skills),
        domains=tuple(str(domain) for domain in benchmark.domains),
        provenance=str(provenance),
    )


def _read_field(record: Mapping[str, Any], benchmark: Benchmark) -> Any:
    value: Any = record
    for key in benchmark.path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    if benchmark.reduce != "mean_of_lang_scores":
        return value
    if not isinstance(value, Mapping):
        return None
    scores = [_as_float(lang.get("score")) for lang in value.values() if isinstance(lang, Mapping)]
    present = [score for score in scores if score is not None]
    return sum(present) / len(present) if present else None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
