from rest_framework import serializers

from overbae.models import Issue
from overbae.services.issue_overview import RANGES

SEVERITIES = ["critical", "high", "medium", "low"]
STATES = ["new", "regressed", "escalating", "ongoing", "resolved", "muted"]


class IssueQuerySerializer(serializers.Serializer):
    project = serializers.UUIDField()
    range = serializers.ChoiceField(choices=sorted(RANGES), default="7d")
    capability = serializers.UUIDField(required=False)


class IssuePageQuerySerializer(IssueQuerySerializer):
    cause = serializers.ChoiceField(choices=Issue.Cause.choices, required=False)
    page = serializers.IntegerField(min_value=1, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=100, default=25)


class TrainingPlanQuerySerializer(serializers.Serializer):
    project = serializers.UUIDField()
    capability = serializers.UUIDField()
    range = serializers.ChoiceField(choices=sorted(RANGES), default="7d")


class IssueDetailQuerySerializer(serializers.Serializer):
    range = serializers.ChoiceField(choices=sorted(RANGES), default="7d")


class IssueRowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    cause = serializers.ChoiceField(choices=Issue.Cause.choices)
    signal_type = serializers.CharField()
    severity = serializers.ChoiceField(choices=SEVERITIES)
    state = serializers.ChoiceField(choices=STATES)
    capability = serializers.UUIDField(allow_null=True)
    capability_name = serializers.CharField()
    traces = serializers.IntegerField()
    rate = serializers.FloatField(allow_null=True)
    prior_rate = serializers.FloatField(allow_null=True)
    first_seen = serializers.DateTimeField()
    last_seen = serializers.DateTimeField()
    remedy = serializers.CharField()
    trend = serializers.ListField(child=serializers.IntegerField())


class IssuePageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = IssueRowSerializer(many=True)


class SystemIssueSerializer(IssueRowSerializer):
    capability_count = serializers.IntegerField()


class TrainingSignalSerializer(serializers.Serializer):
    capability = serializers.UUIDField()
    capability_name = serializers.CharField()
    model = serializers.CharField()
    issues = IssueRowSerializer(many=True)
    more_issues = serializers.IntegerField()
    failing_traces = serializers.IntegerField()
    passing_traces = serializers.IntegerField()


class ObservabilityOverviewSerializer(serializers.Serializer):
    traces_checked = serializers.IntegerField()
    signals_to_train = serializers.IntegerField()
    training_signals = TrainingSignalSerializer(many=True)
    system_issue_count = serializers.IntegerField()
    system_issues = SystemIssueSerializer(many=True)


class ModelMarkerSerializer(serializers.Serializer):
    at = serializers.DateTimeField()
    capability_name = serializers.CharField()
    model = serializers.CharField()


class TrendPointSerializer(serializers.Serializer):
    at = serializers.DateTimeField()
    traces = serializers.IntegerField()
    failing = serializers.IntegerField()
    failure_rate = serializers.FloatField(allow_null=True)
    score = serializers.FloatField(allow_null=True)
    cost = serializers.FloatField(allow_null=True)


class TrendTotalsSerializer(serializers.Serializer):
    traces = serializers.IntegerField()
    failing = serializers.IntegerField()
    failure_rate = serializers.FloatField(allow_null=True)
    score = serializers.FloatField(allow_null=True)
    cost = serializers.FloatField(allow_null=True)


class ObservabilityTrendsSerializer(serializers.Serializer):
    bucket = serializers.ChoiceField(choices=["hour", "day"])
    points = TrendPointSerializer(many=True)
    comparable = serializers.BooleanField()
    current = TrendTotalsSerializer()
    previous = TrendTotalsSerializer()
    markers = ModelMarkerSerializer(many=True)


class CapabilityActivitySerializer(TrendTotalsSerializer):
    capability = serializers.UUIDField()
    capability_name = serializers.CharField()
    severity = serializers.ChoiceField(choices=SEVERITIES, allow_null=True)
    issues = serializers.IntegerField()
    can_train = serializers.BooleanField()


class CapabilityActivityListSerializer(serializers.Serializer):
    capabilities = CapabilityActivitySerializer(many=True)


class TraceSelectionSerializer(serializers.Serializer):
    filters = serializers.DictField(child=serializers.CharField())
    limit = serializers.IntegerField()
    ordering = serializers.CharField()


class TrainingPlanSerializer(serializers.Serializer):
    capability = serializers.UUIDField()
    passing_traces = serializers.IntegerField()
    failing_traces = serializers.IntegerField()
    training_rows = serializers.IntegerField()
    eval_rows = serializers.IntegerField()
    eval_percent = serializers.IntegerField()
    selection = TraceSelectionSerializer()


class IssueCauseSerializer(serializers.Serializer):
    label = serializers.CharField()
    count = serializers.IntegerField()


class IssueExampleSerializer(serializers.Serializer):
    trace_id = serializers.CharField()
    occurred_at = serializers.DateTimeField()
    reason = serializers.CharField()
    input = serializers.CharField()
    output = serializers.CharField()


class RelatedIssueSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    capability_name = serializers.CharField()
    traces = serializers.IntegerField()
    rate = serializers.FloatField(allow_null=True)


class IssueDetailSerializer(IssueRowSerializer):
    sessions = serializers.IntegerField()
    buckets = serializers.ListField(child=serializers.DateTimeField())
    causes = IssueCauseSerializer(many=True)
    examples = IssueExampleSerializer(many=True)
    markers = ModelMarkerSerializer(many=True)
    related = RelatedIssueSerializer(many=True)


class TraceIssueQuerySerializer(serializers.Serializer):
    project = serializers.UUIDField()
    trace = serializers.CharField(max_length=32)


class TraceIssueSerializer(IssueRowSerializer):
    repeating = serializers.BooleanField()
    span_id = serializers.CharField()
    reason = serializers.CharField()


class TraceIssueListSerializer(serializers.Serializer):
    issues = TraceIssueSerializer(many=True)


class IssueStateSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["resolve", "mute", "reopen"])
