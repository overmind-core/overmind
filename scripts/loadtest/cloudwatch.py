"""Pull the server view of a production load run from CloudWatch (read-only).

  uv run python scripts/loadtest/cloudwatch.py scripts/loadtest/runs/<run> --profile overmind

Reads the run's start and finish from result.json, discovers the ECS services, RDS instance and
proxy, ElastiCache nodes and the ALB, and writes cloudwatch.json (one-minute series) plus a
peak table appended to summary.md. Only Describe/List/GetMetricData calls are made.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import boto3

PERIOD = 60


def queries(session, cluster: str) -> list[dict]:
    ecs = session.client("ecs")
    rds = session.client("rds")
    cache = session.client("elasticache")
    elb = session.client("elbv2")
    cw = session.client("cloudwatch")
    out: list[dict] = []

    def add(label, namespace, metric, dims, stat="Maximum"):
        out.append(
            {
                "label": label,
                "stat": stat,
                "query": {
                    "Metric": {
                        "Namespace": namespace,
                        "MetricName": metric,
                        "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()],
                    },
                    "Period": PERIOD,
                    "Stat": stat,
                },
            }
        )

    services = [arn.rsplit("/", 1)[-1] for arn in ecs.list_services(cluster=cluster)["serviceArns"]]
    for name in services:
        dims = {"ClusterName": cluster, "ServiceName": name}
        add(f"ecs/{name}/cpu", "AWS/ECS", "CPUUtilization", dims)
        add(f"ecs/{name}/memory", "AWS/ECS", "MemoryUtilization", dims)
        add(f"ecs/{name}/tasks", "ECS/ContainerInsights", "RunningTaskCount", dims)

    for metric in cw.get_paginator("list_metrics").paginate(Namespace="Overmind/Queues"):
        for item in metric["Metrics"]:
            dims = {d["Name"]: d["Value"] for d in item["Dimensions"]}
            if dims.get("ClusterName") != cluster:
                continue
            add(
                f"queue/{dims.get('Queue', '-')}/{item['MetricName']}",
                "Overmind/Queues",
                item["MetricName"],
                dims,
            )

    for db in rds.describe_db_instances()["DBInstances"]:
        ident = db["DBInstanceIdentifier"]
        dims = {"DBInstanceIdentifier": ident}
        add(f"rds/{ident}/connections", "AWS/RDS", "DatabaseConnections", dims)
        add(f"rds/{ident}/cpu", "AWS/RDS", "CPUUtilization", dims)
        add(f"rds/{ident}/freeable_memory", "AWS/RDS", "FreeableMemory", dims, "Minimum")
    for proxy in rds.describe_db_proxies()["DBProxies"]:
        dims = {"ProxyName": proxy["DBProxyName"]}
        for metric in (
            "ClientConnections",
            "DatabaseConnections",
            "DatabaseConnectionsCurrentlySessionPinned",
        ):
            add(f"proxy/{proxy['DBProxyName']}/{metric}", "AWS/RDS", metric, dims)

    for node in cache.describe_cache_clusters()["CacheClusters"]:
        dims = {"CacheClusterId": node["CacheClusterId"]}
        for metric in ("EngineCPUUtilization", "DatabaseMemoryUsagePercentage", "CurrConnections"):
            add(f"redis/{node['CacheClusterId']}/{metric}", "AWS/ElastiCache", metric, dims)
        add(
            f"redis/{node['CacheClusterId']}/Evictions", "AWS/ElastiCache", "Evictions", dims, "Sum"
        )

    for lb in elb.describe_load_balancers()["LoadBalancers"]:
        dims = {"LoadBalancer": lb["LoadBalancerArn"].split("loadbalancer/", 1)[1]}
        name = lb["LoadBalancerName"]
        add(f"alb/{name}/requests", "AWS/ApplicationELB", "RequestCount", dims, "Sum")
        add(
            f"alb/{name}/target_5xx", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", dims, "Sum"
        )
        add(f"alb/{name}/elb_5xx", "AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", dims, "Sum")
        add(f"alb/{name}/response_p95", "AWS/ApplicationELB", "TargetResponseTime", dims, "p95")
        add(
            f"alb/{name}/active_connections",
            "AWS/ApplicationELB",
            "ActiveConnectionCount",
            dims,
            "Sum",
        )
    return out


def fetch(session, specs: list[dict], start: datetime, end: datetime) -> dict[str, dict]:
    cw = session.client("cloudwatch")
    series: dict[str, dict] = {}
    for offset in range(0, len(specs), 400):
        batch = specs[offset : offset + 400]
        request = [
            {"Id": f"m{offset + i}", "Label": spec["label"], "MetricStat": spec["query"]}
            for i, spec in enumerate(batch)
        ]
        for page in cw.get_paginator("get_metric_data").paginate(
            MetricDataQueries=request, StartTime=start, EndTime=end, ScanBy="TimestampAscending"
        ):
            for result in page["MetricDataResults"]:
                entry = series.setdefault(result["Label"], {"t": [], "v": []})
                entry["t"] += [t.isoformat() for t in result["Timestamps"]]
                entry["v"] += result["Values"]
    return series


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--profile", default="overmind")
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--cluster", default="overmind-prod-cluster")
    parser.add_argument("--margin-minutes", type=int, default=3)
    args = parser.parse_args()

    result = json.loads((args.run / "result.json").read_text())
    margin = timedelta(minutes=args.margin_minutes)
    start = datetime.fromisoformat(result["started"]) - margin
    end = datetime.fromisoformat(result["finished"]) + margin
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    specs = queries(session, args.cluster)
    series = fetch(session, specs, start, end)
    peaks = {
        label: (min(data["v"]) if "freeable_memory" in label else max(data["v"]))
        for label, data in sorted(series.items())
        if data["v"]
    }
    (args.run / "cloudwatch.json").write_text(
        json.dumps(
            {"window": [start.isoformat(), end.isoformat()], "peaks": peaks, "series": series},
            indent=1,
        )
    )
    lines = ["", "## CloudWatch (server view)", "", "| metric | peak |", "|---|---|"]
    lines += [f"| {label} | {value:.2f} |" for label, value in peaks.items()]
    with (args.run / "summary.md").open("a") as summary:
        summary.write("\n".join(lines) + "\n")
    print(f"{len(peaks)} metrics with data, {len(specs) - len(peaks)} empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
