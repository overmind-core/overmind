def microbatches(rows, *, max_rows: int, max_padded_tokens: int):
    if max_rows < 1 or max_padded_tokens < 1:
        raise ValueError("Decision batch budgets must be positive")
    batch = []
    width = 0
    for row in rows:
        length = len(row["input_ids"])
        if not length:
            raise ValueError("Decision inputs cannot be empty")
        if length > max_padded_tokens:
            raise ValueError("A decision input exceeds the padded-token budget")
        next_width = max(width, length)
        if batch and (len(batch) == max_rows or next_width * (len(batch) + 1) > max_padded_tokens):
            yield batch
            batch, width = [], 0
        batch.append(row)
        width = max(width, length)
    if batch:
        yield batch
