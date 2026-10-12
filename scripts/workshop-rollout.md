# Workshop capacity rollout

Imports and workshop script runs have separate capacity. Interactive work can fill all
process slots while CPU and memory remain low. Generate the interactive plan with
`python scripts/plan_workshop_capacity.py --cluster CLUSTER --min-capacity 1 --max-capacity 6 --alarm-topic-arn TOPIC --output PLAN`.

Six workers is the default reviewed rollout bound, not a product limit. Larger
bounds are configurable. Validate regional vCPU and subnet capacity, database
connections, broker memory, provider concurrency, and deployment surge capacity
before applying them. Four process slots per worker means that 250 simultaneous
workshop runs require at least 63 workers; that arithmetic is not a load-test
result or a guarantee of provider throughput.

Apply the reviewed target, backlog policy, age policy, and alarms using the same
procedure as the landing plan. Add the age policy ARN to the queue-age alarm's
actions. The control task's `overmind-queue-metrics` policy must include
`ecs:DescribeServices` for the environment's interactive service; the landing
planner generates that exact resource scope. Verify fresh interactive metrics
before enabling the scaling policies. The backlog target is the worker's actual
process count from `docker/worker-topology.json`.

Keep dynamic scale-in suspended even when older CPU or memory policies exist.
A run can last up to 10 minutes, past ECS's 120-second container stop grace. Scale-out adds consumers;
scale-in and deployments require draining the workers being removed.
