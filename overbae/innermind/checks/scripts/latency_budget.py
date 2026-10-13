"""The llm call took at most MAX_SECONDS."""

MAX_SECONDS = None


def evaluate(span):
    if MAX_SECONDS is None or not span.duration_s:
        return None
    return span.duration_s <= MAX_SECONDS, f"{round(span.duration_s, 3)}s, budget {MAX_SECONDS}s"
