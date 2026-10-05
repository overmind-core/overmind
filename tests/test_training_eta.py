from overbae.services.finetuning_runner import PollSnapshot, progress_from_snapshot


def snapshot(events, step):
    return PollSnapshot(
        state="running",
        step=step,
        total_steps=100,
        eta_s=99999,
        raw={"run_id": "one", "metrics": events},
    )


def events(start, count):
    return [
        {"event": "BT_PROGRESS", "step": step, "elapsed_s": start + step * 6}
        for step in range(1, count + 1)
    ]


def test_native_eta_waits_for_an_observed_window_and_excludes_model_loading():
    early = progress_from_snapshot(snapshot(events(90, 3), 3))
    assert early["eta_seconds"] is None
    assert early["eta_s"] is None
    steady = progress_from_snapshot(snapshot(events(90, 10), 10))
    assert steady["eta_seconds"] is None
    assert steady["eta_s"] is None
    assert steady["estimated_finish"] is None
    assert steady["eta_range_seconds"][0] < 540 < steady["eta_range_seconds"][1]
    assert steady["eta_window"]["intervals"] == 9


def test_native_eta_discards_previous_attempt_window_and_invalid_values():
    restarted = progress_from_snapshot(snapshot(events(90, 10) + events(0, 2), 2))
    assert restarted["eta_seconds"] is None
    invalid = progress_from_snapshot(
        snapshot(
            events(90, 9) + [{"event": "BT_PROGRESS", "step": 10, "elapsed_s": float("nan")}], 10
        )
    )
    assert invalid["eta_seconds"] is None


def test_native_time_range_covers_uneven_batches_and_stops_after_training():
    elapsed = 90
    rows = []
    for step in range(1, 31):
        elapsed += 60 if step % 10 == 0 else 1
        rows.append({"event": "BT_PROGRESS", "step": step, "elapsed_s": elapsed})
    snap = snapshot(rows, 30)
    progress = progress_from_snapshot(snap)
    observed_average_remaining = (rows[-1]["elapsed_s"] - rows[0]["elapsed_s"]) / 29 * 70
    low, high = progress["eta_range_seconds"]
    assert low < observed_average_remaining < high
    assert progress["eta_seconds"] is None
    snap.stage = "final_validation"
    assert progress_from_snapshot(snap)["eta_range_seconds"] is None
    snap.state = "succeeded"
    assert progress_from_snapshot(snap)["eta_range_seconds"] is None
