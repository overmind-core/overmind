"""Authored-text hygiene at creation, not grade time.

Authoring lifts quality signals verbatim from the grounding pack, so an
internal symbol or unfilled placeholder in the grounding text propagates into a
judge question as an impossible requirement. A single-braced name that is a
``VARIABLE_SOURCES`` entry is a pointer into the judge Inputs block, not a
remnant — leave it.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from overbae.services.eval.specs import VARIABLE_SOURCES

# Leading underscore is what separates a module-private constant from a
# legitimately upper-cased domain term (``JSON``, ``PII``, ``HTTP``).
_INTERNAL_SYMBOL_RE = re.compile(r"(?<![\w])_[A-Z][A-Z0-9_]{2,}\b")

# Single-braced only: double-braced ``{{var}}`` rubric variables are intentional.
_FSTRING_REMNANT_RE = re.compile(r"(?<!\{)\{[ ]*([a-zA-Z_][\w]*)[ ]*\}(?!\})")

_ALLOWED_PLACEHOLDERS = frozenset(src.lower() for src in VARIABLE_SOURCES)

# Tier-1 often wraps a card field in markdown code: `` `{trader_investment_plan}` ``.
# Stripping the disallowed `{field}` leaves an empty span the judge cannot bind.
_EMPTY_CODE_SPAN_RE = re.compile(r"`\s*`")
_EMPTY_SPAN_FILLS = ("{output}", "{input}")

# A confidence term in the item id, or leading its text, means the item is ABOUT
# confidence. The same words buried mid-sentence usually belong to a condition
# ("any confidence < 0.85") and are not a reference comparison.
_CONFIDENCE_RE = re.compile(r"\bconfidence|\bcertainty|\bcalibrat", re.I)
_COMPARISON_RE = re.compile(r"reference|expected|golden|match|equal", re.I)
# Judging whether a stated confidence is WARRANTED is a per-sample calibration
# call, which has no right answer on one row however it is phrased — against a
# golden, or against "the evidence". Checking its type or range is a contract
# check and stays legal.
_CONFIDENCE_JUDGEMENT_RE = re.compile(
    r"calibrat|reflect|appropriate|justified|warrant|align|consistent|proportional"
    r"|overconfiden|underconfiden|too (?:high|low)",
    re.I,
)
_CONFIDENCE_LEAD_CHARS = 60

_REFERENCE_VAR_RE = re.compile(
    r"\{[ ]*(?:reference|expected|expected_output)(?:\.[^{}\n]*)?[ ]*\}",
    re.I,
)
_GOLDEN_TERM_RE = re.compile(r"\b(?:reference|expected|golden)\b", re.I)
_COMPARE_PHRASE_RE = re.compile(
    r"\b(?:compar(?:e|ed|ing|ison)|equal(?:s|ed|ing|ity)?|match(?:es|ed|ing)?)\b",
    re.I,
)


def _fill_empty_code_spans(text: str) -> tuple[str, list[str]]:
    unused = [token for token in _EMPTY_SPAN_FILLS if token not in text]
    filled: list[str] = []

    def _fill(_match: re.Match[str]) -> str:
        filled.append("``")
        if unused:
            return unused.pop(0)
        return "{output}"

    return _EMPTY_CODE_SPAN_RE.sub(_fill, text), filled


def sanitize_authored_text(text: str) -> tuple[str, list[str]]:
    """Returns ``(cleaned_text, removed_tokens)``."""
    if not text:
        return text, []

    removed: list[str] = []

    def _drop_symbol(match: re.Match[str]) -> str:
        removed.append(match.group(0))
        return ""

    def _drop_placeholder(match: re.Match[str]) -> str:
        if match.group(1).lower() in _ALLOWED_PLACEHOLDERS:
            return match.group(0)
        removed.append(match.group(0))
        return ""

    cleaned = _INTERNAL_SYMBOL_RE.sub(_drop_symbol, text)
    cleaned = _FSTRING_REMNANT_RE.sub(_drop_placeholder, cleaned)
    cleaned, span_fills = _fill_empty_code_spans(cleaned)
    removed.extend(span_fills)

    if not removed:
        return text, []

    # Close the gaps the removals leave: "the (_X) key" -> "the key".
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:?!])", r"\1", cleaned)
    return cleaned.strip(), removed


def contains_leaked_token(text: str) -> bool:
    if not text:
        return False
    return bool(sanitize_authored_text(text)[1])


def grades_stated_confidence(item: dict) -> bool:
    question = str(item.get("q") or "")
    about_confidence = bool(
        _CONFIDENCE_RE.search(str(item.get("id") or ""))
        or _CONFIDENCE_RE.search(question[:_CONFIDENCE_LEAD_CHARS])
    )
    if not about_confidence:
        return False
    return bool(_COMPARISON_RE.search(question) or _CONFIDENCE_JUDGEMENT_RE.search(question))


def is_mechanical_field_compare(question: str, covered_fields: Collection[str]) -> bool:
    """A judgement that names a covered field but needs reading the input stays."""
    if not question or not covered_fields:
        return False
    if not _names_a_covered_field(question, covered_fields):
        return False
    if _REFERENCE_VAR_RE.search(question):
        return True
    return bool(_COMPARE_PHRASE_RE.search(question) and _GOLDEN_TERM_RE.search(question))


def _names_a_covered_field(question: str, covered_fields: Collection[str]) -> bool:
    # Alnum boundaries (underscore excluded) so `char_interval` matches inside
    # `char_interval_present` but never inside an unrelated longer word.
    return any(
        name and re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", question, re.I)
        for name in covered_fields
    )


_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Equality always matches. A shared run shorter than this collides on words
# like "amount" or "the email".
_SHARED_RUN = 20
CHECKLIST_CLUSTER_JACCARD = 0.5


def item_fingerprint(text: str) -> str:
    stripped = _PLACEHOLDER_RE.sub(" ", (text or "").casefold())
    return " ".join(_NON_ALNUM_RE.sub(" ", stripped).split())


def _fingerprints_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if shorter in longer:
        return len(shorter) >= _SHARED_RUN
    if len(shorter) < _SHARED_RUN:
        return False
    return any(
        shorter[i : i + _SHARED_RUN] in longer for i in range(len(shorter) - _SHARED_RUN + 1)
    )


def checklist_jaccard(left: Collection[str], right: Collection[str]) -> float:
    """|matched| / |union| over fingerprint equality or a shared ≥20-char run."""
    fingerprints_a = [item_fingerprint(q) for q in left]
    fingerprints_b = [item_fingerprint(q) for q in right]
    if not fingerprints_a and not fingerprints_b:
        return 1.0
    if not fingerprints_a or not fingerprints_b:
        return 0.0
    used = [False] * len(fingerprints_b)
    matched = 0
    for a in fingerprints_a:
        for i, b in enumerate(fingerprints_b):
            if used[i]:
                continue
            if _fingerprints_match(a, b):
                used[i] = True
                matched += 1
                break
    union = len(fingerprints_a) + len(fingerprints_b) - matched
    return matched / union if union else 0.0
