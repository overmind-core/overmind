"""Publish lane demand from the control lane, independently of the lanes it measures."""

import logging

from celery import shared_task

from overbae import ecs_task
from overbae.lanes import service
from overbae.services.queue_capacity import NAMESPACE, metric_data, read_workloads

logger = logging.getLogger(__name__)


@shared_task(name="overbae.tasks.queue_metrics.publish", ignore_result=True)
def publish():
    task = ecs_task.Task.current()
    if task is None:
        return {"enabled": False}
    # A failed sample stays missing, which trips the heartbeat alarm instead of
    # reporting a healthy zero.
    workloads = read_workloads()
    services = {service(lane): lane for lane in workloads}
    described = task.client("ecs").describe_services(cluster=task.cluster, services=list(services))
    running = {
        services[item["serviceName"]]: item.get("runningCount", 0)
        for item in described.get("services", [])
    }
    points = metric_data(workloads, running, cluster=task.cluster)
    task.client("cloudwatch").put_metric_data(Namespace=NAMESPACE, MetricData=points)
    logger.info("queue_capacity: published %s metrics for %s", len(points), task.cluster)
    return {"cluster": task.cluster, "metrics": len(points)}
