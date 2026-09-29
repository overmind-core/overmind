import json

from openai import OpenAI

import overmind

MODEL = "openai/gpt-4.1-mini"

TRIAGE_PROMPT = (
    "You triage customer support tickets. Reply with exactly one word: refund, shipping or other."
)

ANSWER_PROMPT = (
    "You are a support agent. Answer the customer in two sentences. "
    "Use lookup_order when the ticket names an order."
)

LOOKUP_ORDER = {
    "type": "function",
    "function": {
        "name": "lookup_order",
        "description": "Fetch an order by its id.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
}

ORDERS = {"42": {"status": "delivered", "total": "19.00 USD"}}

CAPABILITY_IDS: dict[str, str] = {}


def capability_id(slug: str):
    return lambda *args, **kwargs: CAPABILITY_IDS.get(slug)


@overmind.tool("lookup_order")
def lookup_order(order_id: str) -> dict:
    return ORDERS.get(order_id, {"status": "unknown"})


@overmind.run(capability="triage", capability_id=capability_id("triage"))
def triage(client: OpenAI, ticket: str) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": TRIAGE_PROMPT},
            {"role": "user", "content": ticket},
        ],
    )
    return (response.choices[0].message.content or "other").strip().lower()


@overmind.run(capability="answer", capability_id=capability_id("answer"))
def answer(client: OpenAI, ticket: str, category: str) -> str:
    messages = [
        {"role": "system", "content": ANSWER_PROMPT},
        {"role": "user", "content": f"[{category}] {ticket}"},
    ]
    response = client.chat.completions.create(model=MODEL, messages=messages, tools=[LOOKUP_ORDER])
    message = response.choices[0].message
    if not message.tool_calls:
        return message.content or ""
    messages.append(message.model_dump(exclude_none=True))
    for call in message.tool_calls:
        result = lookup_order(**json.loads(call.function.arguments))
        messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
    response = client.chat.completions.create(model=MODEL, messages=messages, tools=[LOOKUP_ORDER])
    return response.choices[0].message.content or ""


@overmind.entry_point("handle_ticket")
def handle(client: OpenAI, ticket: str) -> str:
    return answer(client, ticket, triage(client, ticket))
