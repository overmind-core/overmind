# Isolated live-scoring rollout

The application and `overmind-core/cloud-platform` changes form one release.
The new trace service must exist before the ordinary deployment preflight passes.
Do not let Terraform restart an occupied mixed IO service during this cutover.

1. Verify the target account and cluster. Save current task definitions, desired
   counts and queue/worker observations. Build an immutable application image and
   retain its digest and source revision. The production base must match the
   dependency lock and unchanged source before using an overlay image.
1. Run the additive live-scoring migration in a one-off API task using the new
   image, without moving the API service. Use `register` and `migration_request`
   from `scripts/deploy_ecs.py`; verify that exact task's API container exits zero.
   The ordinary `migrate` command deliberately still requires consumer infrastructure.
   Registering this definition also makes the reviewed image the latest API family
   image, which Terraform uses when provisioning the new service.
1. Review a targeted Terraform plan for the new `celery-traces-worker` service,
   its roles/capacity and queue-metric permissions. Apply only those resources
   first. Defer changes to the existing IO service until its work is drained.
1. Deploy and verify control workers, then the trace-only worker, using the new
   immutable image. Confirm their registered task catalogues and exact queues.
1. Deploy IO with `scripts/deploy_ecs.py`. Its maintenance task cancels the mixed
   consumers and waits for active and reserved tasks to finish. Missing replies,
   nonzero maintenance exit or a timeout stop deployment. A failed drain restores
   consumption; it never purges queues or revokes provider work.
1. Finish worker, beat and API deployment. Apply the remaining reviewed Terraform
   changes, including suspended IO scale-in and trace backlog/age alarms. Review
   the plan for unrelated drift before applying it.
1. Verify every running task uses the intended immutable image, exact queues and
   healthy consumers. Confirm no mixed IO consumer remains, no worker reserves
   trace tasks ahead of free slots, and preparation observation advances while
   live-scoring backlog is present. Inspect admissions by project: global 16 and
   project two are the default ceilings, independent of worker count.
1. Check queue metric publication and alarms. Trace scaling demand is bounded by
   project/global admission budgets; raw waiting count and age remain visible.
   Verify API health, MCP discovery/authentication and the original stalled
   preparation through its existing provider acknowledgement before launching
   any GPU training. Never resubmit an unknown training/provider acknowledgement.

Trace attempts have an enforced five-minute prefork limit and a six-minute
running lease. A broker redelivery cannot claim a running or superseded token.
The existing scoring claim grace and three-attempt budget recover incomplete
judging; scores are never invented on timeout. Automatic scale-in is suspended.

Rollback stops new producers before replacing consumers with code that lacks the
new task names. Leave the additive tables and waiting receipts intact. Never
purge the broker to make a rollback appear complete.
