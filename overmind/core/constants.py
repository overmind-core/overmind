"""Shared filesystem constants for Overmind state under the project root."""

from __future__ import annotations

# Directory created by ``overmind init``; its presence marks the project root.
OVERMIND_DIR_NAME = ".overmind"


def overmind_rel(*segments: str) -> str:
    """Build a POSIX-style path under the state dir for user-facing messages."""
    return "/".join((OVERMIND_DIR_NAME, *segments))
