"""Work the assistant leaves for the customer's coding agent, and that agent's report."""

from overbae.core.errors import InputValidationError
from overbae.models import AssistantHandoff

LISTED = 20
# Instructions and a report each travel in one MCP result.
MAX_CHARS = 20_000


def checked(instructions) -> str:
    if not isinstance(instructions, str) or not instructions.strip():
        raise InputValidationError("instructions is the text the coding agent reads.")
    if len(instructions) > MAX_CHARS:
        raise InputValidationError(f"instructions is at most {MAX_CHARS:,} characters.")
    return instructions


def open_for(thread, call: str, instructions: str) -> AssistantHandoff:
    """The handoff of one tool call: a step that runs again finds the first one."""
    return AssistantHandoff.objects.get_or_create(
        thread=thread, call=call, defaults={"instructions": instructions}
    )[0]


def waiting(project) -> tuple[list[AssistantHandoff], int]:
    """The oldest open handoffs of the project, and how many are open in all."""
    found = AssistantHandoff.objects.filter(
        thread__project=project, state=AssistantHandoff.State.OPEN
    )
    return list(found[:LISTED]), found.count()


def complete(project, handoff_id, result: str) -> AssistantHandoff:
    """Record the agent's report. The waiting thread resumes at the next reconcile."""
    done = AssistantHandoff.objects.filter(
        pk=handoff_id, thread__project=project, state=AssistantHandoff.State.OPEN
    ).update(state=AssistantHandoff.State.DONE, result=result)
    if not done:
        raise InputValidationError("This handoff is not open in this project.")
    return AssistantHandoff.objects.get(pk=handoff_id)
