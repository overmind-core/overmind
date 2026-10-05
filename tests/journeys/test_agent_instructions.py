import asyncio
import json
import re

import pytest
from factories import make_project, make_user
from overmind.__main__ import app
from typer.testing import CliRunner

from overbae.models import APIToken
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.prompts import PROMPTS, get_prompt
from overbae.services.mcp.resources import read_resource, resource_list

COMMAND = re.compile(r"`(overmind [^`]+)`")
PLACEHOLDER = re.compile(r"^(?:[A-Z][A-Z_]*|<[^>]+>|\{[^}]+\})$")


def _prompt_commands() -> dict[str, set[str]]:
    found = {}
    for prompt in PROMPTS:
        arguments = {a.name: "x" for a in prompt.as_mcp_prompt().arguments or []}
        text = get_prompt(prompt.name, arguments).messages[0].content.text
        found[f"prompt {prompt.name}"] = set(COMMAND.findall(text))
    return found


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _resource_commands() -> dict[str, set[str]]:
    user = make_user()
    context = MCPContext(
        user=user,
        token=APIToken(scope={"scope": "project", "permission": ["read"]}),
        project=make_project(member=user),
    )

    async def read(uri):
        with bind_context(context):
            return json.loads((await read_resource(uri))[0].content)

    found = {}
    for resource in resource_list():
        payload = asyncio.run(read(str(resource.uri)))
        commands = set()
        for text in _strings(payload):
            commands |= set(COMMAND.findall(text))
            if text.startswith("overmind "):
                commands.add(text)
        found[f"resource {resource.uri}"] = commands
    return found


def _refusal(command: str) -> str:
    argv = ["x" if PLACEHOLDER.match(word) else word for word in command.split()[1:]]
    result = CliRunner().invoke(app, [*argv, "--help"])
    if result.exit_code == 0:
        return ""
    lines = [line.strip("│╭╰─ ") for line in result.output.splitlines()]
    return (
        " ".join(line for line in lines if line and line != "Error") or f"exit {result.exit_code}"
    )


@pytest.mark.django_db
def test_every_command_the_mcp_surface_hands_an_agent_parses():
    sources = {**_prompt_commands(), **_resource_commands()}
    commands = {(where, command) for where, found in sources.items() for command in found}
    assert len(commands) >= 8

    refusals = {(where, command): _refusal(command) for where, command in sorted(commands)}
    assert {key: why for key, why in refusals.items() if why} == {}
