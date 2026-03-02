from typing import Any
from pydantic import BaseModel, Field
import uuid
from overmind.utils import calculate_llm_usage_cost, safe_int
from overmind.models.traces import SpanModel, TraceModel


def _nonempty(value: Any) -> Any:
    """Return *value* only if it is non-empty (not None / {} / []).

    Stored JSONB columns that were never set default to {} or []; we normalise
    those to None so the frontend correctly shows "—" instead of "{}" or "[]".
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)) and len(value) == 0:
        return None
    return value


class SpanResponseModel(BaseModel):
    user_id: uuid.UUID | None = Field(None, alias="UserId")
    project_id: uuid.UUID | None = Field(None, alias="ProjectId")
    trace_id: uuid.UUID = Field(..., alias="TraceId")
    span_id: str = Field(..., alias="SpanId")
    parent_span_id: str | None = Field(None, alias="ParentSpanId")
    trace_state: str | None = Field("", alias="TraceState")
    start_time_unix_nano: int = Field(..., alias="StartTimeUnixNano")
    end_time_unix_nano: int = Field(..., alias="EndTimeUnixNano")
    duration_nano: int = Field(..., alias="DurationNano")
    status_code: int = Field(..., alias="StatusCode")
    status_message: str | None = Field("", alias="StatusMessage")
    resource_attributes: dict = Field(default_factory=dict, alias="ResourceAttributes")
    span_attributes: dict = Field(default_factory=dict, alias="SpanAttributes")
    inputs: Any | None = Field(None, alias="Inputs")
    outputs: Any | None = Field(None, alias="Outputs")
    policy_outcome: str | None = Field("", alias="PolicyOutcome")
    scope_name: str | None = Field("", alias="ScopeName")
    name: str | None = Field("", alias="Name")
    scope_version: str | None = Field("", alias="ScopeVersion")
    events: list = Field(default_factory=list, alias="Events")
    links: list = Field(default_factory=list, alias="Links")

    @classmethod
    def from_orm_obj(
        cls, obj: SpanModel, trace_obj: TraceModel | None = None
    ) -> "SpanResponseModel":
        # Directly load from ORM (SQLAlchemy) object to Pydantic model
        return cls(
            UserId=getattr(obj, "user_id", None),
            ProjectId=getattr(obj, "project_id", None),
            BusinessId=None,
            TraceId=obj.trace_id,
            SpanId=obj.span_id,
            ParentSpanId=obj.parent_span_id,
            TraceState="",  # not present in ORM
            StartTimeUnixNano=obj.start_time_unix_nano,
            EndTimeUnixNano=obj.end_time_unix_nano,
            DurationNano=obj.end_time_unix_nano - obj.start_time_unix_nano,
            StatusCode=obj.status_code,
            StatusMessage="",
            ResourceAttributes=trace_obj.metadata_attributes if trace_obj else {},
            SpanAttributes={
                **obj.metadata_attributes,
                "feedback_score": obj.feedback_score,
                "cost": obj.metadata_attributes.get("cost")
                or calculate_llm_usage_cost(
                    str(obj.metadata_attributes.get("gen_ai.request.model", "") or ""),
                    safe_int(
                        obj.metadata_attributes.get("gen_ai.usage.input_tokens", 0)
                    ),
                    safe_int(
                        obj.metadata_attributes.get("gen_ai.usage.output_tokens", 0)
                    ),
                ),
            },
            Inputs=_nonempty(obj.input),
            Outputs=_nonempty(obj.output),
            PolicyOutcome=obj.metadata_attributes.get("PolicyOutcome", ""),
            ScopeName=trace_obj.source if trace_obj else obj.operation,
            Name=obj.operation,
            ScopeVersion=trace_obj.version if trace_obj else "",
            Events=obj.metadata_attributes.get("events", []),
            Links=obj.metadata_attributes.get("links", []),
        )

    class Config:
        allow_population_by_field_name = True
        orm_mode = True
        extra = "allow"


class TraceSpansResponseModel(BaseModel):
    trace_id: str
    spans: list[SpanResponseModel]
    span_count: int


class TraceListResponseModel(BaseModel):
    class TraceListFilterModel(BaseModel):
        # ── Required / structural ────────────────────────────────────────────
        project_id: uuid.UUID
        user_id: str | None = None  # set from auth context, not from the request
        start_time_nano: int | None = None
        end_time_nano: int | None = None
        root_only: bool = True
        limit: int = 100
        offset: int = 0
        # ── Prompt shorthand (compound key, kept explicit) ───────────────────
        prompt_slug: str | None = None
        prompt_version: str | None = None
        # ── Generic DRF-style filter expressions ────────────────────────────
        # Each entry: "field;operator;value"  (see trace_filter_backend.py)
        query: list[str] = []

    traces: list[SpanResponseModel]
    count: int
    limit: int
    offset: int
    filters: TraceListFilterModel
