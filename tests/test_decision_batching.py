import pytest

from modal_shared.decision_batching import microbatches


def rows(lengths):
    return [{"key": str(i), "input_ids": [i] * n} for i, n in enumerate(lengths)]


def test_length_budget_preserves_every_row_and_order_at_ragged_boundaries():
    data = rows([2, 2, 3, 3, 9, 1, 1, 1, 1])
    batches = list(microbatches(data, max_rows=3, max_padded_tokens=12))
    assert [row for batch in batches for row in batch] == data
    assert [len(batch) for batch in batches] == [3, 1, 1, 3, 1]
    for batch in batches:
        assert len(batch) <= 3
        assert len(batch) * max(len(row["input_ids"]) for row in batch) <= 12


def test_long_singleton_is_rejected_instead_of_silently_exceeding_memory_budget():
    with pytest.raises(ValueError, match="padded-token budget"):
        list(microbatches(rows([2, 13]), max_rows=8, max_padded_tokens=12))


@pytest.mark.parametrize("max_rows,max_tokens", [(0, 12), (2, 0), (-1, 12), (2, -1)])
def test_nonpositive_batch_budgets_are_rejected(max_rows, max_tokens):
    with pytest.raises(ValueError, match="positive"):
        list(microbatches(rows([2]), max_rows=max_rows, max_padded_tokens=max_tokens))


def test_empty_token_sequence_is_rejected_before_forward_pass():
    with pytest.raises(ValueError, match="empty"):
        list(microbatches(rows([2, 0]), max_rows=8, max_padded_tokens=12))
