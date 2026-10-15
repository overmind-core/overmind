"""Output that opens as JSON, bare or in a complete fence, parses. Plain text abstains."""


def evaluate(span):
    text = (span.output or "").strip()
    if not text.startswith(("{", "[")):
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if not fence:
            return None
        text = fence.group(1).strip()
        if not text.startswith(("{", "[")):
            return None
    try:
        json.loads(text)
    except ValueError as exc:
        return False, "output is not valid JSON: " + str(exc)
    return True, "output is valid JSON"
