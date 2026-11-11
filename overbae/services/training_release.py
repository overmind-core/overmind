import os
from pathlib import Path

import modal
from django.conf import settings

from modal_shared.training_release import identity
from overbae.core.aws import boto3_client


def current():
    if getattr(settings, "FINETUNING_BACKEND", "modal") == "sagemaker":
        base = identity(Path(__file__).resolve().parents[2])
        region = getattr(settings, "AWS_REGION", "eu-west-1")
        sft_bucket = getattr(settings, "AWS_SFT_BUCKET_NAME", "")
        weights_bucket = getattr(settings, "AWS_WEIGHTS_BUCKET_NAME", "")
        if not sft_bucket or not weights_bucket:
            raise ValueError(
                "Set AWS_SFT_BUCKET_NAME and AWS_WEIGHTS_BUCKET_NAME before preparing or launching training."
            )
        return {
            **base,
            "backend": "sagemaker",
            "environment": region,
            "region": region,
            "sft_bucket": sft_bucket,
            "weights_bucket": weights_bucket,
            "image_tag": getattr(settings, "SAGEMAKER_IMAGE_TAG", "latest"),
        }
    environment = os.environ.get("MODAL_ENVIRONMENT", "").strip()
    if not environment:
        raise ValueError("Set MODAL_ENVIRONMENT explicitly before preparing or launching training.")
    return {**identity(Path(__file__).resolve().parents[2]), "environment": environment}


def for_job(job):
    release = (job.requested_configuration or {}).get("runtime")
    if not release:
        raise ValueError(
            "This job has no pinned worker release. Reconcile its runtime before dispatch."
        )
    return release


def verify(release):
    if release.get("backend") == "sagemaker":
        s3 = boto3_client("s3", region_name=release.get("region"))
        for bucket_key in ("sft_bucket", "weights_bucket"):
            bucket = release.get(bucket_key)
            if not bucket:
                raise ValueError("The deployed training release does not match the pinned runtime.")
            s3.head_bucket(Bucket=bucket)
        expected = identity(Path(__file__).resolve().parents[2])
        if any(
            expected.get(key) != release.get(key)
            for key in ("processor", "training", "data_format")
        ):
            raise ValueError("The deployed training release does not match the pinned runtime.")
        return release
    observed = modal.Function.from_name(
        release["app"], "release_identity", environment_name=release["environment"]
    ).remote()
    if any(
        observed.get(key) != release[key]
        for key in ("app", "release", "processor", "training", "data_format")
    ):
        raise ValueError("The deployed training release does not match the pinned runtime.")
    return observed
