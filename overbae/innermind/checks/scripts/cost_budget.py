"""The llm call cost at most MAX_COST, read from the span's usage."""

MAX_COST = None


def evaluate(span):
    cost = span.usage.get("genai.cost", span.usage.get("gen_ai.usage.cost"))
    if MAX_COST is None or not isinstance(cost, (int, float)):
        return None
    return cost <= MAX_COST, f"${cost}, budget ${MAX_COST}"
