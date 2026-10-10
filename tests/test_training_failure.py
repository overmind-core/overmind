from modal_shared.training_failure import failure_receipt


def test_gpu_failure_returns_actionable_facts_without_raw_worker_text(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text(
        "private input and paths\ntorch.OutOfMemoryError: CUDA out of memory. Tried to allocate 1.98 GiB.\n"
    )
    receipt = failure_receipt(log)
    assert receipt["code"] == "gpu_memory_exhausted"
    assert "private" not in str(receipt)
    assert "paths" not in str(receipt)
    assert "memory" in receipt["message"]


def test_unknown_or_absent_worker_log_does_not_invent_a_cause(tmp_path):
    log = tmp_path / "missing.log"
    assert failure_receipt(log)["code"] == "training_process_failed"
    log.write_text('A source example says "CUDA out of memory".\n')
    assert failure_receipt(log)["code"] == "training_process_failed"
