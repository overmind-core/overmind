# Retired independent Slack adapter

These `.example` files preserve the earlier implementation for review; they are
not importable Python modules, registered routes or active Celery tasks. Do not
install or restore them alongside the shared runtime.

The replacement runs as a separate Overwatch Slack profile in the Overlord
repository. Canonical read functions moved to `overbae/services/overwatch_tools.py`;
`overbae/api/overwatch_agent.py` exposes only a dedicated read contract. The
independent OpenRouter model loop, Slack ingress and reply worker are retired.
