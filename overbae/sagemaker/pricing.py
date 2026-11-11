from __future__ import annotations

import json
import math
from typing import Any

from django.conf import settings

from overbae.core.aws import boto3_client

# Maps SageMaker instance types to Overmind rate card SKU keys and GPU count per instance.
SAGEMAKER_INSTANCE_SKUS: dict[str, tuple[list[str], int]] = {
    "ml.g5.2xlarge": (["a10g"], 1),
    "ml.g6.2xlarge": (["l4"], 1),
    "ml.g6e.2xlarge": (["l40s"], 1),
    "ml.p4d.24xlarge": (["a100_40gb"], 8),
    "ml.p4de.24xlarge": (["a100_80gb"], 8),
    "ml.p5.4xlarge": (["h100"], 1),
    "ml.p5.48xlarge": (["h100"], 8),
    "ml.p5e.48xlarge": (["h200"], 8),
}


def _extract_usd_per_hour(price_item: str | dict[str, Any]) -> float | None:
    doc = json.loads(price_item) if isinstance(price_item, str) else price_item
    if not isinstance(doc, dict):
        return None
    terms = (doc.get("terms") or {}).get("OnDemand") or {}
    for term in terms.values():
        dimensions = (term or {}).get("priceDimensions") or {}
        for dim in dimensions.values():
            usd_str = ((dim or {}).get("pricePerUnit") or {}).get("USD")
            if usd_str is not None:
                try:
                    val = float(usd_str)
                    if math.isfinite(val) and val > 0:
                        return val
                except (TypeError, ValueError):
                    continue
    return None


def fetch_sagemaker_rates() -> dict[str, float]:
    region = getattr(settings, "AWS_REGION", "eu-west-1") or "eu-west-1"
    pricing = boto3_client("pricing", region_name="us-east-1")
    resp = pricing.get_products(
        ServiceCode="AmazonSageMaker",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        ],
    )
    rates: dict[str, float] = {}
    for item in resp.get("PriceList") or []:
        doc = json.loads(item) if isinstance(item, str) else item
        if not isinstance(doc, dict):
            continue
        attrs = (doc.get("product") or {}).get("attributes") or {}
        instance_type = (
            str(attrs.get("instanceType") or attrs.get("component") or "")
            .removesuffix("-Training")
            .removesuffix("-Inference")
        )
        if instance_type not in SAGEMAKER_INSTANCE_SKUS:
            continue
        hourly = _extract_usd_per_hour(doc)
        if hourly is None:
            continue
        skus, gpu_count = SAGEMAKER_INSTANCE_SKUS[instance_type]
        per_gpu = round(hourly / max(1, gpu_count), 6)
        for sku in skus:
            key = f"gpu_hour_cost_{sku}"
            if key not in rates or instance_type == "ml.p5.4xlarge":
                rates[key] = per_gpu
    if rates:
        rates.setdefault("cpu_hour_cost", 0.048)
        rates.setdefault("mem_gib_hour_cost", 0.012)
    return rates
