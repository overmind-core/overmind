#!/usr/bin/env python3
"""Generate, never apply, a dedicated landing service and bounded scaling plan.

Input JSON comes from read-only ``ecs describe-task-definition`` and
``ecs describe-services`` calls. Files contain secret references, not values.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import sys
from pathlib import Path

# Also support ``python scripts/plan_landing_capacity.py`` from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.deploy_ecs import render_task_definition  # noqa: E402

NAMESPACE = "Overmind/Queues"
SERVICE = "celery-landing-worker"


def capacity_plan(
    definition,
    source_service,
    *,
    image,
    api_family,
    cluster,
    min_capacity,
    max_capacity,
    alarm_topic_arn=None,
):
    if min_capacity < 1 or max_capacity < min_capacity:
        raise ValueError("Landing requires 1 <= minimum <= maximum worker tasks")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", api_family):
        raise ValueError("Expected the existing API task-definition family")
    definition = copy.deepcopy(definition.get("taskDefinition", definition))
    source_service = source_service.get("services", [source_service])[0]
    containers = [
        c for c in definition["containerDefinitions"] if c["name"] == "celery-batch-worker"
    ]
    if len(containers) != 1 or definition.get("networkMode") != "awsvpc":
        raise ValueError("Expected the reviewed awsvpc celery-batch-worker task definition")
    original_name = containers[0]["name"]
    containers[0]["name"] = SERVICE
    for container in definition["containerDefinitions"]:
        for dependency in container.get("dependsOn", []):
            if dependency.get("containerName") == original_name:
                dependency["containerName"] = SERVICE
    log_options = containers[0].get("logConfiguration", {}).get("options", {})
    if "awslogs-stream-prefix" in log_options:
        log_options["awslogs-stream-prefix"] = "landing"
    family = definition["family"]
    if not family.endswith(original_name):
        raise ValueError("Batch task family must end with the batch container name")
    definition["family"] = family.removesuffix(original_name) + SERVICE
    rendered = render_task_definition(definition, service=SERVICE, image=image, cluster=cluster)
    service = {
        "cluster": cluster,
        "serviceName": SERVICE,
        "taskDefinition": definition["family"],
        "desiredCount": min_capacity,
        "schedulingStrategy": "REPLICA",
        "networkConfiguration": source_service["networkConfiguration"],
        "deploymentConfiguration": {
            "deploymentCircuitBreaker": {"enable": True, "rollback": True},
            "minimumHealthyPercent": 100,
            "maximumPercent": 200,
        },
        "enableECSManagedTags": True,
        "enableExecuteCommand": source_service.get("enableExecuteCommand", False),
    }
    for key in (
        "launchType",
        "capacityProviderStrategy",
        "platformVersion",
        "propagateTags",
    ):
        if source_service.get(key):
            service[key] = source_service[key]
    resource = f"service/{cluster}/{SERVICE}"
    dimensions = [
        {"Name": "ClusterName", "Value": cluster},
        {"Name": "Queue", "Value": "landing"},
    ]
    target = {
        "ServiceNamespace": "ecs",
        "ResourceId": resource,
        "ScalableDimension": "ecs:service:DesiredCount",
        "MinCapacity": min_capacity,
        "MaxCapacity": max_capacity,
    }
    policy = {
        "PolicyName": "landing-backlog-per-worker",
        "ServiceNamespace": "ecs",
        "ResourceId": resource,
        "ScalableDimension": "ecs:service:DesiredCount",
        "PolicyType": "TargetTrackingScaling",
        "TargetTrackingScalingPolicyConfiguration": {
            "TargetValue": 1.0,
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
        ("BlockedImports", 0, 1, "missing"),
        ("MissingWorkers", 0, 1, "breaching"),
        ("MetricHeartbeat", 1, 3, "breaching"),
    ):
        alarms.append(
            {
                "AlarmName": f"{cluster}-landing-{metric}",
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
                "AlarmDescription": "Landing capacity or durable import progress needs attention; inspect preserved import receipts.",
                **(
                    {"AlarmActions": [alarm_topic_arn], "OKActions": [alarm_topic_arn]}
                    if alarm_topic_arn
                    else {}
                ),
            }
        )
    # Age also requests a single additional worker. MaxCapacity still applies.
    age_policy = {
        "PolicyName": "landing-queue-age",
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
    arn = definition.get("taskRoleArn", "")
    if not arn.startswith("arn:"):
        raise ValueError("Source task definition must have an explicit taskRoleArn")
    arn_parts = arn.split(":")
    partition, account = arn_parts[1], arn_parts[4]
    region = source_service.get("serviceArn", "").split(":")[3:4] or ["eu-west-1"]
    region = region[0] or "eu-west-1"
    iam = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["cloudwatch:PutMetricData"],
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": NAMESPACE}},
            },
            {
                "Effect": "Allow",
                "Action": ["ecs:DescribeServices"],
                "Resource": [
                    f"arn:{partition}:ecs:{region}:{account}:service/{cluster}/{name}"
                    for name in (SERVICE, "celery-batch-worker")
                ],
            },
        ],
    }
    deploy_iam = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["ecs:RunTask"],
                "Resource": f"arn:{partition}:ecs:{region}:{account}:task-definition/{api_family}:*",
                "Condition": {
                    "ArnEquals": {
                        "ecs:cluster": f"arn:{partition}:ecs:{region}:{account}:cluster/{cluster}"
                    }
                },
            },
            {
                "Effect": "Allow",
                "Action": ["ecs:DescribeTasks"],
                "Resource": f"arn:{partition}:ecs:{region}:{account}:task/{cluster}/*",
            },
            {
                "Effect": "Allow",
                "Action": ["ecs:ListTasks"],
                "Resource": "*",
                "Condition": {
                    "ArnEquals": {
                        "ecs:cluster": f"arn:{partition}:ecs:{region}:{account}:cluster/{cluster}"
                    }
                },
            },
        ],
    }
    return {
        "deploy-role-policy.json": deploy_iam,
        "task-definition.json": rendered,
        "create-service.json": service,
        "scalable-target.json": target,
        "backlog-policy.json": policy,
        "age-policy.json": age_policy,
        "alarms.json": alarms,
        "metrics-role-policy.json": iam,
    }


def write_plan(plan, directory, *, cluster, image):
    directory.mkdir(parents=True, exist_ok=True)
    for filename, payload in plan.items():
        (directory / filename).write_text(json.dumps(payload, indent=2) + "\n")
    # No command is executed here. Keep the script anchored to its own artifacts.
    script = """#!/usr/bin/env bash
# Review all JSON, IAM changes, resource bounds, alarm destinations and image first.
# Run from the repository root only after the capacity/deployment change is approved.
set -euo pipefail
PLAN_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CLUSTER=__CLUSTER__
IMAGE=__IMAGE__
python scripts/deploy_ecs.py migrate --cluster "$CLUSTER" --image "$IMAGE"
TASK_DEF=$(aws ecs register-task-definition --cli-input-json "file://$PLAN_DIR/task-definition.json" --query 'taskDefinition.taskDefinitionArn' --output text)
aws ecs create-service --cli-input-json "file://$PLAN_DIR/create-service.json" --task-definition "$TASK_DEF" >/dev/null
# The regular release verifies container health before any producer deploys.
python scripts/deploy_ecs.py deploy --cluster "$CLUSTER" --image "$IMAGE" --service celery-landing-worker
CONTROL_DEF=$(aws ecs describe-services --cluster "$CLUSTER" --services celery-control-worker --query 'services[0].taskDefinition' --output text)
METRICS_ROLE_ARN=$(aws ecs describe-task-definition --task-definition "$CONTROL_DEF" --query 'taskDefinition.taskRoleArn' --output text)
aws iam put-role-policy --role-name "${METRICS_ROLE_ARN##*/}" --policy-name overmind-queue-metrics --policy-document "file://$PLAN_DIR/metrics-role-policy.json"
aws application-autoscaling register-scalable-target --cli-input-json "file://$PLAN_DIR/scalable-target.json"
aws application-autoscaling put-scaling-policy --cli-input-json "file://$PLAN_DIR/backlog-policy.json" >/dev/null
AGE_POLICY=$(aws application-autoscaling put-scaling-policy --cli-input-json "file://$PLAN_DIR/age-policy.json" --query PolicyARN --output text)
python - "$PLAN_DIR" "$AGE_POLICY" <<'ALARMS'
import json, pathlib, subprocess, sys, tempfile
root, policy = pathlib.Path(sys.argv[1]), sys.argv[2]
for alarm in json.loads((root / "alarms.json").read_text()):
    if alarm["MetricName"] == "OldestQueuedAgeSeconds":
        alarm["AlarmActions"] = [*alarm.get("AlarmActions", []), policy]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as fp:
        json.dump(alarm, fp); fp.flush()
        subprocess.run(["aws", "cloudwatch", "put-metric-alarm", "--cli-input-json", "file://" + fp.name], check=True)
ALARMS
# Finish via the reviewed release workflow: remaining workers -> API.
# Do not resend legacy imports. Adopt the durable receipt and transfer the exact
# queued envelope only after the updated watchdog and consumer are confirmed.
""".replace("__CLUSTER__", shlex.quote(cluster)).replace("__IMAGE__", shlex.quote(image))
    (directory / "rollout.sh").write_text(script)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-definition", type=Path, required=True)
    parser.add_argument("--service", type=Path, required=True)
    parser.add_argument("--api-family", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--min-capacity", type=int, default=1)
    parser.add_argument("--max-capacity", type=int, default=4)
    parser.add_argument("--alarm-topic-arn")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = capacity_plan(
        json.loads(args.task_definition.read_text()),
        json.loads(args.service.read_text()),
        image=args.image,
        api_family=args.api_family,
        cluster=args.cluster,
        min_capacity=args.min_capacity,
        max_capacity=args.max_capacity,
        alarm_topic_arn=args.alarm_topic_arn,
    )
    write_plan(plan, args.output, cluster=args.cluster, image=args.image)
    print(f"Wrote review-only capacity plan to {args.output}; no AWS resources changed.")


if __name__ == "__main__":
    main()
