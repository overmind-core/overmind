from __future__ import annotations

from overbae.services.finetuning_runner import (
    LIFECYCLE_COMPLETED,
    LIFECYCLE_DEPLOYMENT,
    LIFECYCLE_EVALUATION,
    LIFECYCLE_FAILED,
    LIFECYCLE_SETUP,
    LIFECYCLE_TRAINING,
    lifecycle_stage,
    sanitize_modal_log_lines,
    scrub_backend_names,
)


def test_setup_before_first_step():
    assert lifecycle_stage("running", {"trained_steps": 0}) == LIFECYCLE_SETUP
    assert lifecycle_stage("running", {"stage": "downloading_base_model"}) == LIFECYCLE_SETUP
    assert lifecycle_stage("queued", {}) == LIFECYCLE_SETUP


def test_training_when_steps_advance():
    assert (
        lifecycle_stage("running", {"trained_steps": 5, "total_steps": 300}) == LIFECYCLE_TRAINING
    )


def test_deployment_from_status():
    assert lifecycle_stage("deploying", {"trained_steps": 300}) == LIFECYCLE_DEPLOYMENT


def test_evaluation_while_final_eval_runs():
    prog = {"judge_evals": [{"kind": "final", "status": "running"}]}
    assert lifecycle_stage("succeeded", prog) == LIFECYCLE_EVALUATION


def test_completed_when_final_eval_done():
    prog = {"judge_evals": [{"kind": "final", "status": "completed", "aggregate_score": 0.8}]}
    assert lifecycle_stage("succeeded", prog) == LIFECYCLE_COMPLETED
    # No evals at all (evals disabled) → still COMPLETED once succeeded.
    assert lifecycle_stage("succeeded", {}) == LIFECYCLE_COMPLETED


def test_terminal_offramps():
    assert lifecycle_stage("failed", {"trained_steps": 10}) == LIFECYCLE_FAILED
    assert lifecycle_stage("cancelled", {}) == "cancelled"


def test_backward_compat_missing_progress():
    assert lifecycle_stage("running", None) == LIFECYCLE_SETUP
    assert lifecycle_stage("deploying", None) == LIFECYCLE_DEPLOYMENT
    assert lifecycle_stage("succeeded", None) == LIFECYCLE_COMPLETED


def test_sanitize_maps_modal_stage_markers():
    raw = [
        'MODAL_STAGE {"stage": "downloading_checkpoint"}',
        'MODAL_STAGE {"stage": "merging_lora"}',
        'MODAL_STAGE {"stage": "quantizing_fp8"}',
    ]
    assert sanitize_modal_log_lines(raw) == [
        "Downloading checkpoint…",
        "Merging LoRA adapter into base weights…",
        "Quantising to FP8…",
    ]


def test_sanitize_maps_raw_prints_and_strips_ansi():
    raw = [
        "\x1b[32m[Baseten] Listing checkpoint files for job-1\x1b[0m",
        "[FP8] Dispatching abc to L40S FP8 worker (is_lora=True)",
        "[Baseten] Download done — staged at /weights/.staging/x",
    ]
    assert sanitize_modal_log_lines(raw) == [
        "Listing checkpoint files…",
        "Merging + quantising to FP8 on GPU…",
        "Checkpoint download complete.",
    ]


def test_scrub_backend_names_redacts_all_providers():
    assert scrub_backend_names("Client error: Secret hf_access_token not found in Baseten") == (
        "Client error: Secret hf_access_token not found in the provider"
    )
    assert scrub_backend_names("NEBIUS_API_KEY is not set") == "the provider_API_KEY is not set"
    assert (
        scrub_backend_names("Deployed via Modal Functions") == "Deployed via the provider Functions"
    )
    assert scrub_backend_names("Together AI job failed") == "the provider job failed"
    assert scrub_backend_names("") == ""
    assert scrub_backend_names("all good, no backend mentioned") == "all good, no backend mentioned"
    # A letter-only trailing boundary would rewrite identifiers like ``modal_shared``.
    assert scrub_backend_names("No module named 'modal_shared'") == "No module named 'modal_shared'"
    assert (
        scrub_backend_names("Pre-warming GPU (L4) — building the cold-start memory snapshot…")
        == "Pre-warming GPU — building the cold-start memory snapshot…"
    )


def test_sanitize_job_error_hides_internal_failures():
    from overbae.services.finetuning_runner import (
        USER_FACING_DEPLOY_FAILURE,
        USER_FACING_TRAIN_FAILURE,
        sanitize_job_error,
    )

    assert (
        sanitize_job_error("RuntimeError: train.py exited with code 1") == USER_FACING_TRAIN_FAILURE
    )
    assert (
        sanitize_job_error("GPU pre-warm failed: CUDA out of memory") == USER_FACING_DEPLOY_FAILURE
    )
    assert sanitize_job_error(
        "Polling timed out — fine-tuning did not complete within 4 hours"
    ) == ("Polling timed out — fine-tuning did not complete within 4 hours")


def test_sanitize_drops_noise_and_dedupes():
    raw = [
        "Collecting torch==2.3.0",
        "some random framework log",
        "\rDownloading shards:  40%|████      | 2/5",
        'MODAL_STAGE {"stage": "quantizing_fp8"}',
        'MODAL_STAGE {"stage": "quantizing_fp8"}',
    ]
    assert sanitize_modal_log_lines(raw) == ["Quantising to FP8…"]
