import pytest

from overbae.services.datasets.partition import content_key, split_rows


def test_split_clusters_content_and_groups_and_reports_stratification():
    source = [
        {"input": f"q{i}", "expected_output": "a", "customer": i // 2, "label": i % 2}
        for i in range(20)
    ]
    train, evaluation, report = split_rows(
        source + [source[0]],
        eval_percent=30,
        position="random",
        group_by=["customer"],
        stratify_by="label",
    )
    assert len(train) == 14 and len(evaluation) == 6
    assert {r["customer"] for r in train}.isdisjoint({r["customer"] for r in evaluation})
    assert report["duplicates_removed"] == 1 and report["content_overlap"] == 0
    assert all(stratum["eval"] == 3 for stratum in report["strata"].values())
    with pytest.raises(ValueError, match="independent holdout"):
        split_rows(
            [source[0], {**source[0], "expected_output": "b"}], eval_percent=30, position="random"
        )


def test_contamination_matches_flat_and_nested_conversations():
    messages = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "yes"}]
    assert content_key({"messages": messages}) == content_key({"input": {"messages": messages}})
    assert content_key({"messages": messages}) == content_key({"input": "one"})
