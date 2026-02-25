"""
Template Extractor - Extract prompt templates from LLM traces.

This module analyzes a list of strings (LLM inputs) and groups them by
common templates, extracting both the template structure and variable values.

Usage:
    from overmind.core.template_extractor import extract_templates, ExtractionConfig

    traces = [
        "Hello Alice, welcome to the system!",
        "Hello Bob, welcome to the system!",
        "Hello Charlie, welcome to the system!",
    ]

    result = extract_templates(traces)
    print(result.summary())
    # Template: "Hello {var_0}, welcome to the system!"
    # Variables: var_0='Alice', var_0='Bob', var_0='Charlie'

    # Match new strings against discovered templates
    from overmind.core.template_extractor import match_string_to_template
    match = match_string_to_template("Hello Diana, welcome to the system!", result.templates[0])
    # match.variables = {'var_0': 'Diana'}
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .helpers import (
    Token,
    build_template_from_group,
    compute_anchors_for_group,
    extract_variables_between_anchors,
    find_candidate_groups,
    find_groups_by_common_anchors,
    token_values,
    tokenize,
    validate_anchor_sequence,
)


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class TemplateElement:
    """An element in a template - either fixed text or a variable slot."""

    is_variable: bool
    value: (
        str  # For fixed: the actual text. For variable: the slot name (e.g., "var_0")
    )


@dataclass
class ExtractedVariable:
    """A variable extracted from a matched string."""

    name: str
    value: str
    tokens: list[Token]


@dataclass
class TemplateMatch:
    """A single string that matched a template."""

    original_string: str
    variables: dict[str, str]  # var_name -> value


@dataclass
class Template:
    """A discovered template with its matches."""

    template_string: str  # Human-readable template with {var_N} placeholders
    elements: list[TemplateElement]  # Structured representation
    anchor_tokens: list[str]  # The fixed tokens that define this template
    matches: list[TemplateMatch] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"Template({self.template_string!r}, {len(self.matches)} matches)"


@dataclass
class ExtractionResult:
    """Complete result of template extraction."""

    templates: list[Template]
    unmatched: list[str]  # Strings that didn't match any template (unique)

    def summary(self) -> str:
        """Return a human-readable summary."""
        lines = []
        lines.append(
            f"Found {len(self.templates)} template(s), {len(self.unmatched)} unique string(s)\n"
        )

        for i, template in enumerate(self.templates, 1):
            lines.append(f"Template {i}: {template.template_string}")
            for match in template.matches:
                vars_str = ", ".join(f"{k}={v!r}" for k, v in match.variables.items())
                lines.append(f"  - [{vars_str}]")
            lines.append("")

        if self.unmatched:
            lines.append("Unique (no template):")
            for s in self.unmatched:
                preview = s[:60] + "..." if len(s) > 60 else s
                lines.append(f"  - {preview!r}")

        return "\n".join(lines)


@dataclass
class ExtractionConfig:
    """Configuration for template extraction."""

    min_similarity: float = 0.3  # Minimum Jaccard similarity for candidate grouping
    min_group_size: int = 2  # Minimum strings to form a template
    min_anchor_tokens: int = 1  # Minimum anchor tokens required
    skip_whitespace_in_similarity: bool = True
    skip_whitespace_in_anchors: bool = True
    # Use anchor-based grouping in addition to similarity-based
    use_anchor_grouping: bool = True
    min_common_anchors: int = 3  # For anchor-based grouping
    # Strict mode: treat consistent variable values as part of template
    # This separates "the order" vs "an order" into different templates
    strict_mode: bool = True
    # Performance options
    use_parallel: bool = True  # Use parallel processing for grouping
    n_jobs: int | None = None  # Number of parallel workers (None = auto)


# =============================================================================
# CORE EXTRACTION LOGIC
# =============================================================================


def _parse_template_string(template_string: str) -> list[TemplateElement]:
    """
    Parse a template string into structured elements.

    Args:
        template_string: Template with {var_N} placeholders

    Returns:
        List of TemplateElement objects
    """
    elements = []
    pattern = r"\{(var_\d+)\}"
    last_end = 0

    for match in re.finditer(pattern, template_string):
        # Add fixed text before this variable
        if match.start() > last_end:
            elements.append(
                TemplateElement(
                    is_variable=False, value=template_string[last_end : match.start()]
                )
            )
        # Add the variable
        elements.append(TemplateElement(is_variable=True, value=match.group(1)))
        last_end = match.end()

    # Add any remaining fixed text
    if last_end < len(template_string):
        elements.append(
            TemplateElement(is_variable=False, value=template_string[last_end:])
        )

    return elements


def _create_template_from_group(
    strings: list[str],
    token_lists: list[list[Token]],
    config: ExtractionConfig,
) -> Template | None:
    """
    Create a Template from a group of strings believed to share the same template.

    Args:
        strings: Original strings
        token_lists: Tokenized versions of strings
        config: Extraction configuration

    Returns:
        Template object, or None if no valid template could be created
    """
    if len(strings) < config.min_group_size:
        return None

    # Get token values (strings) for each tokenized string
    token_sequences = [token_values(tl) for tl in token_lists]

    # Compute anchors (LCS across all strings in group)
    anchors = compute_anchors_for_group(
        token_sequences, skip_whitespace=config.skip_whitespace_in_anchors
    )

    if len(anchors) < config.min_anchor_tokens:
        return None

    # Verify all strings match the anchor sequence
    valid_indices = []
    for i, seq in enumerate(token_sequences):
        if validate_anchor_sequence(seq, anchors):
            valid_indices.append(i)

    if len(valid_indices) < config.min_group_size:
        return None

    # Get valid sequences only
    valid_sequences = [token_sequences[i] for i in valid_indices]
    valid_strings = [strings[i] for i in valid_indices]

    # Build template and extract variables
    template_string, all_variables = build_template_from_group(valid_sequences, anchors)

    # Parse template into structured elements
    elements = _parse_template_string(template_string)

    # Create matches for all valid strings
    matches = []
    for i, vars_list in enumerate(all_variables):
        var_dict = {name: value for name, value in vars_list}
        matches.append(
            TemplateMatch(original_string=valid_strings[i], variables=var_dict)
        )

    return Template(
        template_string=template_string,
        elements=elements,
        anchor_tokens=anchors,
        matches=matches,
    )


def _split_by_variable_patterns(
    indices: list[int],
    token_lists: list[list[Token]],
    anchors: list[str],
    min_group_size: int = 2,
) -> list[set[int]]:
    """
    Split a group by consistent variable values (strict mode).

    If some strings consistently have "the" in a variable slot while others
    have "an" or nothing, this splits them into separate groups.

    Args:
        indices: Indices of strings in this group
        token_lists: All tokenized strings
        anchors: Common anchors for this group
        min_group_size: Minimum size to form a group

    Returns:
        List of refined subgroups
    """
    if len(indices) <= 1:
        return [{idx} for idx in indices]

    # Extract variables for each string
    var_patterns: dict[int, tuple[str, ...]] = {}

    for idx in indices:
        token_vals = token_values(token_lists[idx])
        vars_list, _ = extract_variables_between_anchors(token_vals, anchors)
        # Create pattern from variable values (normalized)
        pattern = tuple(v[1].strip() for v in vars_list)
        var_patterns[idx] = pattern

    # Get all patterns
    all_patterns = list(var_patterns.values())
    if not all_patterns:
        return [{idx} for idx in indices]

    num_vars = len(all_patterns[0])
    if num_vars == 0:
        return [set(indices)]

    # For each variable position, determine if it's a "template variation" slot
    # A slot is a template variation if:
    # 1. Values are short (1-2 words)
    # 2. There are only a few distinct values (not all unique)

    def is_template_variation_slot(position: int) -> bool:
        values_at_pos = [p[position] for p in all_patterns if position < len(p)]
        if not values_at_pos:
            return False

        # Check if all values are short
        all_short = all(len(v.split()) <= 2 for v in values_at_pos)
        if not all_short:
            return False

        # Check if there's some repetition (not all unique)
        unique_count = len(set(values_at_pos))
        total_count = len(values_at_pos)

        # If less than 70% unique, likely template variation
        return unique_count < total_count * 0.7

    # Identify which positions are template variations
    template_var_positions = [
        i for i in range(num_vars) if is_template_variation_slot(i)
    ]

    if not template_var_positions:
        # No template variations found - keep as single group
        return [set(indices)]

    # Build signature using only template variation positions
    def get_signature(pattern: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            pattern[i] if i < len(pattern) else "" for i in template_var_positions
        )

    # Group by signature
    sig_groups: dict[tuple[str, ...], set[int]] = defaultdict(set)
    for idx in indices:
        sig = get_signature(var_patterns[idx])
        sig_groups[sig].add(idx)

    # Return groups that meet min size, singletons for the rest
    result = []
    for group in sig_groups.values():
        if len(group) >= min_group_size:
            result.append(group)
        else:
            for idx in group:
                result.append({idx})

    return result


def _refine_group_by_structure(
    indices: set[int],
    token_lists: list[list[Token]],
    config: ExtractionConfig,
) -> list[set[int]]:
    """
    Refine a candidate group by splitting based on actual anchor compatibility.

    Strings with different anchor sequences get split into separate groups.

    Args:
        indices: Indices of strings in this candidate group
        token_lists: All tokenized strings
        config: Extraction configuration

    Returns:
        List of refined subgroups (may be singleton or empty)
    """
    if len(indices) <= 1:
        return [indices]

    idx_list = list(indices)
    token_sequences = [token_values(token_lists[i]) for i in idx_list]

    # Compute initial anchors for the whole group
    anchors = compute_anchors_for_group(
        token_sequences, skip_whitespace=config.skip_whitespace_in_anchors
    )

    if len(anchors) < config.min_anchor_tokens:
        # No good anchors - treat each as singleton
        return [{i} for i in indices]

    # Group by anchor validation
    valid_group: set[int] = set()
    invalid_indices = []

    for i, idx in enumerate(idx_list):
        if validate_anchor_sequence(token_sequences[i], anchors):
            valid_group.add(idx)
        else:
            invalid_indices.append(idx)

    result = []
    if len(valid_group) >= config.min_group_size:
        result.append(valid_group)
    else:
        # Not enough valid members - split into singletons
        for idx in valid_group:
            result.append({idx})

    # Invalid ones become singletons
    for idx in invalid_indices:
        result.append({idx})

    return result


def _merge_overlapping_groups(groups: list[set[int]]) -> list[set[int]]:
    """Merge groups that have overlapping members."""
    if not groups:
        return []

    # Use union-find approach
    merged = []
    for group in groups:
        # Find all existing groups that overlap with this one
        overlapping_indices = []
        for i, existing in enumerate(merged):
            if group & existing:
                overlapping_indices.append(i)

        if not overlapping_indices:
            merged.append(group.copy())
        else:
            # Merge all overlapping groups into one
            new_group = group.copy()
            for i in sorted(overlapping_indices, reverse=True):
                new_group |= merged.pop(i)
            merged.append(new_group)

    return merged


def _iterative_grouping(
    strings: list[str],
    token_lists: list[list[Token]],
    config: ExtractionConfig,
) -> list[set[int]]:
    """
    Perform iterative grouping to find template groups.

    1. Initial grouping by Jaccard similarity (and optionally anchor-based)
    2. Refine by anchor compatibility
    3. Further split by variable patterns if strict mode
    4. Merge overlapping groups

    Args:
        strings: Original strings
        token_lists: Tokenized versions
        config: Extraction configuration

    Returns:
        Final list of groups (sets of indices)
    """
    # Step 1: Initial candidate groups by similarity
    sim_groups = find_candidate_groups(
        token_lists,
        min_similarity=config.min_similarity,
        skip_whitespace=config.skip_whitespace_in_similarity,
        use_parallel=config.use_parallel,
        n_jobs=config.n_jobs,
    )

    # Step 1b: Also use anchor-based grouping (catches cases with low similarity)
    if config.use_anchor_grouping:
        anchor_groups = find_groups_by_common_anchors(
            token_lists,
            min_common_anchors=config.min_common_anchors,
            skip_whitespace=config.skip_whitespace_in_anchors,
            use_parallel=config.use_parallel,
            n_jobs=config.n_jobs,
        )
        # Merge groups from both strategies
        all_initial_groups = _merge_overlapping_groups(sim_groups + anchor_groups)
    else:
        all_initial_groups = sim_groups

    # Step 2: Refine each group by anchor structure
    refined_groups = []
    for group in all_initial_groups:
        subgroups = _refine_group_by_structure(group, token_lists, config)
        refined_groups.extend(subgroups)

    # Step 3: If strict mode, further split by variable patterns
    if config.strict_mode:
        strict_groups = []
        for group in refined_groups:
            if len(group) < config.min_group_size:
                strict_groups.append(group)
                continue

            # Compute anchors for this group
            group_indices = list(group)
            token_sequences = [token_values(token_lists[i]) for i in group_indices]
            anchors = compute_anchors_for_group(
                token_sequences, skip_whitespace=config.skip_whitespace_in_anchors
            )

            if len(anchors) >= config.min_anchor_tokens:
                subgroups = _split_by_variable_patterns(
                    group_indices, token_lists, anchors, config.min_group_size
                )
                strict_groups.extend(subgroups)
            else:
                strict_groups.append(group)

        refined_groups = strict_groups

    return refined_groups


def _consolidate_constant_variables(template: Template) -> Template:
    """
    Post-process a template to merge constant "variables" back into the template.

    If a variable has the same value across ALL matches, it's not really a variable -
    it should be part of the template text.

    Args:
        template: The template to consolidate

    Returns:
        New template with constant variables merged into template text
    """
    if not template.matches or len(template.matches) < 2:
        return template

    # Find which variables are constant (same value in all matches)
    all_var_names: set[str] = set()
    for match in template.matches:
        all_var_names.update(match.variables.keys())

    constant_vars: dict[str, str] = {}  # var_name -> constant_value
    for var_name in all_var_names:
        values = [m.variables.get(var_name, "") for m in template.matches]
        if len(set(values)) == 1:
            # All values are the same - this is a constant
            constant_vars[var_name] = values[0]

    if not constant_vars:
        # No constants found, return original
        return template

    # Rebuild template string by replacing constant variables with their values
    new_template_string = template.template_string
    for var_name, const_value in constant_vars.items():
        new_template_string = new_template_string.replace(
            f"{{{var_name}}}", const_value
        )

    # Rebuild elements list
    new_elements = []
    for elem in template.elements:
        if elem.is_variable and elem.value in constant_vars:
            # Convert to fixed text
            new_elements.append(
                TemplateElement(is_variable=False, value=constant_vars[elem.value])
            )
        else:
            new_elements.append(elem)

    # Merge adjacent fixed elements
    merged_elements = []
    for elem in new_elements:
        if (
            merged_elements
            and not merged_elements[-1].is_variable
            and not elem.is_variable
        ):
            # Merge with previous fixed element
            merged_elements[-1] = TemplateElement(
                is_variable=False, value=merged_elements[-1].value + elem.value
            )
        else:
            merged_elements.append(elem)

    # Renumber remaining variables (var_0, var_1, ...)
    var_mapping: dict[str, str] = {}  # old_name -> new_name
    new_var_idx = 0
    final_elements = []

    for elem in merged_elements:
        if elem.is_variable:
            old_name = elem.value
            if old_name not in var_mapping:
                var_mapping[old_name] = f"var_{new_var_idx}"
                new_var_idx += 1
            final_elements.append(
                TemplateElement(is_variable=True, value=var_mapping[old_name])
            )
        else:
            final_elements.append(elem)

    # Update template string with renumbered variables
    final_template_string = new_template_string
    for old_name, new_name in var_mapping.items():
        final_template_string = final_template_string.replace(
            f"{{{old_name}}}", f"{{{new_name}}}"
        )

    # Update matches with renumbered variables (excluding constants)
    new_matches = []
    for match in template.matches:
        new_vars = {}
        for old_name, value in match.variables.items():
            if old_name not in constant_vars:
                new_name = var_mapping.get(old_name, old_name)
                new_vars[new_name] = value
        new_matches.append(
            TemplateMatch(original_string=match.original_string, variables=new_vars)
        )

    return Template(
        template_string=final_template_string,
        elements=final_elements,
        anchor_tokens=template.anchor_tokens,
        matches=new_matches,
    )


# =============================================================================
# PUBLIC API
# =============================================================================


def extract_templates(
    strings: list[str],
    config: ExtractionConfig | None = None,
) -> ExtractionResult:
    """
    Extract templates from a list of strings.

    This is the main entry point for template extraction. It analyzes the input
    strings, groups them by common structure, and returns discovered templates
    along with variable values for each matched string.

    Args:
        strings: List of input strings (e.g., LLM traces/prompts)
        config: Extraction configuration (uses defaults if None)

    Returns:
        ExtractionResult with discovered templates and unmatched strings

    Example:
        >>> traces = [
        ...     "Hello Alice, welcome!",
        ...     "Hello Bob, welcome!",
        ...     "Goodbye everyone",
        ... ]
        >>> result = extract_templates(traces)
        >>> print(result.templates[0].template_string)
        "Hello {var_0}, welcome!"
        >>> print(result.templates[0].matches[0].variables)
        {'var_0': 'Alice'}
        >>> print(result.unmatched)
        ['Goodbye everyone']
    """
    if config is None:
        config = ExtractionConfig()

    if not strings:
        return ExtractionResult(templates=[], unmatched=[])

    # Tokenize all strings
    token_lists = [tokenize(s) for s in strings]

    # Find groups
    groups = _iterative_grouping(strings, token_lists, config)

    templates = []
    matched_indices: set[int] = set()

    # Try to create a template from each group
    for group in groups:
        if len(group) < config.min_group_size:
            continue

        group_indices = list(group)
        group_strings = [strings[i] for i in group_indices]
        group_tokens = [token_lists[i] for i in group_indices]

        template = _create_template_from_group(group_strings, group_tokens, config)
        if template:
            # Post-process: merge constant "variables" back into template
            template = _consolidate_constant_variables(template)
            templates.append(template)
            matched_indices.update(group_indices)

    # Collect unmatched strings
    unmatched = [strings[i] for i in range(len(strings)) if i not in matched_indices]

    return ExtractionResult(templates=templates, unmatched=unmatched)


def match_string_to_template(string: str, template: Template) -> TemplateMatch | None:
    """
    Try to match a single string against an existing template.

    Useful for classifying new strings against known templates.

    Args:
        string: The string to match
        template: The template to match against

    Returns:
        TemplateMatch if successful, None otherwise

    Example:
        >>> result = extract_templates(["Hello Alice!", "Hello Bob!"])
        >>> template = result.templates[0]  # "Hello {var_0}!"
        >>> match = match_string_to_template("Hello Charlie!", template)
        >>> print(match.variables)
        {'var_0': 'Charlie'}
    """
    tokens = tokenize(string)
    token_vals = token_values(tokens)

    if not validate_anchor_sequence(token_vals, template.anchor_tokens):
        return None

    vars_list, _ = extract_variables_between_anchors(token_vals, template.anchor_tokens)
    var_dict = {name: value for name, value in vars_list}

    return TemplateMatch(original_string=string, variables=var_dict)
