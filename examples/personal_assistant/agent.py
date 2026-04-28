"""
Personal Assistant Supervisor Example

This example demonstrates the tool calling pattern for multi-agent systems.
A supervisor agent coordinates specialized sub-agents (calendar and email)
that are wrapped as tools.
"""

from langchain.tools import tool
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

# ============================================================================
# Step 1: Define low-level API tools (stubbed)
# ============================================================================

@tool
def create_calendar_event(
    title: str,
    start_time: str,  # ISO format: "2024-01-15T14:00:00"
    end_time: str,    # ISO format: "2024-01-15T15:00:00"
    attendees: list[str],  # email addresses
    location: str = ""
) -> str:
    """Create a calendar event. Requires exact ISO datetime format."""
    return f"Event created: {title} from {start_time} to {end_time} with {len(attendees)} attendees"


@tool
def send_email(
    to: list[str],      # email addresses
    subject: str,
    body: str,
    cc: list[str] = []
) -> str:
    """Send an email via email API. Requires properly formatted addresses."""
    return f"Email sent to {', '.join(to)} - Subject: {subject}"


@tool
def get_available_time_slots(
    attendees: list[str],
    date: str,  # ISO format: "2024-01-15"
    duration_minutes: int
) -> list[str]:
    """Check calendar availability for given attendees on a specific date."""
    return ["09:00", "14:00", "16:00"]


# ============================================================================
# Step 2: Create specialized sub-agents
# ============================================================================

model = init_chat_model("gpt-5.4")  # for example

calendar_agent = create_agent(
    model,
    tools=[create_calendar_event, get_available_time_slots],
    system_prompt=(
        "You are a calendar scheduling assistant. "
        "Parse natural language scheduling requests (e.g., 'next Tuesday at 2pm') "
        "into proper ISO datetime formats. "
        "Use get_available_time_slots to check availability when needed. "
        "If there is no suitable time slot, stop and confirm unavailability in your response. "
        "Use create_calendar_event to schedule events. "
        "Always confirm what was scheduled in your final response."
    )
)

email_agent = create_agent(
    model,
    tools=[send_email],
    system_prompt=(
        "You are an email assistant. "
        "Compose professional emails based on natural language requests. "
        "Extract recipient information and craft appropriate subject lines and body text. "
        "Use send_email to send the message. "
        "Always confirm what was sent in your final response."
    )
)

# ============================================================================
# Step 3: Wrap sub-agents as tools for the supervisor
# ============================================================================

@tool
def schedule_event(request: str) -> str:
    """Schedule calendar events using natural language.

    Use this when the user wants to create, modify, or check calendar appointments.
    Handles date/time parsing, availability checking, and event creation.

    Input: Natural language scheduling request (e.g., 'meeting with design team
    next Tuesday at 2pm')
    """
    result = calendar_agent.invoke({
        "messages": [{"role": "user", "content": request}]
    })
    return result["messages"][-1].text


@tool
def manage_email(request: str) -> str:
    """Send emails using natural language.

    Use this when the user wants to send notifications, reminders, or any email
    communication. Handles recipient extraction, subject generation, and email
    composition.

    Input: Natural language email request (e.g., 'send them a reminder about
    the meeting')
    """
    result = email_agent.invoke({
        "messages": [{"role": "user", "content": request}]
    })
    return result["messages"][-1].text


# ============================================================================
# Step 4: Create the supervisor agent
# ============================================================================

supervisor_agent = create_agent(
    model,
    tools=[schedule_event, manage_email],
    system_prompt=(
        "You are a helpful personal assistant. "
        "You can schedule calendar events and send emails. "
        "Break down user requests into appropriate tool calls and coordinate the results. "
        "When a request involves multiple actions, use multiple tools in sequence."
    )
)

# ============================================================================
# Step 5: Use the supervisor
# ============================================================================

def run_assistant(user_request: str) -> str:
    result = supervisor_agent.invoke({
        "messages": [{"role": "user", "content": user_request}]
    })
    return result["messages"][-1].text


if __name__ == "__main__":
    import json

    sample_requests = [
        "Schedule a meeting with the design team next Tuesday at 2pm for 1 hour in CET timezone and send them an email reminder about reviewing the new mockups.",
        "Block 90 minutes this Friday morning for deep work—no attendees—and email my manager that I’ll be offline during that window unless it’s urgent.",
        "Find a time Thursday afternoon for me, Priya (priya@acme.co), and Luis (luis@acme.co) for a 45-minute architecture review, book it, then email both of them the agenda: API versioning, rollout risks, and open questions.",
        "Send the recruiting team a thank-you email for yesterday’s onsite loop and mention we’ll share consolidated feedback by end of week.",
        "Move our weekly standup to Wednesdays at 9:30am for 30 minutes through the end of the month—same video link—and ping the team in email so nobody misses it.",
        "Reserve next Thursday 4pm–5pm for interview debrief with the panel (same people as last time—use their emails from the last debrief invite) and don’t send any email; I’ll Slack them myself.",
        "Schedule a 1-hour product roadmap session next Monday at 11am with product@ourco.com and eng-leads@ourco.com in Conference Room B.",
        "Email legal@ourco.com with subject \"NDA for Contoso pilot\" and say we’re ready for their redlines; attach nothing for now. No calendar changes.",
        "Book a 20-minute sync with Alex (alex@partner.io) tomorrow late afternoon if there’s a slot, and send them a quick email with our latest pricing one-pager summary in the body—no file attach.",
        "On March 12, hold a 2-hour workshop from 1pm to 3pm for the customer success team only, then email CS-all@ourco.com the pre-read link https://docs.example.com/cs-workshop and ask them to skim it before the session.",
        "Check when everyone on the exec@ourco.com list is free next Wednesday for a 60-minute QBR dry run, pick the best option, put it on the calendar, and email them the deck outline: metrics, risks, asks.",
        "Send ops@ourco.com a heads-up that the vendor maintenance window slipped to Sunday 2am–6am UTC and they should pause automated deploys during that window."
    ]

    results = []
    for req in sample_requests:
        output = run_assistant(req)
        results.append({
            "input": {"message": req},
            "expected_output": {"response": output}
        })

    with open("data/seed.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    for entry in results:
        print("Input:", entry["input"]["message"])
        print("Output:", entry["expected_output"]["response"])
        print("-" * 60)