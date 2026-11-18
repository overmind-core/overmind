import pytest

from overbae.models import Dataset, Project
from overbae.services.datasets import land, lineage, paths, store
from overbae.services.datasets.partition import contamination_keys, content_key

pytestmark = pytest.mark.covers("data-logic")


@pytest.mark.parametrize(
    "column",
    [
        "state",
        "question",
        "prompt",
        "instruction",
        "context",
        "evidence",
        "passage",
        "text",
        "options",
        "choices",
        "score",
        "label",
        "output",
    ],
)
def test_tabular_fingerprints_do_not_infer_feature_or_target_roles_from_column_names(column):
    first = {column: "shared", "measurement": 10}
    assert content_key(first) != content_key({**first, "measurement": 20})
    assert content_key(first) != content_key({**first, column: "different"})
    assert content_key(first) == content_key({**first, "source_row": 42})


def test_source_record_and_explicit_input_fingerprints_have_distinct_meanings():
    record = {"text": "shared", "measurement": 10}
    assert content_key(record) != content_key({"input": record})


def test_corrected_fingerprints_do_not_discard_retained_lineage_or_declared_groups():
    original = {
        "state": "active",
        "measurement": 10,
        "group_id": "customer-1",
        "_overmind_provenance": {
            "source_content_keys": ["historical-source-fingerprint"],
            "seed_content_keys": ["synthetic-seed-fingerprint"],
        },
    }
    transformed = {
        "input": "different representation",
        "_overmind_provenance": {
            "source_content_keys": sorted(
                v for k, v in contamination_keys(original) if k == "content"
            ),
            "source_group_keys": sorted(
                [k, v] for k, v in contamination_keys(original) if k != "content"
            ),
        },
    }
    assert contamination_keys(original) <= contamination_keys(transformed)


@pytest.mark.parametrize("shape", ["flat", "nested", "wrapped"])
def test_declared_decision_inputs_group_target_variants_across_wire_formats(shape):
    request = {
        "state": "same evidence",
        "question": "Choose",
        "kind": "choice",
        "options": ["A", "B"],
    }
    canonical = content_key({"decision": request})
    for target in ([1, 0], [0, 1]):
        if shape == "flat":
            row = {**request, "target": target, "source": str(target)}
        elif shape == "nested":
            row = {"decision": {**request, "target_probabilities": target, "weight": 2}}
        else:
            row = {"input": {"decision": request}, "expected_output": {"probabilities": target}}
        assert content_key(row) == canonical


def test_call_origin_and_trace_identity_detect_contamination():
    a = {"origin_trace_id": "shared", "input": "first turn"}
    b = {"trace_id": "shared", "input": "later turn"}
    assert contamination_keys(a) & contamination_keys(b)


GOLDEN_ROWS = [
    {
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Reset  my password"},
            {"role": "assistant", "content": "Done."},
        ],
        "trace_id": "t-1",
        "conversation_id": "c-9",
    },
    {
        "decision": {
            "state": '{"x": 1}',
            "question": "Approve?",
            "kind": "choice",
            "options": ["yes", "no"],
            "target_probabilities": [1.0, 0.0],
        },
        "group_id": "g-2",
    },
    {
        "input": {"question": "What is 2 + 2?", "case_id": "k-7"},
        "expected_output": "4",
        "metadata": {"packet_id": 12},
    },
    {
        "ticket": "T-1",
        "total": 12.5,
        "items": [{"sku": "S1", "qty": 2}],
        "note": None,
        "source_row": 3,
    },
    {
        "text": "Ünïcode  text",
        "customer": "acme",
        "_overmind_provenance": {
            "source_content_keys": ["abc"],
            "source_group_keys": [["trace_id", '"t-0"']],
        },
    },
]
# Computed by the platform before lineage moved to key files. Leakage checks compare keys
# across cells of every age, so these strings may never change.
GOLDEN_KEYS = [
    [
        ["content", "8e4e8524078f2f85c59d161eeeec92c44602f1222e438d3317f58fb00c431653"],
        ["conversation_id", '"c-9"'],
        ["trace_id", '"t-1"'],
    ],
    [
        ["content", "04632979516b3f1ea7eecfd7e3e88358f9a2315cfe02b836820ab6ce5e5d76fe"],
        ["group_id", '"g-2"'],
    ],
    [
        ["case_id", '"k-7"'],
        ["content", "1de78e966a1f4a9d625eb75f1c2b994e24262dee91d72a9afa3f9d62c712c837"],
        ["packet_id", "12"],
    ],
    [["content", "record:v1:e0bb62b9f80a5b617e7645c4dfc9404fc0eb8ceaad14bcce4e4b66cf0def9ef3"]],
    [
        ["content", "abc"],
        ["content", "record:v1:c258a22b88361fbc0924e0218f483df2ba4fd0297b80e70a62b51874a5bf1b06"],
        ["customer", '"acme"'],
        ["trace_id", '"t-0"'],
    ],
]


def test_lineage_keys_keep_their_exact_strings():
    assert [
        sorted(map(list, contamination_keys(row, ("customer",)))) for row in GOLDEN_ROWS
    ] == GOLDEN_KEYS


@pytest.mark.django_db
def test_a_landed_cell_keys_every_row_as_it_is_stored():
    project = Project.objects.create(name="Keys", slug="keys")
    dataset = Dataset.objects.create(project=project, name="Keys")
    land.land_rows(
        dataset, [{k: v for k, v in row.items() if k != "source_row"} for row in GOLDEN_ROWS[:4]]
    )
    frame = paths.cell_path(dataset.pk, dataset.source.pk)
    assert [keys for _, keys in lineage.read(dataset.source)] == [
        lineage.own_keys(row) for row in store.iter_rows(frame)
    ]


@pytest.mark.django_db
def test_a_frame_written_before_key_files_takes_its_keys_from_stored_provenance():
    project = Project.objects.create(name="Old keys", slug="old-keys")
    dataset = Dataset.objects.create(project=project, name="Old keys")
    land.land_rows(dataset, [{"text": "x"}])
    frame = paths.cell_path(dataset.pk, dataset.source.pk)
    store.write_rows(frame, [{**GOLDEN_ROWS[4], "source_row": 0}])
    lineage.keys_path(frame).unlink()
    assert lineage.read(dataset.source) == [
        (0, frozenset(map(tuple, GOLDEN_KEYS[4])) - {("customer", '"acme"')})
    ]
