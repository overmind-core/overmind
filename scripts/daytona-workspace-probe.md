# Persistent Daytona workspace probe

Standalone native-intelligence infrastructure probe. It does not connect to Overmind, migrate application tables, start a reasoning agent, or invoke Modal. MCP impact: CLI-guided infrastructure qualification; no new platform capability or public contract yet.

The script pins Daytona 0.227.0 using inline dependency metadata. Run with `uv run --script scripts/daytona_workspace_probe.py`. Supply `DAYTONA_API_KEY` through your secret manager or use `--key-file` with a private file outside the repository. The credential is used only with `https://app.daytona.io/api`; it is never uploaded into the workspace. SDK exception bodies are deliberately omitted from output.

Use a persistent local receipt path outside the repository:

```sh
uv run --script scripts/daytona_workspace_probe.py provision --state /private/tmp/daytona-poc/receipt.json --simulate-lost-ack
uv run --script scripts/daytona_workspace_probe.py provision --state /private/tmp/daytona-poc/receipt.json
uv run --script scripts/daytona_workspace_probe.py run --state /private/tmp/daytona-poc/receipt.json --simulate-lost-ack
uv run --script scripts/daytona_workspace_probe.py inspect --state /private/tmp/daytona-poc/receipt.json
uv run --script scripts/daytona_workspace_probe.py restart --state /private/tmp/daytona-poc/receipt.json
uv run --script scripts/daytona_workspace_probe.py inspect --state /private/tmp/daytona-poc/receipt.json
```

`provision` creates or attaches to `overmind-native-poc`, checking its ownership label. It reserves 1 CPU, 2 GiB memory and 3 GiB disk. Auto-stop and auto-pause are disabled; auto-delete is disabled; no destructive TTL is set. Outbound networking is blocked. The image build installs Polars 1.35.2 on Python 3.12.8 before runtime network isolation. The workspace remains running and billable after the probe finishes. Stop/delete is an explicit operator action in Daytona; `restart` deliberately stops then starts only this workspace.

`run` uploads synthetic CSV and transformation source, verifies the input transfer, and submits one asynchronous command in a unique session. A retained command intent prevents accidental re-execution. `inspect` recovers the session command ID when the response was discarded, verifies the executor exit code, captures logs and checks the downloaded artifact. Later reads reuse the retained terminal receipt: Daytona session history can disappear after restart, while files persist.

The local receipt is the recovery authority for this probe. Retain it with restricted access and back it up for continued use. File locking protects one receipt on one host, not distributed writers. Do not use different receipt paths to execute concurrently against this workspace. This is intentionally a single-operator probe, not the Phase 2 action ledger.

A lost creation acknowledgement can be reconciled by stable name. If a prior creation intent exists but no matching workspace is discoverable, the probe refuses another creation. A lost command acknowledgement can be reconciled if the unique session contains exactly one matching command. Missing or ambiguous command history is uncertain; never rerun merely because no output is visible. Fault simulation discards a successful response locally; it does not reproduce every server/network failure.

The base image is version-tagged, not digest-pinned, and the actual provider snapshot field was null for this image build. Immutable image identity and independently restorable workspace checkpoints remain prerequisites for the full runtime. No long-duration availability, multi-tenant isolation, process-memory recovery, or production exactly-once claim is made here.
