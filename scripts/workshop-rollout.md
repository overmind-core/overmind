# Workshop capacity rollout

Imports and workshop turns have separate capacity. Interactive work can fill all
process slots while CPU and memory remain low. Generate the interactive plan with
`python scripts/plan_workshop_capacity.py --cluster CLUSTER --min-capacity 1 --max-capacity 6 --alarm-topic-arn TOPIC --output PLAN`.

Apply the reviewed target, backlog policy, age policy, and alarms using the same
procedure as the landing plan. Add the age policy ARN to the queue-age alarm's
actions. The control task's `overmind-queue-metrics` policy must include
`ecs:DescribeServices` for the environment's interactive service; the landing
planner generates that exact resource scope. Verify fresh interactive metrics
before enabling the scaling policies. The backlog target is the worker's actual
process count from `docker/worker-topology.json`.

Keep dynamic scale-in suspended even when older CPU or memory policies exist.
Long turns exceed ECS's 120-second container stop grace. Scale-out adds consumers;
scale-in and deployments require draining the workers being removed.

## First upgrade to execution clocks

The additive migration cannot infer task identity from old chat. Do not run the
ordinary consumer-first release for this one-time transition.

1. Hold the automatic deployment workflow while publishing the reviewed image.
   Record its prior enabled state. After merge, restore that state and dispatch
   **Deploy API** with `build_only=true` to build without deploying services.
1. Apply migration 0015. Keep the existing interactive consumers and control
   reaper running. Upgrade API and landing producers to the new image; verify
   that all old API and landing tasks have stopped. The existing consumer accepts
   the unchanged task arguments while new producers persist identity and clocks.
1. Run `python manage.py check_workshop_transition --require-idle`. Leave the
   consumers running until all existing work completes. Old interactive workers
   can themselves publish proposal continuations, so a nonblank identity alone
   is insufficient during this mixed-version window. This command only reads;
   never purge, recreate, or invent an owner for an existing task.
1. Protect the existing interactive ECS tasks from termination. Cancel only their
   `interactive` consumer through Celery remote control, preserving active work.
   Verify no active/reserved work was accepted during cancellation. If any was,
   restore those consumers and repeat the idle barrier after it completes.
   Run `check_workshop_transition --require-idle` again after cancellation. If
   either check sees work, restore the old consumers and repeat the barrier.
   This also drains continuations an old worker published while finishing.
   Only then can subsequently queued jobs come exclusively from the upgraded
   producers with their persisted task identities.
1. Start healthy new interactive consumers on the reviewed image, release
   protection only on the old idle tasks, and verify their retirement. Upgrade
   control, beat, and the remaining services. Do not reduce desired capacity
   while workers own jobs.
1. Verify a real upload and follow-up through completion, separate queued/start
   times, fresh queue metrics, bounded capacity, and all alarms. Restore the
   deployment workflow's recorded enabled state before completing the rollout.

Later deployments must still preserve busy workers, but every producer already
stamps ownership and does not require this initial transition. Rollback to the
previous image leaves the additive fields intact; repeat this transition before
returning to strict consumers.
