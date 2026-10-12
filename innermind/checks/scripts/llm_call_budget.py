"""The span and its descendants make at most MAX_LLM_CALLS llm calls."""

MAX_LLM_CALLS = None


def evaluate(span):
    if MAX_LLM_CALLS is None:
        return None
    count = 0
    stack = [span]
    while stack:
        node = stack.pop()
        count += node.kind == "llm"
        stack.extend(node.children)
    if not count:
        return None
    reason = f"{count} llm calls, budget {MAX_LLM_CALLS}"
    return count <= MAX_LLM_CALLS, reason
