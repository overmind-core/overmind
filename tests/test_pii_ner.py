"""Offline, these tests never load a model: they pre-seed the content-signature
span cache (``ner._SPANS``), stub ``available()``, or stub the hosted-backend
seam (``_warm_modal``)."""

from __future__ import annotations

import pytest

from overbae.services.pii import ner


@pytest.fixture
def span_cache():
    """Snapshot/restore the process-global span cache."""
    saved = dict(ner._SPANS)
    ner._SPANS.clear()
    try:
        yield ner._SPANS
    finally:
        ner._SPANS.clear()
        ner._SPANS.update(saved)


@pytest.fixture
def offline(span_cache, monkeypatch):
    monkeypatch.setattr(ner, "_modal_ner_available", lambda: False)
    yield


@pytest.fixture
def cached_backend(span_cache, monkeypatch):
    """``available()`` True while Modal stays unconfigured: spans resolve from the
    pre-seeded cache only, and a miss scans nothing."""
    monkeypatch.setattr(ner, "_modal_ner_available", lambda: False)
    monkeypatch.setattr(ner, "available", lambda: True)
    yield


@pytest.fixture
def fake_modal(span_cache, monkeypatch):
    """Stub the hosted-backend seam (``_warm_modal``), not a model: each fan-out
    records the signatures it actually scanned, mirroring the real dedupe."""
    calls: list[list[str]] = []
    mapping: dict[str, list[dict]] = {}

    def _warm(items, progress_cb=None):  # noqa: ARG001 — match the real signature
        pending = [(sig, text) for sig, text in items if sig not in ner._SPANS]
        calls.append([sig for sig, _ in pending])
        for sig, text in pending:
            ner._SPANS[sig] = list(mapping.get(text, []))
        return len(pending)

    monkeypatch.setattr(ner, "_modal_ner_available", lambda: True)
    monkeypatch.setattr(ner, "_warm_modal", _warm)
    return {"calls": calls, "mapping": mapping}


@pytest.fixture
def live_modal_state():
    """Snapshot/restore the span cache only, leaving Modal enabled so ``warm``
    dispatches to the real deployed endpoint."""
    saved_spans = dict(ner._SPANS)
    ner._SPANS.clear()
    try:
        yield
    finally:
        ner._SPANS.clear()
        ner._SPANS.update(saved_spans)


def _seed(text: str, spans: list[dict]) -> None:
    ner._SPANS[ner._signature(text)] = spans


def _scanned(fake_modal) -> int:
    return sum(len(c) for c in fake_modal["calls"])


def test_available_is_modal_only(monkeypatch):
    monkeypatch.setattr(ner, "_modal_ner_available", lambda: False)
    assert ner.available() is False
    monkeypatch.setattr(ner, "_modal_ner_available", lambda: True)
    assert ner.available() is True


def test_active_backend_modal_or_none(monkeypatch):
    monkeypatch.setattr(ner, "_modal_ner_available", lambda: False)
    assert ner.active_backend() == "none"
    monkeypatch.setattr(ner, "_modal_ner_available", lambda: True)
    assert ner.active_backend() == "modal"


def test_redact_text_right_to_left(cached_backend):
    text = "Alice met Bob in Paris"
    _seed(
        text,
        [
            {"start": 0, "end": 5, "label": "PERSON_NAME"},
            {"start": 10, "end": 13, "label": "PERSON_NAME"},
            {"start": 17, "end": 22, "label": "LOCATION"},
        ],
    )
    redacted, spans = ner.redact_text(text)
    # Replacing right to left keeps every earlier offset valid.
    assert redacted == "[REDACTED] met [REDACTED] in [REDACTED]"
    assert [s["label"] for s in spans] == ["PERSON_NAME", "PERSON_NAME", "LOCATION"]


def test_redact_text_no_pii_is_noop(cached_backend):
    text = "just an ordinary sentence with nothing sensitive"
    _seed(text, [])
    redacted, spans = ner.redact_text(text)
    assert redacted == text
    assert spans == []


def test_redact_text_noop_when_no_backend(offline):
    # No backend and no validator layer, so even email/card/SSN pass through.
    text = "mail a@b.com card 4111 1111 1111 1111 ssn 536-89-1234"
    redacted, spans = ner.redact_text(text)
    assert redacted == text
    assert spans == []


def test_redact_value_recurses_dict_list_str(cached_backend):
    _seed(
        "Alice in Paris",
        [
            {"start": 0, "end": 5, "label": "PERSON_NAME"},
            {"start": 9, "end": 14, "label": "LOCATION"},
        ],
    )
    _seed("Bob waved", [{"start": 0, "end": 3, "label": "PERSON_NAME"}])

    value = {
        "user": "Alice in Paris",
        "notes": ["clean note", "Bob waved"],
        "count": 7,
    }
    redacted, changed = ner.redact_value(value)
    assert changed is True
    assert redacted["user"] == "[REDACTED] in [REDACTED]"
    assert redacted["notes"][0] == "clean note"
    assert redacted["notes"][1] == "[REDACTED] waved"
    assert redacted["count"] == 7


def test_redact_value_non_string_scalar(offline):
    assert ner.redact_value(42) == (42, False)
    assert ner.redact_value(None) == (None, False)


def test_strip_token_spans_drops_mask_overlaps():
    """Spans covering the mask token must be dropped, or a redacted row re-flags
    forever and the Mask PII fix loops."""
    token = ner._REDACTION_TOKEN
    text = f"see {token} and Berlin"
    berlin = {"start": text.index("Berlin"), "end": text.index("Berlin") + 6, "label": "LOCATION"}
    spans = [
        # GLiNER emits both a bare-token span and one glued to a neighbour
        # ("see [REDACTED]"); both must drop on overlap.
        {"start": text.index(token), "end": text.index(token) + len(token), "label": "LOCATION"},
        {"start": 0, "end": text.index(token) + len(token), "label": "ORG"},
        berlin,
    ]
    assert ner._strip_token_spans(text, spans) == [berlin]
    assert ner._strip_token_spans("plain Berlin text", [berlin]) == [berlin]


def test_redact_value_noop_on_already_redacted(cached_backend):
    """Re-redaction reports changed=False, so the fix-apply preview cannot keep
    ``would_apply`` alive on token-only rows."""
    token = ner._REDACTION_TOKEN
    text = f"the {token} clinic"
    _seed(
        text,
        [{"start": text.index(token), "end": text.index(token) + len(token), "label": "LOCATION"}],
    )
    new_text, changed = ner.redact_value(text)
    assert new_text == text and changed is False


def test_redact_cached_no_midword_mangling():
    """GLiNER mistags lone letters as names, so cached redaction must match on
    word boundaries only."""
    text = "Alice had urine and sneezing"
    terms = {"Alice": "PERSON_NAME", "e": "PERSON_NAME"}
    new_text, _spans = ner._redact_text_cached(text, terms)
    assert "urine" in new_text and "sneezing" in new_text
    assert "Alice" not in new_text and ner._REDACTION_TOKEN in new_text


def test_normalize_spans_filters_labels_and_ranges():
    raw = [
        {"start": 0, "end": 4, "label": "ORG"},
        {"start": 6, "end": 13, "label": "DATE"},  # not a PII label
        {"start": 5, "end": 2, "label": "PERSON_NAME"},
        {"start": "x", "end": 9, "label": "LOCATION"},
        "junk",
    ]
    assert ner._normalize_spans(raw) == [{"start": 0, "end": 4, "label": "ORG"}]
    assert ner._normalize_spans("not a list") == []


def test_min_score_resolution(monkeypatch):
    monkeypatch.setenv("PII_NER_MIN_SCORE", "0.9")
    assert ner._min_score() == 0.9
    monkeypatch.setenv("PII_NER_MIN_SCORE", "garbage")
    assert ner._min_score() == ner._PII_NER_MIN_SCORE_DEFAULT
    monkeypatch.delenv("PII_NER_MIN_SCORE", raising=False)
    assert ner._min_score() == 0.8  # falls through to the Django setting


def test_modal_infer_sends_threshold(monkeypatch):
    """The request body carries the cutoff so GLiNER drops low-confidence spans
    at decode, server-side."""
    monkeypatch.setenv("PII_NER_MIN_SCORE", "0.77")

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._p

    captured: dict = {}

    class _Client:
        def post(self, endpoint, json=None, headers=None, timeout=None):
            captured["body"] = json
            return _Resp({"results": [[] for _ in json["texts"]]})

    out = ner._modal_infer(_Client(), "http://x/infer", {}, ["a", "b"])
    assert captured["body"]["texts"] == ["a", "b"]
    assert captured["body"]["threshold"] == 0.77
    assert out == [[], []]


def test_redact_value_cached_label_subset(span_cache):
    row, row_text, sig = {"a": "Alice", "b": "Paris"}, "Alice Paris", "rowL"
    ner._SPANS[sig] = [
        {"start": 0, "end": 5, "label": "PERSON_NAME"},
        {"start": 6, "end": 11, "label": "LOCATION"},
    ]
    tok = ner._REDACTION_TOKEN

    all_out, all_changed = ner.redact_value_cached(dict(row), row_text, sig)
    assert all_changed and all_out == {"a": tok, "b": tok}

    only_person, changed = ner.redact_value_cached(dict(row), row_text, sig, labels={"PERSON_NAME"})
    assert changed and only_person == {"a": tok, "b": "Paris"}

    none_out, none_changed = ner.redact_value_cached(dict(row), row_text, sig, labels=set())
    assert none_changed is False and none_out == row


def test_redact_value_cached_column_subset(span_cache):
    """``columns=`` names top-level keys only."""
    row, row_text, sig = {"a": "Alice", "b": "Bob"}, "Alice Bob", "rowC"
    ner._SPANS[sig] = [
        {"start": 0, "end": 5, "label": "PERSON_NAME"},
        {"start": 6, "end": 9, "label": "PERSON_NAME"},
    ]
    out, changed = ner.redact_value_cached(dict(row), row_text, sig, columns={"a"})
    assert changed and out == {"a": ner._REDACTION_TOKEN, "b": "Bob"}

    none_out, none_changed = ner.redact_value_cached(dict(row), row_text, sig, columns=set())
    assert none_changed is False and none_out == row


def test_warm_noop_when_modal_unconfigured(offline):
    stats = ner.warm([("sig", "Alice met Bob in Paris")])
    assert stats == {"backend": "none", "scanned": 0, "elapsed_s": 0.0}
    assert ner._SPANS == {}
    assert ner.spans_for("sig") == []


def test_warm_caches_and_skips_reprocessing(fake_modal):
    fake_modal["mapping"]["Alice met Bob in Paris"] = [
        {"start": 0, "end": 5, "label": "PERSON_NAME"},
        {"start": 17, "end": 22, "label": "LOCATION"},
    ]
    stats = ner.warm([("sig1", "Alice met Bob in Paris")])
    assert stats["backend"] == "modal"
    assert stats["scanned"] == 1
    assert ner._SPANS["sig1"] == fake_modal["mapping"]["Alice met Bob in Paris"]

    again = ner.warm([("sig1", "Alice met Bob in Paris")])
    assert again["scanned"] == 0
    assert _scanned(fake_modal) == 1


def test_spans_and_labels_lookup_hit_cache(fake_modal):
    fake_modal["mapping"]["ACME shipped it"] = [{"start": 0, "end": 4, "label": "ORG"}]

    assert ner.spans_for("sigX", "ACME shipped it") == [{"start": 0, "end": 4, "label": "ORG"}]
    assert ner.labels_for("sigX") == ["ORG"]
    assert _scanned(fake_modal) == 1

    assert ner.spans_for("never-seen") == []
    assert _scanned(fake_modal) == 1


def test_lookup_miss_never_warms_without_a_backend(offline):
    assert ner.spans_for("sig", "Alice met Bob in Paris") == []
    assert ner._SPANS == {}


def test_cache_only_suppresses_fanout_but_rides_cache(fake_modal):
    fake_modal["mapping"]["Alice in Paris"] = [{"start": 0, "end": 5, "label": "PERSON_NAME"}]

    ner.warm([("base", "Alice in Paris")])
    assert _scanned(fake_modal) == 1

    with ner.cache_only():
        assert ner.spans_for("base", "Alice in Paris") == [
            {"start": 0, "end": 5, "label": "PERSON_NAME"}
        ]
        assert ner.spans_for("mutated", "Alice in Paris redacted") == []
        assert ner.warm([("mutated2", "another never seen row")])["backend"] == "cache-only"

    assert _scanned(fake_modal) == 1
    assert "mutated" not in ner._SPANS
    assert "mutated2" not in ner._SPANS
    assert ner.fanout_suppressed() is False


def test_disk_cache_persists_and_reloads_across_processes(fake_modal, tmp_path):
    fake_modal["mapping"]["Bob in Berlin"] = [{"start": 0, "end": 3, "label": "PERSON_NAME"}]

    try:
        ner.configure_disk_cache(str(tmp_path))
        ner.warm([("rowsig", "Bob in Berlin")])
        assert ner._SPANS["rowsig"] == [{"start": 0, "end": 3, "label": "PERSON_NAME"}]

        # Simulate a separate process: drop the in-memory cache and the reload
        # flag, keeping the configured dir.
        ner._SPANS.clear()
        ner._disk_loaded = False
        with ner.cache_only():
            assert ner.spans_for("rowsig") == [{"start": 0, "end": 3, "label": "PERSON_NAME"}]
        assert _scanned(fake_modal) == 1
    finally:
        ner.configure_disk_cache(None)
        ner._disk_loaded = False


def test_delta_scan_sets_and_restores_state():
    # ``_delta_full`` is a ContextVar (thread/context-local) so a worker
    # thread's delta_scan can never suppress a concurrent baseline fan-out.
    assert ner.fanout_suppressed() is False
    assert ner._delta_full.get() is False
    with ner.delta_scan():
        assert ner.fanout_suppressed() is True
        assert ner._delta_full.get() is True
    assert ner.fanout_suppressed() is False
    assert ner._delta_full.get() is False


def test_cache_only_misses_introduced_pii_but_delta_scan_catches_it(fake_modal):
    fake_modal["mapping"]["clean baseline row"] = []
    fake_modal["mapping"]["Alice introduced here"] = [
        {"start": 0, "end": 5, "label": "PERSON_NAME"}
    ]

    ner.warm([("base", "clean baseline row")])
    calls = _scanned(fake_modal)

    with ner.cache_only():
        ner.warm([("mut", "Alice introduced here")])
    assert "mut" not in ner._SPANS
    assert _scanned(fake_modal) == calls

    with ner.delta_scan():
        ner.warm([("mut", "Alice introduced here")])
    assert ner._SPANS.get("mut") == [{"start": 0, "end": 5, "label": "PERSON_NAME"}]
    assert ner.labels_for("mut") == ["PERSON_NAME"]


def test_delta_scan_rides_cache_for_unchanged_rows(fake_modal):
    fake_modal["mapping"]["cached row"] = [{"start": 0, "end": 6, "label": "PERSON_NAME"}]
    ner.warm([("base", "cached row")])
    calls = _scanned(fake_modal)
    with ner.delta_scan():
        assert ner.spans_for("base", "cached row") == [
            {"start": 0, "end": 6, "label": "PERSON_NAME"}
        ]
    assert _scanned(fake_modal) == calls


def test_delta_scan_covers_every_uncached_row(fake_modal):
    fake_modal["mapping"]["row A"] = [{"start": 0, "end": 1, "label": "PERSON_NAME"}]
    fake_modal["mapping"]["row B"] = [{"start": 0, "end": 1, "label": "PERSON_NAME"}]
    with ner.delta_scan():
        ner.warm([("a", "row A"), ("b", "row B")])
    assert "a" in ner._SPANS
    assert "b" in ner._SPANS


@pytest.mark.skipif(not ner._modal_ner_available(), reason="Modal PII NER endpoint not configured")
def test_modal_delta_scan_live_catches_introduced_pii(live_modal_state):
    ner.warm([("clean-base", "the patient reported mild symptoms today")])
    sig, text = "introduced-pii", "the patient Barack Obama reported mild symptoms today"

    with ner.cache_only():
        ner.warm([(sig, text)])
    assert ner._SPANS.get(sig) in (None, [])
    ner._SPANS.pop(sig, None)

    with ner.delta_scan():
        ner.warm([(sig, text)])
    assert "PERSON_NAME" in set(ner.labels_for(sig))


# The tests below hit the real deployed endpoint over the network, so they run
# only when PII_NER_ENDPOINT_URL + PII_NER_TOKEN are configured — otherwise they
# are honestly skipped, never mocked.


@pytest.mark.skipif(not ner._modal_ner_available(), reason="Modal PII NER endpoint not configured")
def test_modal_ner_live_detects_entities(live_modal_state):
    sig = "live-modal-1"
    text = "Barack Obama visited Berlin while working with Microsoft."
    ner.warm([(sig, text)])
    labels = set(ner.labels_for(sig))
    assert "PERSON_NAME" in labels
    assert {"LOCATION", "ORG"} & labels


@pytest.mark.skipif(not ner._modal_ner_available(), reason="Modal PII NER endpoint not configured")
def test_modal_ner_live_redaction_redacts_entities(live_modal_state):
    # GLiNER emits no structured tokens (email/card), so the assertion stays loose.
    text = "Jane Doe visited Paris while working with Microsoft"
    redacted, spans = ner.redact_text(text)
    labels = {s["label"] for s in spans}
    assert "PERSON_NAME" in labels or "LOCATION" in labels
    assert redacted != text
