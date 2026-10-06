"""What every ``overmind`` command shares: connection flags, output, and exit codes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

console = Console()

# ``--api-key`` has no envvar on purpose: Typer would hand OVERMIND_API_KEY over
# as if it were the flag, and the flag outranks the saved project key.
ApiKeyOption = Annotated[str, typer.Option("--api-key", help="Overmind API key", show_default=False)]
ApiUrlOption = Annotated[str, typer.Option("--api-url", envvar="OVERMIND_API_URL", help="Overmind backend base URL")]
ConfigPathOption = Annotated[Path, typer.Option("--path", help="Path to overmind.toml")]
ProjectIdOption = Annotated[str, typer.Option("--project-id", help="Project UUID")]
JsonOption = Annotated[bool, typer.Option("--json", help="Print machine-readable output")]


def emit_json(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False))


def emit_error(message: str, *, as_json: bool) -> None:
    if as_json:
        emit_json({"error": message})
    else:
        console.print(f"[red]{message}[/red]")


@contextmanager
def guard(*errors: type[Exception], as_json: bool = False, code: int = 1) -> Iterator[None]:
    """Turn the listed failures into one error line and a non-zero exit.

    An exception with an integer ``code`` attribute exits with that code.
    """
    try:
        yield
    except errors as exc:
        emit_error(str(exc), as_json=as_json)
        exit_code = getattr(exc, "code", code)
        raise typer.Exit(exit_code if isinstance(exit_code, int) else code) from exc
