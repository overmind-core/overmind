# Landing capacity and release order

`landing` has a dedicated Celery prefork worker with one process per ECS task. Bulk evaluation and connector work cannot occupy that process. Both bulk and landing workers disable speculative Redis prefetch; their queued work remains available to other consumers. `docker/worker-topology.json` is applied to every ECS task-definition revision, including commands and the landing consumer health check.

## Initial provisioning

Generate the plan from read-only snapshots of the existing batch service. Use the exact reviewed immutable release image; never use `latest` or the old production image, which lacks the new receipt schema and consumer health module.

```sh
aws ecs describe-task-definition --task-definition celery-batch-worker:6 \
  --profile overmind-prod-readonly --region eu-west-1 \
  --query taskDefinition --output json > batch-task-definition.json
aws ecs describe-services --cluster overmind-prod-cluster --services celery-batch-worker \
  --profile overmind-prod-readonly --region eu-west-1 \
  --query 'services[0]' --output json > batch-service.json
python scripts/plan_landing_capacity.py \
  --task-definition batch-task-definition.json --service batch-service.json \
  --api-family api --cluster overmind-prod-cluster --image "$REVIEWED_IMAGE_URI" \
  --min-capacity 1 --max-capacity 8 --alarm-topic-arn "$EXISTING_ALARM_TOPIC_ARN" \
  --output landing-capacity-plan
```

For staging, use its own batch snapshots and the observed API task-definition family (`overmind-staging-api`); the landing family retains the batch family prefix (`overmind-staging-celery-landing-worker`). Service and container names remain `celery-landing-worker` in each cluster. The `--api-family` value must come from that environment's current API task definition.

The generator only writes local files. Review the image, task-role references, secret references, EFS mounts, network, min/max capacity, alarm destination and IAM additions. The initial plan preserves the existing 4-vCPU/8-GiB batch resource envelope and creates additional capacity. Its 1–8 task bounds are cost limits, not a promise to absorb unlimited arrivals. Adjust after measuring real import memory and execution time.

The inspected production deployment inline policy does not grant `ecs:RunTask`, `ecs:ListTasks` or `ecs:DescribeTasks`. Check whether attached policies already grant them before applying an additional policy. The generated `deploy-role-policy.json` grants only API migration tasks in the selected cluster and task inspection; it does not expand `iam:PassRole`. Apply this reviewed additional policy to the environment's deployment role before using the new workflow. Its existing PassRole permission already covers the batch role reused by the landing task.

The control worker needs the separate generated `metrics-role-policy.json`: namespace-constrained `cloudwatch:PutMetricData` plus read-only `ecs:DescribeServices` for landing and bulk workers. Apply it to the actual control task role, not the GitHub deployment role. The monitor explicitly uses refreshing ECS task-role credentials; checkpoint archive keys injected into the same container cannot override that identity. The generated rollout script resolves that role from ECS. IAM, service provisioning and autoscaling are infrastructure mutations and are not performed by generating the plan. Use an approved deployment identity; the investigation's read-only profile cannot apply it.

After review, the generated rollout script migrates the schema, creates the dedicated service, verifies its registered landing-only consumer, and installs bounded scaling and alarms. For subsequent releases the workflow orders:

1. Schema migration in a one-off ECS API task; the web server is never started there.
1. Dedicated landing service on the new image, with actual consumer health confirmed.
1. Remaining workers, with source-controlled commands.
1. Beat, after workers can register its new periodic tasks.
1. API, after ready consumers and the scheduler.

The service must be provisioned in each environment before that environment's first release. An absent or unhealthy consumer stops the workflow before producer rollout. Existing `batch` consumers stay enabled. A route change affects future messages; it does not move legacy messages already in `batch`.

### First production release

The frontend has a separate release workflow. Publishing the release before the receipt-aware API is live would expose Retry import to the old API, whose run operation cannot recover an empty source. Complete this initial backend promotion before publishing the production release:

1. Validate the exact reviewed commit in staging and record its immutable image digest.
1. Generate and apply the approved production capacity plan using that same image. Production task definitions retain production environment and secret references; the image carries application code.
1. Deploy that image to the production control, I/O, batch and interactive services with `scripts/deploy_ecs.py deploy`, then beat, then API. Verify every service and confirm the previous worker/beat tasks have stopped.
1. Perform the guarded incident recovery and verify the source cell and workshop progress.
1. Publish the release for that exact commit. The independent frontend workflow now reaches a compatible API. The release's backend rollout follows the same consumer-first order with the same reviewed code.

Do not publish the release after only provisioning landing capacity or validating staging. This initial promotion gate is required for the new recovery behavior.

## Scaling and alerting

Every minute, the independent control lane publishes `Overmind/Queues` metrics with `ClusterName` and `Queue` dimensions. Demand comes from durable receipts in Postgres, including work waiting for admission. Broker depth alone hides that work. The metric task expires before the next sample and uses bounded AWS requests.

The plan scales landing on outstanding work per running worker, targeting one, and adds capacity when the oldest waiting import exceeds two minutes. Both policies obey the configured maximum. Alerts cover old waiting work, imports newly blocked by a system cause, missing workers and a missing monitor heartbeat. A missing sample remains missing; failures do not publish a false healthy zero. Supply an existing SNS alarm topic to receive notifications. Without one, CloudWatch alarm state is available but no notification destination is configured.

Imports exceeding their configured queue-age budget become visibly blocked while retaining their source. Raising the fleet ceiling is an explicit capacity decision. Batch metrics also include waiting `EvalGenerationWork` receipts; increasing bulk workers must be coordinated with the global and per-run admission limits, otherwise extra containers add cost without admitting more provider work.

Automatic scale-in is disabled. A normal ECS reduction can force-stop a long import after the 120-second container stop timeout. Safe manual scale-in requires a maintenance window that prevents new import admissions, verification that durable queued/running receipts and Celery active/reserved work are empty, and reduction only to the approved warm minimum before resuming admission. Do not use `cancel_consumer` as an unattended drain: the landing health check intentionally rejects a worker that no longer consumes landing. A future automated scale-in policy must integrate task scale-in protection and consumer draining first. [ECS task protection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-scale-in-protection.html).

## Existing queued imports

Complete the entire new-worker/beat rollout before repairing old queued imports, so no old watchdog can race receipt adoption. Confirm every previous worker/beat task has reached STOPPED; an old task still draining must finish before recovery. Recovery must validate the dataset, project, upload and original Celery task ID, retain a durable import receipt, and atomically transfer only the original queued broker envelope. Never blind-resend an upload that might already be active. Dataset error states require an explicit, narrowly validated repair; they must not be reset just because an old message exists.

Postgres receipt adoption and Redis movement are separate transactions. A failed move must retain both the receipt and the original envelope. A duplicate, reserved, active, missing or conflicting message requires inspection instead of another publish. Successful recovery is established by a completed durable import, committed source cell and subsequent workshop progress—not by queue movement alone.

For this incident, the dedicated management command is fixed to dataset `8fd9cc11-831f-4090-a0d5-1f4029f762e0`, project `df764b4b-b269-475b-b0f8-5df6402ded2d`, task `47322726-fdb2-4447-96f3-73d27beae0e3` and upload `b80e24be-5642-4839-ae1a-2fb7c271040e`. It requires the verified SHA-256, 60,302-byte CSV with 891 rows and 12 columns, no cells/chat/agent activity, and only the original watchdog error. It preserves `user_id="3"` and `infer_capability=false`.

After the complete approved release, inspect first:

```sh
python manage.py recover_landing_incident
```

The default is inspection only. Once the reported single-envelope relocation is reviewed, `python manage.py recover_landing_incident --apply` commits receipt adoption, then moves the original envelope atomically within Redis. A crash between stores leaves a retained durable receipt; this is not a distributed transaction.

If the old worker already acknowledged the original message without creating a source cell, the alternate inspection is:

```sh
python manage.py recover_landing_incident --retry-acknowledged-noop
```

This path refuses `PENDING`, unknown outcomes, any queued/unacked/active/reserved/scheduled copy, changed source, changed choices, or any existing durable import. Only the exact legacy `SUCCESS` result `{"status":"landed"}` with the original empty watchdog-error dataset qualifies. After review, adding `--apply` binds one durable retry with the original task ID; the importer controls publication and fences each execution attempt. This branch never guesses original choices from a dataset whose original message is gone.

The new receipt-aware batch worker may consume the old envelope during rollout. For an already errored dataset it records a blocked `legacy_interrupted` receipt and retains the upload, then acknowledges the envelope without importing. This is a third observable outcome; it is neither a still-queued envelope nor the old worker's misleading `SUCCESS/{status: landed}` no-op. Inspect it with:

```sh
python manage.py recover_landing_incident --retry-preserved-import
```

This mode accepts only the original task's never-started blocked receipt, exact preserved error, original input choices and retained source manifest. It checks the incident SHA-256 and CSV shape again, and refuses any source cell, agent activity, owner, attempt, competing publication or later state. It locks the receipt before the dataset and calls the existing importer's resume operation; it never creates a second receipt or publishes directly. After reviewing the dry run, add `--apply` to resume that receipt through its durable publisher. A concurrent or repeated application sees the first transition and refuses. Duplicate broker delivery remains fenced by the importer's attempt owner; no broker/result-backend absence guess is required for this receipt-owned path.

The general Console retry uses the same importer resume operation, but this incident command additionally enforces the reviewed identity and source hash. Do not use general retry to bypass an incident guard failure. Automated coverage verifies guard refusals and one publication from concurrent real-Postgres resumes using a local test upload. The original incident bytes were not downloaded, so successful production SHA validation and the resulting 891-row source cell must be verified during the approved recovery.

No command repairs sibling datasets in bulk. Each sibling needs its own source/task/error evidence and review.
