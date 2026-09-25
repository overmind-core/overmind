"""Overmind CLI — optimise LLM agents and manage agent skills.

Commands:
    init                      Install skills, slash commands, MCP; seed overmind.toml.
    sync                      Two-way sync of overmind.toml with the server.
    chassis                   Print the deterministic AST digest the local scan uses as ground truth.
    dataset upload FILE       Upload a local dataset and start a build.
    dataset export DATASET    Download a committed dataset locally.
    connector add TYPE        Add a tracing connector from env or a TTY prompt.
    model download-checkpoint DEPLOYMENT
                              Download a deployed model checkpoint locally.
    optimise                  Client-driven optimiser loop (skill generates diffs; server scores).
    backtest                  Score models on stored LLM calls.
    finetune                  Start fine-tunes from stored LLM calls.
    skills                    Manage Overmind agent skills.

Use --help with any command or subcommand for details.
"""
# Allow ``python -m overmind …`` when a different ``overmind`` script shadows PATH.

try:
    import time

    import typer
    from rich.console import Console

    from overmind.analytics import cli_command_from_argv, track_cli_invocation
    from overmind.chassis import chassis as chassis_cmd
    from overmind.connector_cmd import connector_app
    from overmind.dataset_cmd import dataset_app
    from overmind.init_cmd import init as init_cmd
    from overmind.layer_cmd import backtest as backtest_cmd
    from overmind.layer_cmd import finetune as finetune_cmd
    from overmind.model_cmd import model_app
    from overmind.optimizer_cmd import OPTIMISE_HELP, optimise_app
    from overmind.skills import skills_app
    from overmind.sync import sync as sync_cmd
except ImportError as exc:
    raise ImportError("The Overmind CLI failed to import. Reinstall with: pip install overmind") from exc


app = typer.Typer(
    name="overmind",
    help="Overmind CLI — local skills, sync, tracing, optimiser client.",
    epilog="Overmind https://overmindlab.ai is a tool for optimising LLM agents and managing agent skills.",
    pretty_exceptions_enable=True,
    no_args_is_help=True,
)

console = Console()


def version_callback(value: bool):
    if value:
        from overmind import __version__

        typer.echo(f"Overmind CLI Version: {__version__}")
        raise typer.Exit()


@app.callback()
def common(
    ctx: typer.Context,
    version: bool = typer.Option(None, "--version", callback=version_callback),
):
    # Same pattern as the HQ usage path: one event when the CLI process closes,
    # carrying the exact subcommand path, exit code, and duration.
    started = time.perf_counter()

    track_cli_invocation(
        event="cli.invoked",
        command=cli_command_from_argv() or ctx.invoked_subcommand,
        exit_code=int(getattr(ctx, "exit_code", 0) or 0),
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
    )

    def on_command_complete():
        track_cli_invocation(
            event="cli.completed",
            command=cli_command_from_argv() or ctx.invoked_subcommand,
            exit_code=int(getattr(ctx, "exit_code", 0) or 0),
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )

    ctx.call_on_close(on_command_complete)


app.add_typer(optimise_app, name="optimise", help=OPTIMISE_HELP)

app.add_typer(skills_app, name="skills")
app.add_typer(dataset_app, name="dataset")
app.add_typer(connector_app, name="connector")
app.add_typer(model_app, name="model")
app.command("init")(init_cmd)
app.command("sync")(sync_cmd)
app.command("chassis")(chassis_cmd)
app.command("backtest")(backtest_cmd)
app.command("finetune")(finetune_cmd)


if __name__ == "__main__":
    app()
