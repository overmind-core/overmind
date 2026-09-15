from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from django.test import override_settings

from overbae.services.recommendation.constraints import Exclusion, eligible_models

_ROOT = Path(__file__).resolve().parents[1]

_UNTRAINABLE = {"training_type": {"lora": {"enabled": False}, "full": {"enabled": False}}}
_NO_TOOLS = {"supports_tool_calling": False}
_SHORT_CONTEXT = {"context_length_sft": 2048}


def _entry(model_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "id": model_id,
        "display": model_id,
        "params": "8B",
        "total_params_b": 8.0,
        "context_length_sft": 8192,
        "max_batch_size": 8,
        "min_batch_size": 1,
        "supports_tool_calling": True,
        "training_type": {"lora": {"enabled": True}, "full": {"enabled": False}},
        **overrides,
    }


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    tiers: dict[str, list[dict[str, Any]]] = {}
    monkeypatch.setattr(
        "overbae.services.recommendation.constraints.tier_models",
        lambda **_kwargs: tiers,
    )
    return tiers


def test_a_model_with_no_trainable_method_is_named_as_such(catalog):
    catalog["small"] = [_entry("vendor/untrainable", **_UNTRAINABLE)]

    eligible, exclusions = eligible_models(has_tool_calling=False, max_row_tokens=1000)

    assert eligible == {}
    assert exclusions == [
        Exclusion(model="vendor/untrainable", reason="No supported fine-tuning method")
    ]


def test_a_tool_calling_dataset_names_the_support_the_model_lacks(catalog):
    catalog["small"] = [_entry("vendor/no-tools", **_NO_TOOLS)]

    eligible, exclusions = eligible_models(has_tool_calling=True, max_row_tokens=1000)

    assert eligible == {}
    assert exclusions == [
        Exclusion(model="vendor/no-tools", reason="No tool-calling fine-tuning support")
    ]


def test_the_context_reason_quotes_both_lengths(catalog):
    catalog["small"] = [_entry("vendor/short", **_SHORT_CONTEXT)]

    eligible, exclusions = eligible_models(has_tool_calling=False, max_row_tokens=4100)

    assert eligible == {}
    assert len(exclusions) == 1
    assert exclusions[0].model == "vendor/short"
    assert "SFT context 2,048 < longest row 4,100" in exclusions[0].reason


def test_exact_context_equality_is_excluded_for_headroom(catalog):
    """Model max == longest row leaves zero headroom — must not survive."""
    catalog["small"] = [_entry("vendor/tight", context_length_sft=4100)]

    eligible, exclusions = eligible_models(has_tool_calling=False, max_row_tokens=4100)

    assert eligible == {}
    assert exclusions[0].model == "vendor/tight"
    assert "4,100" in exclusions[0].reason


def test_a_model_lacking_tools_survives_a_dataset_that_never_calls_one(catalog):
    catalog["small"] = [_entry("vendor/no-tools", **_NO_TOOLS)]

    eligible, exclusions = eligible_models(has_tool_calling=False, max_row_tokens=1000)

    assert [entry["id"] for entry in eligible["small"]] == ["vendor/no-tools"]
    assert exclusions == []


def test_an_unmeasured_row_length_leaves_context_unchecked(catalog):
    catalog["small"] = [_entry("vendor/short", **_SHORT_CONTEXT)]

    eligible, exclusions = eligible_models(has_tool_calling=False, max_row_tokens=None)

    assert [entry["id"] for entry in eligible["small"]] == ["vendor/short"]
    assert exclusions == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({**_UNTRAINABLE, **_SHORT_CONTEXT}, "No supported fine-tuning method"),
        ({**_UNTRAINABLE, **_NO_TOOLS}, "No supported fine-tuning method"),
        ({**_NO_TOOLS, **_SHORT_CONTEXT}, "No tool-calling fine-tuning support"),
    ],
)
def test_a_model_failing_two_rules_is_excluded_once(catalog, overrides, reason):
    catalog["small"] = [_entry("vendor/doomed", **overrides)]

    eligible, exclusions = eligible_models(has_tool_calling=True, max_row_tokens=4100)

    assert eligible == {}
    assert exclusions == [Exclusion(model="vendor/doomed", reason=reason)]


def test_a_tier_that_loses_every_model_is_omitted_rather_than_empty(catalog):
    catalog["compact"] = [_entry("vendor/short", **_SHORT_CONTEXT)]
    catalog["small"] = [_entry("vendor/fits")]

    eligible, exclusions = eligible_models(has_tool_calling=True, max_row_tokens=4100)

    assert list(eligible) == ["small"]
    assert [entry["id"] for entry in eligible["small"]] == ["vendor/fits"]
    assert [exclusion.model for exclusion in exclusions] == ["vendor/short"]


def test_the_real_catalog_rejects_each_model_at_most_once():
    with override_settings(FINETUNING_BACKEND="baseten"):
        eligible, exclusions = eligible_models(has_tool_calling=True, max_row_tokens=50_000)

    rejected = [exclusion.model for exclusion in exclusions]
    survivors = {entry["id"] for models in eligible.values() for entry in models}
    assert rejected
    assert len(rejected) == len(set(rejected))
    assert not survivors & set(rejected)
    assert {exclusion.reason for exclusion in exclusions if exclusion.reason.startswith("SFT")}
    assert "No tool-calling fine-tuning support" in {e.reason for e in exclusions}


def test_no_quality_signal_is_reachable_from_the_hard_filters():
    reachable = _reachable_modules("overbae.services.recommendation.constraints")

    assert "overbae.services.recommendation.catalog" in reachable
    assert [
        module
        for module in reachable
        if module.startswith("overbae.services.benchmarks")
        or module
        in {
            "overbae.services.recommendation.ranking",
            "overbae.services.recommendation.candidates",
            "overbae.services.recommendation.analysis",
        }
    ] == []


def _reachable_modules(root: str) -> set[str]:
    """Every first-party module the source of *root* can pull in, lazy imports included."""
    seen: set[str] = set()
    queue = [root]
    while queue:
        module = queue.pop()
        path = _module_file(module)
        if path is None or module in seen:
            continue
        seen.add(module)
        queue.extend(_imported_names(module, path) - seen)
    return seen - {root}


def _module_file(module: str) -> Path | None:
    parts = module.split(".")
    candidates = (_ROOT.joinpath(*parts).with_suffix(".py"), _ROOT.joinpath(*parts, "__init__.py"))
    return next((path for path in candidates if path.is_file()), None)


def _imported_names(module: str, path: Path) -> set[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            prefix = _absolute_prefix(package, node)
            names.add(prefix)
            names |= {f"{prefix}.{alias.name}" for alias in node.names}
    return {name for name in names if name.startswith("overbae.")}


def _absolute_prefix(package: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    parts = package.split(".")
    base = ".".join(parts[: len(parts) - node.level + 1])
    return f"{base}.{node.module}" if node.module else base
