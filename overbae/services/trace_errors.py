from overbae.models import Span


def captured_errors(span: Span) -> dict | None:
    attrs = span.attributes or {}
    records = []

    def text(value):
        return value if isinstance(value, str) and value.strip() else ""

    def add(source, error_type="", message="", stacktrace=""):
        record = {
            "source": source,
            "type": text(error_type),
            "message": text(message),
            "stacktrace": text(stacktrace),
        }
        if any(record[key] for key in ("type", "message", "stacktrace")):
            records.append(record)

    if span.status_code == 2:
        add("Status", message=span.status_message)
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

    http_status = attrs.get("http.response.status_code", attrs.get("http.status_code"))
    failed_http = str(http_status).isdigit() and 400 <= int(http_status) <= 599
    if span.status_code != 2 and not records and not failed_http:
        return None

    context = {}
    provider = (
        attrs.get("gen_ai.provider.name")
        or attrs.get("genai.provider")
        or attrs.get("gen_ai.system")
    )
    if text(provider):
        context["Provider"] = text(provider)
    if isinstance(http_status, (str, int)) and not isinstance(http_status, bool):
        context["HTTP status"] = str(http_status)
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
        if text(attrs.get(key)):
            context[key] = text(attrs[key])

    error_type = next((r["type"] for r in records if r["type"]), "")
    message = next((r["message"] for r in records if r["message"]), "")
    parts = [error_type]
    if "HTTP status" in context:
        parts.append(f"HTTP {context['HTTP status']}")
    if message and message != error_type:
        parts.append(message.splitlines()[0])
    summary = " · ".join(part for part in parts if part) or "No error message captured"
    return {"summary": summary, "records": records, "context": context}
