# Background generation progress verification

Verified 2026-10-06 against the existing Docker Compose development stack.

## Environment and input

- API at `http://localhost:8000`; Console at `http://localhost:5173`.
- Browser authenticated for the `financial-services` project.
- Dataset `8abda57a-a667-4ebc-988a-379d953fc9de`, project `e18b29b5-915d-45a7-80cd-77ffe6559205`.
- Existing generation run `f6cc6f4c-59b1-441d-ad88-5eee44ea9f3f`, requesting 450 examples.
- The active source was version 1.3, 496 passages. No worker restart, cancellation, provider resubmission or dataset mutation was used for this verification.

## Reproduce in the browser

Open `/datasets/8abda57a-a667-4ebc-988a-379d953fc9de?projectId=e18b29b5-915d-45a7-80cd-77ffe6559205` while generation is running. With the authenticated CUA tab bound as `lagTab`:

```javascript
await lagTab.reload();
nodeRepl.write(await lagTab.playwright.getByLabel("Workshop activity", {exact: true}).innerText());
nodeRepl.write(await lagTab.playwright.evaluate(() => ({
  liveThinking: Array.from(document.querySelectorAll("button"))
    .map(element => element.getAttribute("aria-label"))
    .filter(label => label?.startsWith("Thinking")),
  tail: document.body.innerText.slice(-600),
})));
```

Observe again after the next provider batch completes. A later completed run will no longer exercise the running-state case; use another authorised in-progress generation for that case.

## Observed results

- Before the change, the Console showed `Queued` after the planning response completed, despite saved work and an active generation worker.
- Saved receipts showed batches of eight examples completing sequentially; the operation was progressing rather than stuck in the queue.
- After the change, the notebook displayed `Generating examples` and `64 of 450 rows saved`.
- Reload retained the saved count. The count subsequently advanced to `80 of 450 rows saved` without a new user request.
- No live `Thinking` disclosure was present during background generation. Historical planning steps remained inspectable.
- Version 1.3 remained active with 496 passages while generated examples accumulated separately. The existing worker schedules the audit only after `published_cell` is present.
- The quiet notice now refers to rows saved, rather than incorrectly asserting that the provider has no activity.

## Checks

Run from `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
bun run test src/components/datasets/notebook/chat.test.tsx
```

The typecheck, lint, design, contrast and control checks passed. The existing chat test file passed all 21 tests. The live browser checks above cover the asynchronous handoff and reload regression; no new unit tests were added.

MCP impact: frontend-only presentation correction. REST and MCP already expose the saved generation workflow; no server contract or generated client changed.
