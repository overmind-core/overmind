"""Private vLLM pools for dedicated deployments.

Deploy alongside the shared inference app:

  modal deploy overbae/modal/modal_dedicated_worker.py --env overmind-dev

H100 exists only here. Each deployment binds its own model name, so its
autoscaler cannot pin the shared ``overmind-inference`` pools.
"""

# Modal deploy resolves the module attribute `app` and never calls it.
from modal_vllm_worker import dedicated_app as app  # noqa: F401

APP_NAME = "overmind-dedicated"
