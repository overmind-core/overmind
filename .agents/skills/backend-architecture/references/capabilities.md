# Capabilities and sync

The agent is the project itself: one graph per project, with no table. `Capability` rows are its nodes (UI "Capability"; the sidebar's "Agent" is the product).

## Identity

- Ingest never creates a capability. `services/capabilities/identity.lookup` resolves IDs, names and slugs through `IdentityAlias` (renames included). An unknown identity leaves the span unbound, and `tasks/capability_rebind.py` re-binds the backlog after scans and hand-made rows.
- Wire identity is `overmind.capability.id` alone. `overmind.agent.*` attributes are not read, and `overmind.capability.name` is a display label that never resolves. The `overmind/<uuid>` model alias never changes.
- A scan never deletes. An absent capability becomes `status=leftover` and reactivates in place when the code returns; `observed` rows (from telemetry or made by hand) are exempt.
- `DELETE /api/capabilities/{id}/` is a soft delete (`status=deleted`): the row and its data stay but are invisible to lists, scans and identity lookup, so a later scan mints a fresh row. There is no merge, split, retire or history.

## Discovery and sync

Discovery runs locally. `/overmind setup` runs `overmind chassis` (AST inventory and call graph for Python and TypeScript), writes `overmind_capabilities.json`, and conversion fills prompt spans, drops fabricated anchors and unverifiable provenance, and stamps `trajectory_map[].verified` against the chassis. `overmind sync` uploads the toml.

Tool branches and steps may carry `transitions: [{target, condition}]`. A target resolves to an exact step anchor on the owning path or `terminal`; missing or ambiguous destinations stay unresolved.

Toml sync is `POST`/`GET /api/v1/sync` (API-key auth). It maps onto `Capability` (`current`/`leftover`), stores the card, prompt and matrix in `improvement_metadata`, keeps `repo_summary`, `trace_provider` and the toml `version` in `Project.settings`, and writes leftovers back with `archived = true`. Then `innermind.default_set.upsert` runs per current capability and records the result in `improvement_metadata.eval_preload`:

- seeded checks for the capability's span types (created only when missing);
- one deterministic row per toml `eval_matrix` entry that names a catalog `check` with `params` (an unknown check or constant is a 400);
- one llm row per legacy toml expectation;
- one span-scoped llm row per `expectations=` sentence on the checkout's entry-point, workflow and function decorators. `overmind sync` reads them; setup never writes judge criteria to the toml. Setup applies these decorators to every function, tool, retrieval step and model call the card names, then syncs again. A sentence the code drops loses its row on the next sync.

The Default eval set is activated when none is active. The upsert is synchronous and calls no model.

## Repository provenance

`overmind chassis` saves `.overmind/scan.json` before inspection. Conversion verifies branch, commit and a SHA-256 fingerprint of tracked and unignored files, then writes `repository_snapshot` to the toml; a changed checkout needs a rescan. Sync never recaptures Git state: it stores the snapshot in `Project.settings` and stamps `last_synced_at` separately. A push without provenance clears the old label, and missing provenance stays unknown. The Agent header and `overmind://project/current` show both.
