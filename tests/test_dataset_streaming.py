import json
import weakref

import pytest

from overbae.models import Dataset, Project
from overbae.services.datasets import files, land, paths, store


def test_stream_writer_checks_late_types_without_retaining_rows(tmp_path):
    class Row(dict):
        pass

    refs = []

    def source():
        for i in range(25_003):
            if i and i % 10_000 == 0:
                assert sum(ref() is not None for ref in refs) <= 10_000
            row = Row(index=i, payload=None if i < 20_000 else {"q": [0.2, 0.8]})
            if i == 25_002:
                row["late"] = "present"
            refs.append(weakref.ref(row))
            yield row

    target = tmp_path / "rows.parquet"
    store.write_rows(target, source())
    assert store.row_count(target) == 25_003
    assert store.read_manifest(target) == [
        {"name": "index", "type": "integer"},
        {"name": "payload", "type": "json"},
        {"name": "late", "type": "string"},
    ]
    last = next(row for row in store.iter_rows(target) if row["index"] == 25_002)
    assert last == {"index": 25_002, "payload": {"q": [0.2, 0.8]}, "late": "present"}


def test_file_rows_decide_numeric_types_across_the_entire_csv(tmp_path):
    path = tmp_path / "late.csv"
    path.write_text("id,count\n" + "12,1\n" * 20_000 + "007,2\n")
    source = files.FileRows(path, filename=path.name)
    assert len(source) == 20_001
    first = next(iter(source))
    assert first == {"id": "12", "count": 1}
    assert list(source)[-1] == {"id": "007", "count": 2}


@pytest.mark.django_db
def test_landing_measures_every_row_without_full_frame_reads(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT = tmp_path / "media"
    path = tmp_path / "native.jsonl"
    with path.open("w") as stream:
        for i in range(20_003):
            q = [0.2, 0.8] if i < 20_002 else [0.2, 0.7]
            stream.write(
                json.dumps(
                    {
                        "decision": {
                            "state": str(i),
                            "question": "Choose",
                            "kind": "choice",
                            "options": ["A", "B"],
                            "target_probabilities": q,
                        }
                    }
                )
                + "\n"
            )

    def forbid(*args, **kwargs):
        raise AssertionError("Whole-frame read during landing")

    monkeypatch.setattr(store, "read_frame", forbid)
    project = Project.objects.create(name="Streaming", slug="streaming")
    dataset = Dataset.objects.create(project=project, name="Native", intent="train")
    landing = land.read_file(path, filename=path.name)
    land.commit(dataset, landing, infer_capability=False)
    source = dataset.source
    assert source.rows == 20_003
    assert not source.intent_report["train"]["ok"]
    assert source.stats["num_examples"] == 20_003
    rows = store.iter_rows(paths.cell_path(dataset.id, source.id))
    first = next(rows)
    assert first["source_row"] == 0
    assert first["_overmind_provenance"]["source_content_keys"]
    last = None
    for last in rows:  # noqa: B007 — inspect the final streamed row
        pass
    assert last["source_row"] == 20_002
    assert last["decision"]["target_probabilities"] == [0.2, 0.7]


@pytest.mark.django_db
def test_batch_cell_impact_and_export_are_file_backed(tmp_path, settings, monkeypatch):
    from overbae.services.datasets import use
    from overbae.services.datasets.notebook import agent
    from overbae.services.datasets.notebook import run as run_svc

    settings.MEDIA_ROOT = tmp_path / "media"
    project = Project.objects.create(name="Batch", slug="batch")
    dataset = Dataset.objects.create(project=project, name="Corpus", intent="train")
    records = [{"text": f"case {i}", "target": [0.25, 0.75]} for i in range(20_003)]
    land.land_rows(dataset, records)
    source = dataset.source
    original = store.head(paths.cell_path(dataset.id, source.id), 1)[0]

    def forbid(*args, **kwargs):
        raise AssertionError("Whole-frame read in the batch lifecycle")

    monkeypatch.setattr(store, "read_frame", forbid)
    tools = agent.Tools(dataset.id, None, lambda event: None)
    script = """
def transform_batch(df):
    df = df.loc[df.source_row % 3 != 1].copy()
    df['decision'] = df.apply(lambda r: {
        'state': r['text'], 'question': 'Choose', 'kind': 'choice',
        'options': ['No', 'Yes'], 'target_probabilities': r['target']
    }, axis=1)
    return df[['source_row', 'decision']]
"""
    preview = tools.try_script({"script": script})
    assert preview["ok"], preview
    assert preview["rows"] == 13_335
    proposed = tools.add_cell({"title": "Native", "script": script, "kind": "mechanical"})
    assert proposed["ok"] and not proposed.get("proposed"), proposed
    assert proposed["review"]["rows_removed"] == 6668
    assert proposed["review"]["identity_preserved"]
    cell = dataset.cells.get(pk=proposed["id"])
    run_svc.execute(dataset, activate_cell_id=cell.id)
    dataset.refresh_from_db()
    assert dataset.state == "idle", dataset.error
    cell.refresh_from_db()
    assert cell.rows == 13_335 and cell.intent_report["train"]["ok"]
    first = store.head(paths.cell_path(dataset.id, cell.id), 1)[0]
    assert all(
        first["_overmind_provenance"][k] == v for k, v in original["_overmind_provenance"].items()
    )
    assert first["_overmind_provenance"]["parents"][0]["row"] == original["source_row"]
    assert first["decision"]["target_probabilities"] == [0.25, 0.75]
    assert use.check(dataset, "train", cell=cell).id == cell.id


def test_late_batch_failure_never_returns_partial_output(tmp_path):
    from overbae.services.datasets.notebook import runner

    path = tmp_path / "input.parquet"
    store.write_rows(path, ({"source_row": i, "x": i} for i in range(20_003)))
    result = runner.run(
        """
def transform_batch(df):
    if df.x.max() > 20_000:
        raise ValueError('late batch failed')
    return df
""",
        path,
        library_cache=tmp_path / "libraries",
    )
    assert not result.ok and "late batch failed" in result.error
    assert result.frame is None


@pytest.mark.django_db
def test_batch_quality_audit_covers_late_failures_without_loading_source(
    tmp_path, settings, monkeypatch
):
    from overbae.services.datasets import review

    settings.MEDIA_ROOT = tmp_path / "media"
    project = Project.objects.create(name="Audit", slug="audit-stream")
    dataset = Dataset.objects.create(project=project, name="Audit", intent="eval")
    land.commit(
        dataset,
        land.Landing(
            {"input": f"question {i}", "expected_output": "answer"} for i in range(20_003)
        ),
    )

    def forbid(*args, **kwargs):
        raise AssertionError("Whole-frame read during audit")

    monkeypatch.setattr(store, "read_frame", forbid)
    result = review.record_quality(
        dataset,
        dataset.source,
        [{"name": "coverage", "evidence": "Test the final identity"}],
        script="""
def transform_batch(df):
    return df[['source_row']].assign(coverage=df.source_row != 20_002)
""",
    )
    check = result["checks"][0]
    assert check["rows_checked"] == 20_003
    assert check["rows_failed"] == 1 and check["failed_source_rows"] == [20_002]


def test_row_lookup_reads_only_requested_parquet_groups(tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    path = tmp_path / "lookup.parquet"
    store.write_rows(path, ({"source_row": i, "nested": {"value": i}} for i in range(25_003)))
    monkeypatch.setattr(pq, "read_table", lambda *a, **k: pytest.fail("Whole-table lookup"))
    assert [row["nested"] for row in store.read_rows(path, [25_002, 0, 25_002, -1, 99_999])] == [
        {"value": 25_002},
        {"value": 0},
        {"value": 25_002},
    ]


def test_sandboxed_queries_stream_only_the_registered_file(tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    source = tmp_path / "allowed.parquet"
    outside = tmp_path / "outside.parquet"
    store.write_rows(source, ({"n": i} for i in range(25_003)))
    store.write_rows(outside, [{"secret": "never return"}])
    monkeypatch.setattr(
        pq, "read_table", lambda *a, **k: pytest.fail("Whole-table SQL registration")
    )
    assert store.query("SELECT count(*) n, max(n) final_n FROM t", t=source)["rows"] == [
        {"n": 25_003, "final_n": 25_002}
    ]
    with pytest.raises(Exception, match="access|Permission|not allowed"):
        store.query(f"SELECT * FROM read_parquet('{outside}')", t=source)
    with pytest.raises(Exception, match="access|Permission|not allowed|disabled|Extension"):
        store.query("SELECT * FROM read_csv('https://example.com/data.csv')", t=source)
    with pytest.raises(Exception, match="Parser|SELECT|allowed"):
        store.query(
            "SELECT * FROM t); SET enable_external_access=true; SELECT * FROM t --", t=source
        )


@pytest.mark.django_db
def test_training_validation_checks_bounded_batches_and_retains_late_errors(monkeypatch):
    from conftest import frozen_dataset

    from overbae.models import Project
    from overbae.services import finetuning_validator as validator

    project = Project.objects.create(name="Bounded validation", slug="bounded-validation")
    source = [
        {
            "messages": [
                {"role": "user", "content": str(i)},
                {"role": "assistant", "content": "a" * 1000},
            ]
        }
        for i in range(2001)
    ]
    dataset = frozen_dataset(project, source, contract="train")
    original_rows, original_check = validator.row_store.iter_rows, validator.validate_rows
    pending = 0
    checked = 0

    def read(cell):
        nonlocal pending
        for row in original_rows(cell):
            pending += 1
            assert pending <= 512, "Validation retained more than its bounded row batch"
            if row.index == 2000:
                row.extra["messages"][-1]["content"] = ""
            yield row

    def check(rows):
        nonlocal pending, checked
        checked += len(rows)
        pending = 0
        return original_check(rows)

    monkeypatch.setattr(validator.row_store, "iter_rows", read)
    monkeypatch.setattr(validator, "validate_rows", check)
    result = validator.validate_dataset(str(dataset.id), validation_enabled=False)
    assert checked == 2001
    assert not result.valid and any("2001" in error for error in result.errors)


@pytest.mark.django_db
def test_attached_files_and_replay_never_materialize_existing_frame(
    tmp_path, settings, monkeypatch
):
    from overbae.models import Dataset, Project
    from overbae.services.datasets import attachments, land, paths, store
    from overbae.services.datasets.notebook import run

    settings.MEDIA_ROOT = tmp_path
    project = Project.objects.create(name="Attachment streaming", slug="attachment-streaming")
    dataset = Dataset.objects.create(project=project, name="Source", intent="explore")
    land.land_rows(dataset, [{"text": f"evidence {i}", "group_id": str(i)} for i in range(3000)])
    dataset.refresh_from_db()
    source = dataset.source
    monkeypatch.setattr(
        store,
        "read_frame",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("whole frame materialized")),
    )
    added = attachments.commit(
        dataset, land.Landing({"text": f"attached {i}", "group_id": "attachment"} for i in range(5))
    )
    assert added.rows == 3005
    output = paths.cell_path(dataset.id, added.id)
    assert store.page(output, offset=3000)["rows"][0]["text"] == "attached 0"
    assert store.file_sha256(paths.cell_path(dataset.id, source.id)) == source.fingerprint
    added.state = "queued"
    added.save(update_fields=["state"])
    run.execute(dataset)
    added.refresh_from_db()
    assert added.state == "ok" and added.rows == 3005
