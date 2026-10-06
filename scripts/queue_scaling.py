"""Reviewable scaling bounds for long-running Celery workers."""

NAMESPACE = "Overmind/Queues"


def scaling_plan(
    *, cluster, queue, service, slots, min_capacity, max_capacity, alarm_topic_arn=None
):
    if slots < 1 or min_capacity < 1 or max_capacity < min_capacity:
        raise ValueError("Expected positive slots and 1 <= minimum <= maximum workers")
    resource = f"service/{cluster}/{service}"
    dimensions = [
        {"Name": "ClusterName", "Value": cluster},
        {"Name": "Queue", "Value": queue},
    ]
    target = {
        "ServiceNamespace": "ecs",
        "ResourceId": resource,
        "ScalableDimension": "ecs:service:DesiredCount",
        "MinCapacity": min_capacity,
        "MaxCapacity": max_capacity,
        "SuspendedState": {
            "DynamicScalingInSuspended": True,
            "DynamicScalingOutSuspended": False,
            "ScheduledScalingSuspended": False,
        },
    }
    policy = {
        "PolicyName": f"{queue}-backlog-per-worker",
        "ServiceNamespace": "ecs",
        "ResourceId": resource,
        "ScalableDimension": "ecs:service:DesiredCount",
        "PolicyType": "TargetTrackingScaling",
        "TargetTrackingScalingPolicyConfiguration": {
            "TargetValue": float(slots),
            "ScaleOutCooldown": 60,
            "ScaleInCooldown": 900,
            "DisableScaleIn": True,
            "CustomizedMetricSpecification": {
                "Namespace": NAMESPACE,
                "MetricName": "BacklogPerWorker",
                "Dimensions": dimensions,
                "Statistic": "Average",
                "Unit": "Count",
            },
        },
    }
    alarms = []
    for metric, threshold, periods, missing in (
        ("OldestQueuedAgeSeconds", 120, 2, "missing"),
        ("BlockedImports" if queue == "landing" else "UnknownWork", 0, 1, "missing"),
        ("MissingWorkers", 0, 1, "breaching"),
        ("MetricHeartbeat", 1, 3, "breaching"),
    ):
        alarms.append(
            {
                "AlarmName": f"{cluster}-{queue}-{metric}",
                "Namespace": NAMESPACE,
                "MetricName": metric,
                "Dimensions": dimensions,
                "Statistic": "Maximum",
                "Period": 60,
                "EvaluationPeriods": periods,
                "DatapointsToAlarm": periods,
                "Threshold": threshold,
                "ComparisonOperator": "LessThanThreshold"
                if metric == "MetricHeartbeat"
                else "GreaterThanThreshold",
                "TreatMissingData": missing,
                "AlarmDescription": "Queue capacity or progress needs attention; inspect the waiting and running work.",
                **(
                    {"AlarmActions": [alarm_topic_arn], "OKActions": [alarm_topic_arn]}
                    if alarm_topic_arn
                    else {}
                ),
            }
        )
    # Age also requests a single additional worker. MaxCapacity still applies.
    age_policy = {
        "PolicyName": f"{queue}-queue-age",
        "ServiceNamespace": "ecs",
        "ResourceId": resource,
        "ScalableDimension": "ecs:service:DesiredCount",
        "PolicyType": "StepScaling",
        "StepScalingPolicyConfiguration": {
            "AdjustmentType": "ChangeInCapacity",
            "Cooldown": 120,
            "MetricAggregationType": "Maximum",
            "StepAdjustments": [{"MetricIntervalLowerBound": 0, "ScalingAdjustment": 1}],
        },
    }
    return {
        "scalable-target.json": target,
        "backlog-policy.json": policy,
        "age-policy.json": age_policy,
        "alarms.json": alarms,
    }
