# Workshop capacity rollout

Initial source imports run on the dedicated landing worker. Immutable Workshop
pipelines run on batch alongside admitted evaluation generation. Queue metrics
count retained import, pipeline and evaluation receipts; no chat-agent work is
scheduled on interactive.

Generate a review-only Workshop batch plan with
`python scripts/plan_workshop_capacity.py --cluster CLUSTER --min-capacity 1 --max-capacity 6 --alarm-topic-arn TOPIC --output PLAN`.
The backlog target comes from batch concurrency in `docker/worker-topology.json`.
Review database connections, provider limits, regional compute and deployment
surge capacity before applying the generated plan. It does not change AWS.

Keep automatic scale-in disabled. Drain active work before removing consumers;
queue metrics are demand measurements, not proof that a worker can terminate.
`check_workshop_transition --require-idle` reads queued/running pipeline receipts
and refuses an active or unbound workload.

Before upgrading an installation that still has the autonomous Workshop runtime,
drain and stop its workers before the API cutover. Preserve unresolved provider
receipts; cancellation does not establish remote termination. Migration 0034
joins both applied histories without renumbering them and archives retired
ownership and handoff fields in DatasetHistory.

Run all migrations, verify a healthy landing-only consumer, then update the
remaining workers, beat and API. Validate a retained source upload, explicit
pipeline execution and an interrupted-import recovery through completion. Check
fresh queue metrics and alarms before enabling scale-out policies.
