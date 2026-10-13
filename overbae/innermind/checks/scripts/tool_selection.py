"""F1 of the tools called under this span against its reference_trajectory.

Abstains without a reference or without tool calls: no calls is missing
evidence, not a wrong choice.
"""

TOOL_TYPES = ("tool_call", "tool", "retrieval")


def reference_of(span):
    value = span.attributes.get("overmind.reference_trajectory")
    if value is None:
        value = span.attributes.get("reference_trajectory")
    # OTLP array attributes can arrive as a JSON-encoded string.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, (list, tuple)):
        return []
    names = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        else:
            try:
                names.append(str(item.get("name") or ""))
            except AttributeError:
                names.append(str(item))
    return [name for name in names if name]


def tools_called(span):
    names = []
    stack = list(span.children)
    while stack:
        node = stack.pop()
        if node.span_type in TOOL_TYPES:
            names.append(node.tool or node.name)
        stack.extend(node.children)
    return names


def evaluate(span):
    expected = set(reference_of(span))
    called = set(tools_called(span))
    if not expected or not called:
        return None
    hits = called & expected
    if not hits:
        return 0.0, "no reference tool was called"
    precision = len(hits) / len(called)
    recall = len(hits) / len(expected)
    f1 = 2 * precision * recall / (precision + recall)
    reason = f"F1 {round(f1, 2)}"
    missing = sorted(expected - called)
    extra = sorted(called - expected)
    if missing:
        reason += "; not called: " + ", ".join(missing)
    if extra:
        reason += "; not in reference: " + ", ".join(extra)
    return f1, reason
