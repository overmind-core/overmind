from overbae.models import Span


def captured_errors(span: Span) -> dict | None:
    if span.status_code != 2:
        return None
    attrs = span.attributes or {}
    records = []

    def text(value):
        return value if isinstance(value, str) and value.strip() else ""

    def first(*keys):
        for key in keys:
            value = attrs.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return str(value)
            if text(value):
                return text(value)
        return ""

    def add(source, error_type="", message="", stacktrace=""):
        record = {
            "source": source,
            "type": text(error_type),
            "message": text(message),
            "stacktrace": text(stacktrace),
        }
        if any(record[key] for key in ("type", "message", "stacktrace")):
            records.append(record)

    # Most specific first: the summary takes the first type and message it finds.
    for prefix in ("error", "overmind.error", "exception"):
        add(
            "Attributes" if prefix == "error" else prefix,
            attrs.get(f"{prefix}.type"),
            attrs.get(f"{prefix}.message"),
            attrs.get(f"{prefix}.stacktrace"),
        )
    for event in span.events or []:
        if not isinstance(event, dict) or event.get("name") != "exception":
            continue
        event_attrs = event.get("attributes")
        if not isinstance(event_attrs, dict):
            continue
        add(
            "Exception event",
            event_attrs.get("exception.type"),
            event_attrs.get("exception.message"),
            event_attrs.get("exception.stacktrace"),
        )
    add("Status", message=span.status_message)

    context = {}
    provider = first("gen_ai.provider.name", "genai.provider", "gen_ai.system")
    if provider:
        context["Provider"] = provider
    http_status = first("http.response.status_code", "http.status_code")
    if http_status:
        context["HTTP status"] = http_status
    for key in (
        "http.request.method",
        "url.full",
        "http.url",
        "server.address",
        "anthropic.request.id",
        "openai.request.id",
        "gen_ai.response.id",
        "http.response.request_id",
        "request.id",
    ):
        if value := first(key):
            context[key] = value

    error_type = next((r["type"] for r in records if r["type"]), "")
    message = next((r["message"] for r in records if r["message"]), "")
    parts = [error_type]
    if "HTTP status" in context:
        parts.append(f"HTTP {context['HTTP status']}")
    if message and message != error_type:
        parts.append(message.splitlines()[0])
    summary = " · ".join(part for part in parts if part) or "No error message captured"
    return {"summary": summary, "records": records, "context": context}
