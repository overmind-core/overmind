"""The llm call cost at most MAX_COST, read from genai.cost."""

MAX_COST = None


def evaluate(span):
    cost = span.attributes.get("genai.cost")
    if MAX_COST is None or not isinstance(cost, (int, float)):
        return None
    return cost <= MAX_COST, f"${cost}, budget ${MAX_COST}"
