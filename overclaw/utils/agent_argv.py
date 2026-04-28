"""Mask Overclaw's CLI ``sys.argv`` while importing or invoking user agent code.

Third-party agents sometimes call ``ArgumentParser.parse_args()`` with no explicit
arguments, which reads the parent process argv. Under ``overclaw optimize …``
that surfaces as bogus errors (e.g. ``unrecognized arguments: optimize …``).
"""

from __future__ import annotations

import contextlib
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

_lock = threading.Lock()


@contextlib.contextmanager
def isolated_agent_argv(
    agent_path: str | None = None, argv0: str | None = None
) -> Iterator[None]:
    """Replace ``sys.argv`` with ``[<basename>]`` for the duration of the block."""
    label = argv0 if argv0 is not None else Path(agent_path or "agent.py").name
    with _lock:
        saved = sys.argv[:]
        sys.argv = [label]
        try:
            yield
        finally:
            sys.argv = saved
