from overbae.services.datasets import rows


def sample_io(sample):
    trajectory = sample.trajectory or {}
    metadata = trajectory.get("metadata") or {}
    request = trajectory.get("model_request")
    source = "unavailable"
    input_value = None
    truncated = bool(metadata.get("truncated"))
    if isinstance(request, dict) and isinstance(request.get("messages"), list):
        source = "recorded"
        input_value = {"messages": request["messages"], "tools": request.get("tools", [])}
        truncated = truncated or bool(request.get("truncated"))
    elif sample.run.cell_id and sample.row_index is not None:
        try:
            rows.verify(sample.run.cell)
            row = rows.row(sample.run.cell, sample.row_index)
            if row is not None:
                source, input_value = "dataset", row.input
        except (rows.RowStoreError, OSError, ValueError):
            pass  # A missing pinned frame cannot be replaced by the dataset's latest version.

    messages = trajectory.get("messages") or []
    boundary = metadata.get("output_start")
    output_messages = (
        messages[boundary:] if isinstance(boundary, int) and 0 <= boundary <= len(messages) else []
    )
    return {
        "input": input_value,
        "input_source": source,
        "output": trajectory.get("final_output"),
        "output_messages": output_messages,
        "reference": sample.expected,
        "truncated": truncated,
    }
