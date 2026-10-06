"""Slug rules shared by the runtime decorators, the AST scan and ``overmind sync``.

They match the platform's ``Capability.slug`` / ``Behaviour.slug`` convention, so a
capability named at runtime and the same one found by the scan resolve to one row.
"""

from __future__ import annotations

import re

_NON_SLUG_RUN = re.compile(r"[^a-z0-9]+")
_KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def identity_slug(value: str) -> str:
    return _NON_SLUG_RUN.sub("-", value.lower()).strip("-")


def project_slug(name: str) -> str:
    return (identity_slug(name.strip()) or "project")[:80]


def is_slug(value: str) -> bool:
    return bool(_KEBAB.fullmatch(value))


def split_capability_reference(reference: str) -> tuple[str, str | None]:
    """``(display, slug)`` for a positional capability reference.

    A kebab-case reference is its own slug and display name; anything else is a
    display name with a derived slug.
    """
    reference = str(reference).strip()
    if is_slug(reference):
        return reference, reference
    return reference, identity_slug(reference) or None
