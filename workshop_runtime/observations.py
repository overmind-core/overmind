import json


def compact(value, *, text_limit=3000, items=12):
    """Bound observations without cutting JSON or rewriting retained evidence."""
    if isinstance(value, str) and len(value) > text_limit:
        head = text_limit * 2 // 3
        tail = text_limit - head
        return {
            "head": value[:head],
            "tail": value[-tail:],
            "omitted_chars": len(value) - text_limit,
        }
    if isinstance(value, list):
        if len(value) > items:
            return {
                "first": [compact(v, text_limit=text_limit, items=items) for v in value[:items]],
                "omitted_items": len(value) - items,
            }
        return [compact(v, text_limit=text_limit, items=items) for v in value]
    if isinstance(value, dict):
        return {k: compact(v, text_limit=text_limit, items=items) for k, v in value.items()}
    return value


def observation(result, receipt, *, tool):
    value = dict(result)
    if tool == "python" and isinstance(value.get("output"), str):
        fragments = []
        for line in value["output"].splitlines():
            try:
                item = json.loads(line)
                if not isinstance(item, dict) or not isinstance(item.get("content"), dict):
                    fragments.append(line)
                    continue
                content = item["content"]
                fragment = (
                    content.get("text")
                    or content.get("data", {}).get("text/plain")
                    or "\n".join(content.get("traceback", []))
                )
                fragments.append(fragment if isinstance(fragment, str) else line)
            except (ValueError, AttributeError, TypeError):
                fragments.append(line)
        value = {
            key: value[key] for key in ("state", "exit_code", "error", "timed_out") if key in value
        } | {"output": "\n".join(fragments)}
    # Explicit reads are the escape hatch: preserve the requested page verbatim.
    reduced = value if tool == "read" else compact(value)
    rendered = json.dumps(reduced, ensure_ascii=False)
    if tool != "read" and len(rendered) > 14000:
        reduced = {
            "fields": list(value),
            "preview": compact(rendered, text_limit=6000),
            **{
                key: compact(value[key])
                for key in (
                    "id",
                    "state",
                    "error",
                    "exit_code",
                    "timed_out",
                    "verified_against_replay",
                )
                if key in value
            },
        }
    return {
        **reduced,
        "full_receipt": receipt,
        "compacted": reduced != value,
    }
