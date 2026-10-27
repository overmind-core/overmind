"""Private vLLM pools for dedicated deployments.

Deploy alongside the shared inference app:

  modal deploy overbae/modal/modal_dedicated_worker.py --env overmind-dev

H100 exists only here. Each deployment binds its own model name, so its
autoscaler cannot pin the shared ``overmind-inference`` pools.
"""

import modal

from overbae.modal.modal_vllm_worker import register_dedicated_classes

APP_NAME = "overmind-dedicated"
app = modal.App(APP_NAME)
register_dedicated_classes(app)
