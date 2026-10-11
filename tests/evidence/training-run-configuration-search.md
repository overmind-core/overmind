# Run configuration search

Verified on the local Console, 2026-10-10. Requires the running development stack
and an authenticated local Console session. No training or data operations were
performed.

## Browser replay

Open `http://localhost:5173/training?projectId=a279d834-899c-4ac6-888a-157f0c7d9cf8&groupId=9e61cda4-9bdc-4655-af5d-01aea112a876&jobId=535888b9-70ac-43c1-96c0-164fc9f54762`.

1. Confirm the collapsed disclosure reads **Run configuration**, without parameter
   chips. Expand it; **Search run configuration** appears above the table.
1. Enter `LeArNiNg RaTe`. Both the requested learning rate and effective worker
   learning rate show `0.0002`, with their respective section/source headings.
1. Enter `H100`. Only the effective GPU type row and its section/header remain.
1. Enter `Titanic`. Both training and validation dataset links match by their
   displayed names. Matching run name, request key and task description also remain.
1. Enter `zz-no-setting-match`. The table reports **No matching settings**.
1. Click **Clear search**. All 120 table rows, including headers, return.
1. Collapse the disclosure. Search disappears and the header remains chip-free.
   Reopen it and search `learning rate` again.

All steps passed. Screenshot: `/private/tmp/run-configuration-search.jpg`.

## Static checks

From `frontend/`:

```sh
bun run typecheck
bun run lint
bun run check:all
```

All passed. Unit suites were not run for this local presentation change; search
behavior was verified against the real run in the browser.
