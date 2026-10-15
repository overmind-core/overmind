# Workshop rollout

This merge retires main's mutable Execution and separate DecisionJob runtimes.
Their records, checkpoint references, costs and attachment metadata are archived;
they do not become runnable native FinetuningJob models. The migration preserves
cell IDs, source bytes and active selections. It refuses active legacy execution
or decision jobs. This is a maintenance cutover, not a rolling schema change.

## Infrastructure prerequisite

[cloud-platform PR #11](https://github.com/overmind-core/cloud-platform/pull/11)
contains the companion infrastructure against
`7b801e1c007e98f5d3e79be34fe7c3e84f88b985`. It passed Terraform formatting and
`init -backend=false` / `validate`. It has not been planned or applied to AWS.
Review and apply it in the infrastructure repository; keep state and environment
secrets there.

The patch provisions a dedicated EC2 Docker host, singleton controller service,
EFS access, database/Redis connectivity and the release role's task-role access.
Only the controller receives the Docker socket. Recipe containers have no network
or host mounts. The controller uses a direct PostgreSQL connection because its
session advisory lock cannot use transaction pooling. No provider credentials
are passed to this service.

Build the approved Workshop runtime for the production host's linux/amd64
architecture and publish it to ECR. Set `workshop_runtime_images` to the Docker
config image ID mapped to its immutable ECR manifest URI. These are different
hashes; verify both after pulling the image on the host. Retain images referenced
by existing recipes. An empty map deliberately provisions no host capacity.
New runtime images require replacement or explicit preparation of the existing
host; a launch-template change alone does not refresh it. Verify the ECS host is
registered with `workshop=true` and all approved images are locally available.
The service initially has desired count zero until the schema cutover.

Keep main's isolated `sandbox` service: it has no app-data mount or application
credentials and no outbound network. Landing workers use `SANDBOX_URL` and
`SANDBOX_TOKEN`; there is no in-process document-parsing fallback. The parser
retains branch OCR, native-text recovery, provenance and streamed progress.

## First cutover

1. Build the release image without deploying (`build_only`). Deploy the exact
   pinned training, decision-serving and decision-evaluation worker releases.
1. Put the public API in maintenance mode. Stop new submissions and the scheduler,
   drain queued and active work, and reconcile all outstanding provider calls.
   Record each ECS service's desired count and autoscaling settings. Suspend
   autoscaling and stop API, all five workers and beat; wait for running and
   pending counts to reach zero. Do not discard broker messages or treat a remote
   cancellation request as proof that work ended.
1. Confirm a restorable PostgreSQL backup and retained media snapshot. Rehearse
   against a disposable restored copy. The included validator also proves clean
   and populated main/branch upgrades using loopback-only temporary databases:
   `PYTHONPATH=. uv run python scripts/validate_migration_upgrade.py origin/main`
   (repeat with the previous branch commit and `fresh`). It never targets the
   application's database.
1. Run `scripts/deploy_ecs.py preflight --cluster CLUSTER`, then
   `scripts/deploy_ecs.py migrate --cluster CLUSTER --image IMMUTABLE_IMAGE`.
   Preflight refuses the first cutover while old consumers are active. Migration
   archives legacy rows under transactional table locks before dropping fields.
   No application container performs implicit database bootstrap.
1. Deploy using `scripts/deploy_ecs.py deploy --cluster CLUSTER --image IMAGE --service SERVICE --desired-count RECORDED_COUNT`. Start sandbox, then
   workshop-runner (always exactly one), landing, the remaining workers, beat,
   and finally API. Restore recorded capacity explicitly; the script refuses to
   guess counts for stopped services. Each deployment waits for readiness before
   continuing. Keep maintenance mode until the checks below pass. Later automated
   releases preserve desired counts and follow the same consumer-first ordering.
1. Verify source upload/export, OCR, pipeline preview/publication/cancellation,
   preparation, bounded training, held-out evaluation and inference through the
   intended project-scoped MCP endpoint. Check runner heartbeat and image health,
   exact output cells and fingerprints, and source retention. Restore autoscaling
   and traffic only after successful checks.

Do not reverse the archival cutover against new writes. A failed cutover requires
an explicitly reviewed forward repair, or restoring the matching database/media
snapshot and previous application version while traffic remains stopped.

## Local environment

Before recreating services, inspect their physical Docker volume names. Preserve
those names in the ignored local compose override; renaming a compose project
must not switch an existing database or media volume. The repository's defaults
are not proof of which volumes an older local installation currently uses.
