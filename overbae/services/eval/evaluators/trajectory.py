"""Trajectory evaluator family. ``config["mode"]`` selects ``match`` (default,
deterministic) or ``judge`` (routed through the long-trace cascade).

``strict``/``unordered``/``subset``/``superset`` score 1.0/0.0. ``subsequence``
instead awards order-preserving partial credit in [0,1] (matched_steps /
len(reference)) — it is not ``subset``, which is unordered and all-or-nothing.

``config["tool_args_match_overrides"]`` maps a tool name to either a match-mode
string (``exact``/``ignore``/``subset``/``superset``) or a list of field names
that must match exactly, overriding the global ``tool_args_match_mode``.
"""

from __future__ import annotations

from typing import Any

from overbae.services.eval.evaluators.base import (
    OUTCOME_ABSTAINED,
    EvalUnit,
    ScoreDraft,
    judge_module,
)


def evaluate(unit: EvalUnit, evaluator, ctx: dict[str, Any]) -> list[ScoreDraft]:
    config = evaluator.config or {}
    mode = config.get("mode", "match")
    if mode == "judge":
        return judge_module(ctx).evaluate(unit, evaluator, ctx)
    return [_match(unit, evaluator, config)]


def _calls(unit: EvalUnit) -> list[dict[str, Any]]:
    return ((unit.structured or {}).get("tool_graph") or {}).get("nodes", [])


def _reference_calls(unit: EvalUnit, config: dict) -> list[dict[str, Any]]:
    if "reference_trajectory" in config:
        return config["reference_trajectory"]
    exp = unit.expected
    if isinstance(exp, dict) and "trajectory" in exp:
        return exp["trajectory"]
    if isinstance(exp, list):
        return exp
    # Never fall back to the actual calls: comparing a trajectory to itself
    # scores a trivial 1.0. Empty makes ``_match`` abstain instead.
    return []


def _match(unit: EvalUnit, evaluator, config: dict) -> ScoreDraft:
    match_mode = config.get("trajectory_match_mode", "unordered")
    args_mode = config.get("tool_args_match_mode", "exact")
    overrides: dict[str, str] = config.get("tool_args_match_overrides") or {}
    actual = _calls(unit)
    reference = _reference_calls(unit, config)

    if not reference:
        reason = (
            "No reference trajectory available; Trajectory Accuracy needs a reference "
            "call sequence (config 'reference_trajectory' or expected output) to compare "
            "against. Abstaining rather than comparing the trajectory to itself."
            if actual
            else "No tool calls found in trajectory; Trajectory Accuracy requires capability steps."
        )
        return ScoreDraft(
            name=evaluator.name,
            data_type="numeric",
            value=None,
            outcome=OUTCOME_ABSTAINED,
            reasoning=reason,
            scope=evaluator.scope,
        )

    if not actual:
        # Empty evidence abstains, matching the judges: "made no calls" is not
        # "made the wrong calls", which still scores 0 via _compare below.
        return ScoreDraft(
            name=evaluator.name,
            data_type="numeric",
            value=None,
            outcome=OUTCOME_ABSTAINED,
            reasoning=(
                "No tool calls found in trajectory; abstaining (empty evidence) rather "
                "than scoring 0 against the reference call sequence."
            ),
            scope=evaluator.scope,
        )

    if match_mode == "subsequence":
        matched = _lcs_len(actual, reference, args_mode, overrides)
        score = matched / len(reference) if reference else 0.0
        return ScoreDraft(
            name=evaluator.name,
            data_type="numeric",
            value=round(score, 4),
            reasoning=(
                f"trajectory subsequence match: {matched}/{len(reference)} reference "
                f"steps matched in order ({len(actual)} actual calls)"
            ),
            scope=evaluator.scope,
            sub_scores=[
                {
                    "actual_tools": [_name(c) for c in actual],
                    "reference_tools": [_name(c) for c in reference],
                    "matched_steps": matched,
                }
            ],
        )

    ok, detail = _compare(actual, reference, match_mode, args_mode, overrides)
    return ScoreDraft(
        name=evaluator.name,
        data_type="boolean",
        value=1.0 if ok else 0.0,
        passed=ok,
        reasoning=(
            f"trajectory {match_mode} match: {'pass' if ok else 'fail'} "
            f"({len(actual)} actual vs {len(reference)} reference calls"
            + (f"; {detail}" if detail else "")
            + ")"
        ),
        scope=evaluator.scope,
        sub_scores=[
            {
                "actual_tools": [c.get("tool") for c in actual],
                "reference_tools": [_name(c) for c in reference],
                "first_mismatch": detail or None,
            }
        ],
    )


def _name(call: Any) -> str:
    if isinstance(call, dict):
        return call.get("tool") or call.get("name") or ""
    return str(call)


def _args(call: Any) -> dict:
    if isinstance(call, dict):
        return call.get("arguments", {}) or {}
    return {}


def _effective_args_mode(tool_name: str, global_mode: str, overrides: dict) -> Any:
    """Either a mode string or a list of field names that must match exactly."""
    return overrides.get(tool_name, global_mode)


def _call_eq(a: Any, b: Any, args_mode: str, overrides: dict | None = None) -> bool:
    tool = _name(a)
    if tool != _name(b):
        return False
    effective = _effective_args_mode(tool, args_mode, overrides or {})
    aa, ba = _args(a), _args(b)
    if isinstance(effective, (list, tuple)):
        # Only the listed argument fields must match exactly; others are ignored.
        return all(aa.get(k) == ba.get(k) for k in effective)
    if effective == "ignore":
        return True
    if effective == "subset":
        # Reference args must appear in actual (actual can have extras).
        return all(aa.get(k) == v for k, v in ba.items())
    if effective == "superset":
        # Actual args must appear in reference (reference can have extras).
        return all(ba.get(k) == v for k, v in aa.items())
    return aa == ba


def _lcs_len(actual: list, reference: list, args_mode: str, overrides: dict) -> int:
    n, m = len(actual), len(reference)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if _call_eq(actual[i - 1], reference[j - 1], args_mode, overrides):
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[n][m]


def _compare(
    actual: list,
    reference: list,
    match_mode: str,
    args_mode: str,
    overrides: dict[str, str],
) -> tuple[bool, str]:
    """Return (passed, first_mismatch_detail)."""
    if match_mode == "strict":
        if len(actual) != len(reference):
            return False, f"length {len(actual)} != {len(reference)}"
        for i, (a, b) in enumerate(zip(actual, reference, strict=False)):
            if not _call_eq(a, b, args_mode, overrides):
                return False, f"step {i}: {_name(a)!r} vs {_name(b)!r}"
        return True, ""

    if match_mode == "unordered":
        ok = _multiset_equal(actual, reference, args_mode, overrides)
        return ok, "" if ok else "tool call sets do not match"

    if match_mode == "subset":
        # Every actual call must appear in reference (no extras).
        ok, miss = _contained_detail(actual, reference, args_mode, overrides)
        return ok, miss

    if match_mode == "superset":
        # Every reference call must appear in actual (extras allowed).
        ok, miss = _contained_detail(reference, actual, args_mode, overrides)
        return ok, miss

    return False, f"unknown match_mode {match_mode!r}"


def _contained_detail(
    needles: list, haystack: list, args_mode: str, overrides: dict[str, str]
) -> tuple[bool, str]:
    pool = list(haystack)
    for n in needles:
        idx = next((i for i, h in enumerate(pool) if _call_eq(n, h, args_mode, overrides)), None)
        if idx is None:
            return False, f"no match for {_name(n)!r}"
        pool.pop(idx)
    return True, ""


def _multiset_equal(
    a: list, b: list, args_mode: str, overrides: dict[str, str] | None = None
) -> bool:
    return len(a) == len(b) and _contained_detail(a, b, args_mode, overrides or {})[0]
