# Workshop OpenUI verification

Local trial, 7 November 2026. No training was launched.

## Implemented contract

The resident Daytona agent can compose 15 Workshop components: text, headings,
callouts, charts, tables, metrics, images, cell links, tabs, disclosures, steps,
code, questions, choices and layout. Questions support text, numbers, selections,
multiple selections and booleans. The catalog generates the validation schema
staged beside the data in the resident workspace.

The agent publishes computed local files through `present`. The platform retains
the validated document, data, hashes and exact published source cells. The Console
renders OpenUI inside the existing conversation using Overmind's tokens and
controls. Replies enter the existing native follow-up queue with the original
message, component and validated answer. They cannot directly launch training.

Prose streams; visual documents appear after validation and replay from retained
conversation events. Arbitrary JSX, remote image URLs and client-side data queries
are not part of this contract. Presentations have a 2 MiB payload limit and tables
retain at most 2,000 rows. These are display limits, not agent token budgets.

## Live evidence

- Project: `1e3f3e92-b50d-4590-85ed-97921d132d3c` on `http://localhost:8000`.
- Dataset: `f4abd6eb-32d5-48f8-b768-bc8a2935288f`, Titanic · verified resident harness.
- Source cell: `46f81df3-2a1a-4c44-94d7-b88dada4f9ed`.
- Runtime: `sha256:e5e17d2ff693e83f7fb429193f0d3a737575c486a68a1ba790f7ad6558f06f61`.
- Sandbox: `44933a6d-f84c-455f-ae0e-aa9c18c85136`.

Task `323c067b-246f-4cee-95ab-1bef813d9078` received:

> Show the passenger-class distribution for this dataset as an interactive chart
> with the exact values. Let me choose whether to inspect missing values or review
> target leakage next. Inspect only.

It completed five turns with no tool errors, publishing an actual OpenUI bar
chart, searchable exact-value table and two choices. An independent SQL aggregation
over the source cell agreed with all three values:

| Class  | Passengers | Percentage |
| ------ | ---------: | ---------: |
| First  |        216 |      24.24 |
| Second |        184 |      20.65 |
| Third  |        491 |      55.11 |

The response explicitly identified the full 891-row dataset rather than claiming
training-only scope. The browser rendered the chart, filtered the table to Third
(491, 55.11%), and focused the source cell through its inline link.

Clicking **Inspect missing values** created task
`707fdd84-22f2-419d-9eb6-448e24eaf4bb`. The retained interaction binds
`passenger-class-inspection-next` to the original `presentation:3` message with
`choice=inspect_missing_values`. Reloading the Console retained the answer and
disabled both choices. The follow-up inspected the attached data through native
Python execution rather than a browser or MCP.

The follow-up completed six turns without tool errors and published a second
interactive chart with missingness tables. It reported 177 missing ages, 2 missing
embarkation ports and 688 missing deck values. It distinguished the 179 rows with
missing model inputs from deck, which is retained but excluded from the model
inputs. It made no data changes.
An independent SQL query confirmed all four missingness counts. Both generated
charts rendered in the browser, and the chart resized with the panel from 416 to
560 pixels. The Console remained healthy with zero restarts after recovery.

## Checks and recovery

- 21 focused backend tests passed for presentation, conversation and resident execution.
- 14 MCP contract tests passed.
- 9 Workshop UI tests passed, including the real renderer, saved form drafts,
  source navigation, answer submission, composer preservation and re-answering a
  removed queued reply.
- The Data Workshop journey passed.
- Typecheck, lint, catalog consistency, design, contrast, controls and pre-commit
  checks passed.
- The production frontend build passed; Vite reported the existing large-chunk
  warning, including the ELK bundle.
- The deployed runtime passed all 14 scripted Daytona qualification checks with
  zero model calls, including runner recovery, clean replay, follow-up recovery
  and cancellation while waiting for a model.

The first qualification attempt hit a Daytona control-request timeout after its
scripted execution had finished. Its events were inspected and the attempt was
retained; a fresh qualification passed. Workspace provisioning resumed from its
checkpoint after a local worker restart. The previous sandbox was retained for
rollback.

The shared 8 GiB Docker VM killed Vite during dependency compilation. OpenUI now
uses component subpath imports, and the Console container uses the same Bun
1.3.13 version as the passing host checks. Compilation completed after temporarily
stopping the confirmed-idle batch worker; the worker was restored and the Console
remained healthy. A cold dependency rebuild still needs sufficient Docker memory.

Source attribution and hashes establish provenance, not semantic correctness of
every future agent-generated analysis. The live counts above were independently
checked. This trial does not establish model-training readiness or performance.
