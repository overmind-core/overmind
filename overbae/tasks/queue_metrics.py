"""Publish queue health independently of the workers whose backlog it measures."""

import logging

import boto3
from botocore.config import Config
from celery import shared_task
from django.conf import settings

from overbae.services.queue_capacity import (
    NAMESPACE,
    WORKER_SERVICES,
    metric_data,
    read_workloads,
)

logger = logging.getLogger(__name__)


@shared_task(name="overbae.tasks.queue_metrics.publish", ignore_result=True)
def publish():
    if not settings.QUEUE_METRICS_ENABLED:
        return {"enabled": False}
    cluster = settings.QUEUE_METRICS_CLUSTER
    if not cluster:
        raise ValueError("QUEUE_METRICS_CLUSTER is required when queue metrics are enabled")
    # The control pool is threaded; bound SDK I/O itself instead of declaring a
    # Celery time limit that threads cannot enforce. Failed samples stay missing,
    # which trips the monitor-health alarm rather than reporting a healthy zero.
    config = Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1})
    ecs = boto3.client("ecs", region_name=settings.AWS_REGION, config=config)
    services = ecs.describe_services(cluster=cluster, services=list(WORKER_SERVICES.values()))
    counts = {
        service["serviceName"]: service.get("runningCount", 0)
        for service in services.get("services", [])
    }
    running = {queue: counts.get(service, 0) for queue, service in WORKER_SERVICES.items()}
    points = metric_data(read_workloads(), running, cluster=cluster)
    cloudwatch = boto3.client("cloudwatch", region_name=settings.AWS_REGION, config=config)
    cloudwatch.put_metric_data(Namespace=NAMESPACE, MetricData=points)
    logger.info("queue_capacity: published %s metrics for %s", len(points), cluster)
    return {"metrics": len(points)}
