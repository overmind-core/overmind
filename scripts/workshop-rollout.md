# Workshop capacity rollout

Imports and workshop script runs have separate capacity. Interactive work can fill all
process slots while CPU and memory remain low, so the interactive service scales
on queue backlog per worker and on queue age. Terraform in `cloud-platform` owns
those bounds, policies and alarms.

Keep dynamic scale-in suspended even when older CPU or memory policies exist.
A run can last up to 10 minutes, past ECS's 120-second container stop grace. Scale-out adds consumers;
scale-in and deployments require draining the workers being removed.
