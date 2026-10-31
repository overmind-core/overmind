"""Keep agent instructions inside the limits the coding agents load.

Codex stops reading AGENTS.md at 32 KiB, so the playbook keeps half that as headroom.
Claude Code and Cursor advise SKILL.md under 500 lines, with detail in linked files.
"""

import sys
from pathlib import Path

AGENTS_MAX_BYTES = 16 * 1024
SKILL_MAX_LINES = 500


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    errors = []
    size = (root / "AGENTS.md").stat().st_size
    if size > AGENTS_MAX_BYTES:
        errors.append(f"AGENTS.md is {size} bytes; the limit is {AGENTS_MAX_BYTES}.")
    for skill in sorted((root / ".agents" / "skills").glob("*/SKILL.md")):
        lines = len(skill.read_text(encoding="utf-8").splitlines())
        if lines > SKILL_MAX_LINES:
            errors.append(
                f"{skill.relative_to(root)} has {lines} lines; the limit is {SKILL_MAX_LINES}. "
                "Move detail into a linked file beside it."
            )
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
