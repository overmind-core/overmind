"""Share of EXPECTED_CALLS made under this span with matching arguments.

MODE "exact" needs equal arguments, "subset" needs the expected ones present
(extras allowed), "superset" allows only expected ones. A tool's arguments are
its overmind.arg.* attributes, else its input when that is a JSON object.

    EXPECTED_CALLS = ({"name": "search_flights", "arguments": {"destination": "LIS"}},)
"""

TOOL_TYPES = ("tool_call", "tool", "retrieval")

EXPECTED_CALLS = ()
MODE = "subset"
ARG_PREFIX = "overmind.arg."


def arguments_of(node):
    args = {}
    for key, value in node.attributes.items():
        if key.startswith(ARG_PREFIX):
            args[key[len(ARG_PREFIX) :]] = value
    if args:
        return args
    try:
        parsed = json.loads(node.input or "")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def tool_calls(span):
    calls = []
    stack = list(span.children)
    while stack:
        node = stack.pop()
        if node.span_type in TOOL_TYPES:
            name = node.tool or node.name
            calls.append((name, arguments_of(node)))
        stack.extend(node.children)
    return calls


def matches(actual, expected):
    if MODE == "subset":
        return all(actual.get(key) == value for key, value in expected.items())
    if MODE == "superset":
        return all(expected.get(key) == value for key, value in actual.items())
    return actual == expected


def evaluate(span):
    calls = tool_calls(span)
    if not EXPECTED_CALLS or not calls:
        return None
    unmatched = []
    for expected in EXPECTED_CALLS:
        name = expected.get("name")
        arguments = expected.get("arguments") or {}
        if not any(called == name and matches(args, arguments) for called, args in calls):
            unmatched.append(str(name))
    matched = len(EXPECTED_CALLS) - len(unmatched)
    reason = f"{matched}/{len(EXPECTED_CALLS)} expected calls matched"
    if unmatched:
        reason += "; unmatched: " + ", ".join(unmatched)
    return matched / len(EXPECTED_CALLS), reason
