"""The llm call took at most MAX_SECONDS, read from genai.elapsed_seconds."""

MAX_SECONDS = None


def evaluate(span):
    seconds = span.attributes.get("genai.elapsed_seconds")
    if MAX_SECONDS is None or not isinstance(seconds, (int, float)):
        return None
    return seconds <= MAX_SECONDS, f"{round(seconds, 3)}s, budget {MAX_SECONDS}s"
