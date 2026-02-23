"""
Utility functions for the prompts endpoint.
"""

from typing import Any


def normalize_criteria_rules(rules: list[str]) -> set:
    """
    Normalize a list of criteria rules for comparison.
    Converts to lowercase and removes duplicates (order-independent).

    Args:
        rules: List of criteria rules

    Returns:
        Set of normalized rules (lowercase, stripped, no duplicates)
    """
    return {rule.strip().lower() for rule in rules if rule.strip()}


def are_criteria_same(
    old_criteria: dict[str, list[str]] | None, new_criteria: dict[str, list[str]]
) -> bool:
    """
    Compare two criteria dictionaries to check if they're the same.
    Comparison is case-insensitive and order-independent.

    Args:
        old_criteria: Existing evaluation criteria (or None)
        new_criteria: New evaluation criteria to compare

    Returns:
        True if criteria are the same, False otherwise

    Examples:
        >>> old = {"correctness": ["Rule 1", "Rule 2"]}
        >>> new = {"correctness": ["rule 1", "rule 2"]}
        >>> are_criteria_same(old, new)
        True

        >>> old = {"correctness": ["Rule 2", "Rule 1"]}
        >>> new = {"correctness": ["Rule 1", "Rule 2"]}
        >>> are_criteria_same(old, new)
        True

        >>> old = {"correctness": ["Rule 1"]}
        >>> new = {"correctness": ["Rule 1", "Rule 2"]}
        >>> are_criteria_same(old, new)
        False
    """
    if old_criteria is None:
        return False

    # Check if they have the same keys
    if set(old_criteria.keys()) != set(new_criteria.keys()):
        return False

    # Compare normalized rule sets for each metric
    for metric_name in old_criteria.keys():
        old_rules = normalize_criteria_rules(old_criteria[metric_name])
        new_rules = normalize_criteria_rules(new_criteria[metric_name])

        if old_rules != new_rules:
            return False

    return True


def are_descriptions_same(
    old_agent_description: dict[str, Any] | None,
    new_description: str | None,
) -> bool:
    """
    Check whether the description field inside agent_description has effectively
    changed.  Comparison is case-insensitive and ignores leading/trailing
    whitespace so that cosmetic-only edits don't trigger unnecessary resets.

    Args:
        old_agent_description: The existing ``agent_description`` JSONB value
                                (may be None or a dict containing a "description" key).
        new_description: The new description string supplied by the caller.

    Returns:
        True if the description is the same, False if it has changed.
    """
    old_text: str | None = None
    if old_agent_description and isinstance(old_agent_description, dict):
        old_text = old_agent_description.get("description")

    if old_text is None and new_description is None:
        return True
    if old_text is None or new_description is None:
        return False
    return old_text.strip().lower() == new_description.strip().lower()
