import math

from pydantic import BaseModel, ConfigDict, Field

from overbae.core.errors import InputValidationError


class TrainingConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_training_run_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unpriced_allowance_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class RuntimeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_steps: int = Field(ge=1, le=128)
    max_seconds: int = Field(ge=60, le=3600)


def profile_options(hyperparameters):
    requested = hyperparameters.get("runtime_profile")
    if requested is None:
        return {}
    profile = RuntimeProfile.model_validate(requested)
    if hyperparameters.get("max_steps") != profile.max_steps:
        raise InputValidationError("The runtime profile step bound must match max_steps")
    # A timed-out profile must not acquire another full provider attempt.
    return {"timeout": profile.max_seconds, "retries": 0}


def require_authorized_forecast(protocol, quote_id):
    policy = TrainingConstraints.model_validate(protocol.get("constraints") or {})
    if policy.max_training_run_usd is None:
        return
    quote = protocol.get("forecast") or {}
    if not quote or quote.get("id") != quote_id:
        raise InputValidationError(
            "Review the saved forecast and supply its quote_id before launching"
        )
    if policy.unpriced_allowance_usd is None:
        raise InputValidationError(
            "The forecast has unpriced components; declare their planning allowance before authorizing"
        )
    for candidate in quote["variants"]:
        cost = candidate.get("training_gpu_usd")
        if not cost or any(type(v) not in {int, float} or not math.isfinite(v) for v in cost):
            raise InputValidationError(
                "A measured forecast is required by the saved training constraint"
            )
        if cost[1] + policy.unpriced_allowance_usd > policy.max_training_run_usd:
            raise InputValidationError(
                "The forecast exceeds the authorized per-run planning allowance"
            )
