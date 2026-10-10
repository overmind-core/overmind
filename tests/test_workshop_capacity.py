def test_workshop_scaling_tracks_slots_and_preserves_busy_workers():
    from scripts.plan_workshop_capacity import capacity_plan

    plan = capacity_plan(cluster="test", min_capacity=1, max_capacity=6)
    target = plan["scalable-target.json"]
    assert target["MaxCapacity"] == 6
    assert target["SuspendedState"]["DynamicScalingInSuspended"] is True
    policy = plan["backlog-policy.json"]["TargetTrackingScalingPolicyConfiguration"]
    assert policy["TargetValue"] == 6
    assert policy["DisableScaleIn"] is True
    assert policy["CustomizedMetricSpecification"]["MetricName"] == "BacklogPerWorker"
    assert any(a["MetricName"] == "OldestQueuedAgeSeconds" for a in plan["alarms.json"])
