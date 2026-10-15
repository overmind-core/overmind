# Workshop rollout

This merge retires main's mutable Execution and separate DecisionJob runtimes.
Their records, checkpoint references, costs and attachment metadata are archived;
they do not become runnable native FinetuningJob models. The migration preserves
cell IDs, source bytes and active selections. It refuses active legacy execution
or decision jobs. This is a maintenance cutover, not a rolling schema change.

## Infrastructure prerequisite

Retain main's Fargate services, isolated sandbox, five Celery worker profiles,
per-project interactive admission, queue autoscaling and scale-in drain policy.
The dedicated EC2/Docker controller proposed in cloud-platform PR #11 is not a
dependency of this application release and must not be applied for this rollout.

Landing and interactive workers need SANDBOX_URL and SANDBOX_TOKEN and network
access to the sandbox. The sandbox gets no app credentials or media mounts and
has no outbound route. Deploy the same immutable application image to all three.
Its pinned libraries and offline tokenizer cache support retained packages and
branch document/OCR fixes. There is no in-process parsing or script fallback.

Existing Docker-pinned package revisions remain historical records. Register new
revisions with the sandbox identity advertised by inspect_dataset_workbench; do
not rewrite old manifests. Current recipes do not install dependencies at runtime.

## First cutover

1. Build the release image without deploying (`build_only`). Deploy the exact
   pinned training, decision-serving and decision-evaluation worker releases.
1. Put the public API in maintenance mode. Stop new submissions and the scheduler,
   drain queued and active work, and reconcile all outstanding provider calls.
   Record each ECS service's desired count and autoscaling settings. Suspend
   autoscaling and stop API, all five workers, beat and sandbox; wait for running and
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
   Preflight checks the currently deployed API release marker and refuses the
   first schema cutover while old consumers are active; it never infers cutover
   from a desired count of zero. Migration
   archives legacy rows under transactional table locks before dropping fields.
   No application container performs implicit database bootstrap.
1. Deploy using `scripts/deploy_ecs.py deploy --cluster CLUSTER --image IMAGE --service SERVICE --desired-count RECORDED_COUNT`. Start sandbox, then
   landing, the remaining workers (including interactive), beat,
   and finally API. Restore recorded capacity explicitly; the script refuses to
   guess counts for stopped services. Each deployment waits for readiness before
   continuing. Keep maintenance mode until the checks below pass. Later automated
   releases preserve desired counts and follow the same consumer-first ordering.
1. Verify source upload/export, OCR, pipeline preview/publication/cancellation,
   preparation, bounded training, held-out evaluation and inference through the
   intended project-scoped MCP endpoint. Check sandbox runtime identity and interactive consumer health,
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
