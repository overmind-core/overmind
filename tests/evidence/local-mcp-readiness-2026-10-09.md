# Local MCP readiness — 2026-10-09

## Scope

Read-only verification after Workshop presentation changes. Those changes are
frontend-only: they do not change dataset operations, contracts or the catalogue.
No new keys, configuration changes, container restarts, dataset mutations or paid
jobs were needed. No browser fallback was used.

## Results

- Saved global MCP endpoint: `http://localhost:8000/api/mcp/`, enabled with no
  tool allowlist/denylist. The repository has no competing Overmind MCP entry.
- MCP and transfer use the same account credential. The transfer profile has
  mode `0600`; credential values were not printed. No injected environment key.
- A repository-free invocation of the installed CLI initially returned
  `network_permission_denied` under the shell sandbox. The scoped host-approved
  invocation passed both MCP and transfer readiness, with upload/export allowed.
  This does not certify network access in other sandboxes or future sessions.
- Contract 5.2.0; 67 tools. Running-server and current-checkout catalogue hashes
  both equal `f9a5923b7fa4164250eaee0180e6c6752c4770a986977886771c419baef7de83`.
- Fresh MCP SDK transport verified initialization, tools/list, interface resource,
  and list_projects. Ten Workshop lifecycle tools are present; retired platform
  agent tools are absent.
- Current transformation prompt is listed and renders successfully.
  `inspect_dataset_workbench` supports exact pipeline-revision lookup.
- Project discovery includes financial-services and eight other authorized
  projects. Global configuration is not pinned to one project.
- MCP list_datasets and inspect_dataset_workbench succeeded for existing fixture
  `canvas-verification-synthetic.jsonl`, dataset
  `9b028cb6-a7d2-40e4-b22f-410954744e26`, project
  `e18b29b5-915d-45a7-80cd-77ffe6559205`.
- Installed CLI connection, transfer and dataset modules byte-match this checkout.
- Local API is healthy; Workshop runner is running. Both mount this checkout.

## Repeat

From a repository-free directory, using the saved connection:

```sh
overmind connection check --project-id e18b29b5-915d-45a7-80cd-77ffe6559205 --json
```

From the repository with its existing dependencies:

```sh
UV_CACHE_DIR=/tmp/overmind-uv-cache uv run --no-sync python tests/evidence/workshop_connection_replay.py
```

For the domain read check, call `list_projects`, select financial-services, then
`list_datasets` with search `canvas-verification-synthetic.jsonl`, and
`inspect_dataset_workbench` with the returned dataset UUID. Pass project_id on
both project-scoped calls. Retrieve `author-dataset-transformation` with that
dataset and an inspect-only task. Do not save or execute a new recipe.

## Client handoff still required

This existing chat has no loaded Overmind MCP tools/resources. The saved global
configuration and fresh protocol session work, but that is not proof of this
chat's native tool readiness. Reconnect/restart the MCP client and confirm
Overmind appears before testing. No tool exposed in this chat can reload its
native MCP connections.

The Overmind connection guidance required checking file transfer independently
of MCP. Official OpenAI documentation was consulted for the client reconnect
handoff: https://learn.chatgpt.com/docs/extend/mcp?surface=cli.
