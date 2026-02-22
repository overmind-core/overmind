"""
Helper functions for template extraction.

This module contains tokenization, alignment algorithms, and similarity computation.
"""

from __future__ import annotations

import multiprocessing
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, FrozenSet, List, Optional, Set, Tuple


# =============================================================================
# TOKEN DATA MODEL
# =============================================================================


@dataclass
class Token:
    """A single token from a string."""

    value: str
    start: int  # Character position in original string
    end: int


# =============================================================================
# TOKENIZER
# =============================================================================

# Pattern to split on word boundaries while preserving punctuation as separate tokens
# Matches: words, numbers, or individual punctuation/symbols
_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]|\s+")


def tokenize(text: str) -> List[Token]:
    """
    Tokenize a string into a list of tokens.

    Splits on word boundaries, keeping punctuation as separate tokens.
    Whitespace is preserved as tokens to maintain structure.

    Args:
        text: The input string to tokenize

    Returns:
        List of Token objects with value and position info
    """
    tokens = []
    for match in _TOKEN_PATTERN.finditer(text):
        value = match.group()
        tokens.append(Token(value=value, start=match.start(), end=match.end()))
    return tokens


def tokens_to_string(tokens: List[Token]) -> str:
    """Reconstruct a string from tokens."""
    return "".join(t.value for t in tokens)


def token_values(tokens: List[Token]) -> List[str]:
    """Extract just the string values from a list of tokens."""
    return [t.value for t in tokens]


def is_whitespace_token(token: Token) -> bool:
    """Check if a token is pure whitespace."""
    return token.value.isspace()


def normalize_tokens(
    tokens: List[Token], collapse_whitespace: bool = False
) -> List[Token]:
    """
    Normalize a token list.

    Args:
        tokens: List of tokens to normalize
        collapse_whitespace: If True, collapse consecutive whitespace into single space

    Returns:
        Normalized token list
    """
    if not collapse_whitespace:
        return tokens

    result = []
    prev_was_whitespace = False
    pos = 0

    for token in tokens:
        if is_whitespace_token(token):
            if not prev_was_whitespace:
                result.append(Token(value=" ", start=pos, end=pos + 1))
                pos += 1
            prev_was_whitespace = True
        else:
            result.append(
                Token(value=token.value, start=pos, end=pos + len(token.value))
            )
            pos += len(token.value)
            prev_was_whitespace = False

    return result


# =============================================================================
# ALIGNMENT ALGORITHMS
# =============================================================================


def lcs_tokens(seq1: List[str], seq2: List[str]) -> List[str]:
    """
    Find the Longest Common Subsequence of two token sequences.

    Uses difflib.SequenceMatcher for better performance (C implementation).

    Args:
        seq1: First sequence of token values
        seq2: Second sequence of token values

    Returns:
        List of tokens in the LCS (in order)
    """
    matcher = SequenceMatcher(None, seq1, seq2, autojunk=False)

    lcs = []
    for match in matcher.get_matching_blocks():
        if match.size > 0:
            lcs.extend(seq1[match.a : match.a + match.size])

    return lcs


def multi_lcs(sequences: List[List[str]]) -> List[str]:
    """
    Find the LCS across multiple sequences.

    Iteratively computes LCS of current result with each new sequence.

    Args:
        sequences: List of token value sequences

    Returns:
        LCS tokens common to all sequences (in order)
    """
    if not sequences:
        return []
    if len(sequences) == 1:
        return list(sequences[0])

    result = list(sequences[0])
    for seq in sequences[1:]:
        result = lcs_tokens(result, seq)
        if not result:
            break

    return result


def get_non_whitespace_tokens(tokens: List[str]) -> List[str]:
    """Get only non-whitespace tokens."""
    return [t for t in tokens if not t.isspace()]


# Tokens that should not be considered as anchors because they commonly
# appear in variable content (e.g., JSON, code snippets, placeholders)
# Note: apostrophe ' is NOT excluded since it's common in natural language
EXCLUDED_ANCHOR_TOKENS = frozenset({"{", "}", "[", "]", '"', "`", "\\"})


def get_anchor_candidate_tokens(tokens: List[str]) -> List[str]:
    """
    Get tokens that are candidates for being anchors.

    Excludes whitespace and tokens that commonly appear in variable content
    (like curly braces which appear in JSON/code).
    """
    return [t for t in tokens if not t.isspace() and t not in EXCLUDED_ANCHOR_TOKENS]


def compute_anchors_for_group(
    token_sequences: List[List[str]],
    skip_whitespace: bool = True,
    exclude_problematic: bool = True,
) -> List[str]:
    """
    Compute anchor tokens for a group of token sequences.

    Anchors are the LCS of filtered tokens across all sequences.

    Args:
        token_sequences: List of token value lists
        skip_whitespace: If True, don't consider whitespace as anchors
        exclude_problematic: If True, exclude tokens like {, }, [, ] that
                            commonly appear in variable content

    Returns:
        List of anchor token values
    """
    if exclude_problematic:
        filtered = [get_anchor_candidate_tokens(seq) for seq in token_sequences]
    elif skip_whitespace:
        filtered = [get_non_whitespace_tokens(seq) for seq in token_sequences]
    else:
        filtered = token_sequences

    return multi_lcs(filtered)


def find_anchor_positions(tokens: List[str], anchors: List[str]) -> List[int]:
    """
    Find positions of anchor tokens within a token sequence.

    Anchors must appear in order. Returns position indices.

    Args:
        tokens: The full token sequence (may include whitespace)
        anchors: The anchor tokens to find (in order, no whitespace)

    Returns:
        List of position indices for each anchor
    """
    positions = []
    search_start = 0

    for anchor in anchors:
        for i in range(search_start, len(tokens)):
            if tokens[i] == anchor:
                positions.append(i)
                search_start = i + 1
                break

    return positions


def validate_anchor_sequence(tokens: List[str], anchors: List[str]) -> bool:
    """
    Check if anchors appear in the token sequence in the correct order.

    Args:
        tokens: The full token sequence
        anchors: The expected anchor sequence

    Returns:
        True if all anchors appear in order
    """
    positions = find_anchor_positions(tokens, anchors)
    return len(positions) == len(anchors)


def extract_variables_between_anchors(
    tokens: List[str], anchors: List[str]
) -> Tuple[List[Tuple[str, str]], str]:
    """
    Extract variable values and build template string.

    This is the core extraction logic that:
    1. Finds anchor positions in the token sequence
    2. Extracts content between anchors as variables
    3. Includes whitespace with anchors (not as variables)

    Args:
        tokens: The full token sequence
        anchors: The anchor tokens (non-whitespace)

    Returns:
        Tuple of (list of (var_name, value) pairs, template_string)
    """
    if not anchors:
        # No anchors - entire string is one variable
        return [("var_0", "".join(tokens))], "{var_0}"

    positions = find_anchor_positions(tokens, anchors)
    if len(positions) != len(anchors):
        # Invalid - anchors not found
        return [("var_0", "".join(tokens))], "{var_0}"

    variables = []
    template_parts = []
    var_idx = 0

    # Handle content before first anchor
    if positions[0] > 0:
        before_content = tokens[: positions[0]]
        # Separate leading whitespace (goes to template) from variable content
        var_start = 0
        for i, t in enumerate(before_content):
            if not t.isspace():
                var_start = i
                break
        else:
            var_start = len(before_content)

        if var_start > 0:
            template_parts.append("".join(before_content[:var_start]))

        var_content = before_content[var_start:]
        if var_content:
            # Strip trailing whitespace from variable
            var_end = len(var_content)
            for i in range(len(var_content) - 1, -1, -1):
                if not var_content[i].isspace():
                    var_end = i + 1
                    break
            else:
                var_end = 0

            if var_end > 0:
                variables.append((f"var_{var_idx}", "".join(var_content[:var_end])))
                template_parts.append(f"{{var_{var_idx}}}")
                var_idx += 1
                # Add trailing whitespace to template
                if var_end < len(var_content):
                    template_parts.append("".join(var_content[var_end:]))

    # Process each anchor and content after it
    for i, anchor_pos in enumerate(positions):
        # Add the anchor to template
        template_parts.append(anchors[i])

        # Determine end of this segment
        if i + 1 < len(positions):
            segment_end = positions[i + 1]
        else:
            segment_end = len(tokens)

        # Content after this anchor (before next anchor or end)
        segment = tokens[anchor_pos + 1 : segment_end]

        if not segment:
            continue

        # Find where whitespace ends (leading ws goes to template)
        content_start = 0
        for j, t in enumerate(segment):
            if not t.isspace():
                content_start = j
                break
        else:
            # All whitespace
            template_parts.append("".join(segment))
            continue

        # Find where content ends (trailing ws goes to template)
        content_end = len(segment)
        for j in range(len(segment) - 1, -1, -1):
            if not segment[j].isspace():
                content_end = j + 1
                break

        # Leading whitespace -> template
        if content_start > 0:
            template_parts.append("".join(segment[:content_start]))

        # Variable content (non-whitespace core)
        var_content = segment[content_start:content_end]
        if var_content:
            variables.append((f"var_{var_idx}", "".join(var_content)))
            template_parts.append(f"{{var_{var_idx}}}")
            var_idx += 1

        # Trailing whitespace -> template
        if content_end < len(segment):
            template_parts.append("".join(segment[content_end:]))

    template_string = "".join(template_parts)
    return variables, template_string


def build_template_from_group(
    token_sequences: List[List[str]], anchors: List[str]
) -> Tuple[str, List[List[Tuple[str, str]]]]:
    """
    Build a unified template string and extract variables for all sequences.

    The template string is built from the first sequence as reference,
    then validated against all sequences.

    Args:
        token_sequences: List of token sequences
        anchors: Common anchor tokens

    Returns:
        Tuple of (template_string, list of variable lists for each sequence)
    """
    if not token_sequences:
        return "", []

    # Build template from first sequence
    first_vars, template_string = extract_variables_between_anchors(
        token_sequences[0], anchors
    )

    all_variables = [first_vars]

    # Extract variables from remaining sequences
    for seq in token_sequences[1:]:
        vars_list, _ = extract_variables_between_anchors(seq, anchors)
        all_variables.append(vars_list)

    return template_string, all_variables


# =============================================================================
# SIMILARITY COMPUTATION
# =============================================================================


def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    """
    Compute Jaccard similarity between two sets.

    Jaccard = |intersection| / |union|

    Returns:
        Float between 0.0 and 1.0
    """
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0

    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union


def token_set(tokens: List[Token], skip_whitespace: bool = True) -> Set[str]:
    """
    Convert a token list to a set of unique token values.

    Args:
        tokens: List of tokens
        skip_whitespace: If True, exclude whitespace-only tokens

    Returns:
        Set of unique token string values
    """
    if skip_whitespace:
        return {t.value for t in tokens if not t.value.isspace()}
    return {t.value for t in tokens}


def compute_similarity_pair(
    args: Tuple[int, int, FrozenSet[str], FrozenSet[str]],
) -> Tuple[int, int, float]:
    """Compute similarity for a single pair (for parallel processing)."""
    i, j, set_i, set_j = args
    sim = jaccard_similarity(set_i, set_j)
    return i, j, sim


def find_candidate_groups(
    token_lists: List[List[Token]],
    min_similarity: float = 0.3,
    skip_whitespace: bool = True,
    use_parallel: bool = True,
    n_jobs: Optional[int] = None,
) -> List[Set[int]]:
    """
    Find groups of strings that are candidates for sharing a template.

    Uses connected components in the similarity graph where edges exist
    between strings with similarity >= min_similarity.

    Args:
        token_lists: List of tokenized strings
        min_similarity: Minimum Jaccard similarity to consider two strings related
        skip_whitespace: If True, exclude whitespace when computing similarity
        use_parallel: If True, use parallel processing for large inputs
        n_jobs: Number of parallel workers (default: CPU count)

    Returns:
        List of sets, each set containing indices of strings in the same candidate group
    """
    n = len(token_lists)
    if n == 0:
        return []

    # Build token sets (use frozenset for hashability in parallel processing)
    sets = [frozenset(token_set(tokens, skip_whitespace)) for tokens in token_lists]

    # Build adjacency list for similarity graph
    adj: Dict[int, Set[int]] = defaultdict(set)

    # For small inputs, use simple loop; for large inputs, parallelize
    if n < 20 or not use_parallel:
        # Simple sequential computation
        for i in range(n):
            for j in range(i + 1, n):
                # Quick pre-filter: if sets are very different in size, skip
                len_i, len_j = len(sets[i]), len(sets[j])
                if len_i > 0 and len_j > 0:
                    size_ratio = min(len_i, len_j) / max(len_i, len_j)
                    if size_ratio < min_similarity:
                        continue

                sim = jaccard_similarity(sets[i], sets[j])
                if sim >= min_similarity:
                    adj[i].add(j)
                    adj[j].add(i)
    else:
        # Parallel computation for larger inputs
        if n_jobs is None:
            n_jobs = min(multiprocessing.cpu_count(), 8)

        # Generate pairs to compute (with pre-filtering)
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                len_i, len_j = len(sets[i]), len(sets[j])
                if len_i > 0 and len_j > 0:
                    size_ratio = min(len_i, len_j) / max(len_i, len_j)
                    if size_ratio < min_similarity:
                        continue
                pairs.append((i, j, sets[i], sets[j]))

        # Use ThreadPoolExecutor (better for I/O-bound, also avoids pickle issues)
        with ThreadPoolExecutor(max_workers=n_jobs) as executor:
            results = executor.map(compute_similarity_pair, pairs)

        for i, j, sim in results:
            if sim >= min_similarity:
                adj[i].add(j)
                adj[j].add(i)

    # Find connected components using BFS
    visited: Set[int] = set()
    groups = []

    for start in range(n):
        if start in visited:
            continue

        # BFS from this node
        group: Set[int] = set()
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            group.add(node)
            for neighbor in adj[node]:
                if neighbor not in visited:
                    queue.append(neighbor)

        groups.append(group)

    return groups


def compute_lcs_pair(
    args: Tuple[int, int, List[str], List[str], int],
) -> Tuple[int, int, int]:
    """Compute LCS length for a single pair (for parallel processing)."""
    i, j, seq_i, seq_j, _min_anchors = args
    lcs = lcs_tokens(seq_i, seq_j)
    return i, j, len(lcs)


def find_groups_by_common_anchors(
    token_lists: List[List[Token]],
    min_common_anchors: int = 3,
    skip_whitespace: bool = True,
    use_parallel: bool = True,
    n_jobs: Optional[int] = None,
) -> List[Set[int]]:
    """
    Alternative grouping strategy: group by common anchor tokens.

    This is more effective when variable content dominates (low Jaccard similarity)
    but the template structure (anchors) is clear.

    Args:
        token_lists: List of tokenized strings
        min_common_anchors: Minimum number of common anchor tokens to form a group
        skip_whitespace: If True, exclude whitespace when looking for anchors
        use_parallel: If True, use parallel processing for large inputs
        n_jobs: Number of parallel workers (default: CPU count)

    Returns:
        List of sets, each set containing indices of strings that share anchors
    """
    n = len(token_lists)
    if n == 0:
        return []

    # Get anchor candidate tokens for each string
    if skip_whitespace:
        token_seqs = [
            get_anchor_candidate_tokens([t.value for t in tl]) for tl in token_lists
        ]
    else:
        token_seqs = [[t.value for t in tl] for tl in token_lists]

    # Build adjacency based on LCS length
    adj: Dict[int, Set[int]] = defaultdict(set)

    # For small inputs, use simple loop
    if n < 15 or not use_parallel:
        for i in range(n):
            for j in range(i + 1, n):
                # Quick pre-filter: if one sequence is too short, skip
                if (
                    len(token_seqs[i]) < min_common_anchors
                    or len(token_seqs[j]) < min_common_anchors
                ):
                    continue

                lcs = lcs_tokens(token_seqs[i], token_seqs[j])
                if len(lcs) >= min_common_anchors:
                    adj[i].add(j)
                    adj[j].add(i)
    else:
        # Parallel computation
        if n_jobs is None:
            n_jobs = min(multiprocessing.cpu_count(), 8)

        # Generate pairs (with pre-filtering)
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                if (
                    len(token_seqs[i]) >= min_common_anchors
                    and len(token_seqs[j]) >= min_common_anchors
                ):
                    pairs.append(
                        (i, j, token_seqs[i], token_seqs[j], min_common_anchors)
                    )

        # Use ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=n_jobs) as executor:
            results = executor.map(compute_lcs_pair, pairs)

        for i, j, lcs_len in results:
            if lcs_len >= min_common_anchors:
                adj[i].add(j)
                adj[j].add(i)

    # Find connected components using BFS
    visited: Set[int] = set()
    groups = []

    for start in range(n):
        if start in visited:
            continue

        group: Set[int] = set()
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            group.add(node)
            for neighbor in adj[node]:
                if neighbor not in visited:
                    queue.append(neighbor)

        groups.append(group)

    return groups
