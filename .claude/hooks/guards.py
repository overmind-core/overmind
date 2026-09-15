"""Vendor-neutral guard decisions. The Claude Code and Cursor hook entrypoints
translate these into their own wire formats."""

import re

_RULES = [
    (
        # gh pr edit aborts on this repo (Projects-classic GraphQL deprecation)
        # while looking like it worked. REST is the reliable path.
        r"(^|[;&|]\s*)gh\s+pr\s+edit\b",
        "deny",
        "gh pr edit fails on this repo (Projects-classic GraphQL). Use "
        "gh api -X PATCH repos/overmind-core/platform/pulls/<n> --input pr.json "
        "(see the pr-etiquette skill).",
    ),
    (
        # No node binary on this machine — only bun. Catch it before a 127 exit.
        r"(^|[;&|]\s*)node\s",
        "deny",
        "There is no 'node' on this machine. Run scripts with 'bun' instead.",
    ),
    (
        r"git push[^;&|]*[\s:/](main|master)(?=$|[\s;&|])",
        "deny",
        "Direct push to main is not allowed. Push to the current feature branch and open a PR.",
    ),
    (
        r"(^|[;&|]\s*)make clean\b|docker compose down[^;&|]*-v\b",
        "ask",
        "This destroys the local postgres/app volumes (docker compose down -v).",
    ),
    (
        # A _merge_ migration or a reused number fails the migrations CI job.
        r"makemigrations[^;&|]*--merge\b",
        "deny",
        "Never resolve a migration fork with --merge. Rebase on origin/main, delete "
        "your migration and regenerate it (pr-etiquette skill, Migrations).",
    ),
    (
        # biome 2.5.x sorts svg attributes and corrupted a logo's XML prolog.
        r"biome[^;&|]*--write[^;&|]*\.svg|biome[^;&|]*\.svg[^;&|]*--write",
        "deny",
        "Never run biome --write over .svg files; .svg is excluded in biome.json for a reason.",
    ),
    (
        r"(^|[;&|]\s*)git commit[^;&|]*Co-[Aa]uthored-[Bb]y",
        "deny",
        "Commit messages carry no co-author trailers: short subject plus at most one body line.",
    ),
]

_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def check_command(command):
    """Return (decision, reason) for a shell command, or None to stay out of the way.

    Quoted strings are blanked first so a grep for a guarded phrase is not itself denied.
    The commit-trailer rule reads the raw command because the trailer lives in the message.
    """
    if not command:
        return None
    bare = _QUOTED.sub("''", command)
    for pattern, decision, reason in _RULES:
        haystack = command if "Co-" in pattern else bare
        if re.search(pattern, haystack):
            return decision, reason
    return None


def is_generated_path(path):
    return "/frontend/src/openapi/" in (path or "")


def needs_uv_resync(command):
    """True after `uv add` / `uv remove`, which drop the dev and test groups from the venv."""
    return bool(command) and re.search(r"(^|[;&|]\s*)uv\s+(add|remove)\b", command) is not None
