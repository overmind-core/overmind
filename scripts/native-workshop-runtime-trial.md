# Titanic native Workshop trial review

## Timeout and response recovery remediation

The failed reference trial exceeded Temporal's 180-second reasoning activity
limit. The earlier HTTP timeout measured inactivity, so it did not guarantee that
a response body would finish before that deadline. No response ID or body was
retained for the interrupted call; its provider outcome cannot be reconstructed.
A previous trial also lost its reply when Pydantic AI rejected the output before
`Agent.run` returned.

Reasoning now makes one Pydantic AI model request per durable activity, saves the
raw response and usage before parsing, and feeds malformed replies back for
correction without executing their proposals. An absolute 150-second deadline
records a specific failure before Temporal's deadline. A saved response can be
replayed without another provider request; resume retains accounted responses as
feedback and explicitly marks their proposals unexecuted. Unknown responses or
usage still block automatic recovery. Task-wide token budgets remain absent.

Verification: 34 focused tests passed, including body stalls, malformed replies,
checkpoint interruption, idempotent accounting and resume. The Data Workshop
journey passed again after the final recovery changes in 59.67 seconds. Repository hooks passed. A worker startup check
also caught and corrected an import cycle; its focused resume test passed.

Live verification task `6648786a-afdb-4daa-b64a-efd71b2fbc5f` uses fresh dataset
`5a8df227-9982-4ef6-96c4-d9b27e97185b`, source cell
`637dcfac-dcd7-4642-8de0-679cbf9c4ab1`, the unchanged source bytes and the same
minimal objective. Its first four model responses used the prior worker process:
the dedicated Temporal worker does not hot-reload. The task was paused until the
in-flight call settled, the worker restarted, and the task resumed. Subsequent
live responses contain the new raw response checkpoints. This operational
interruption must be accounted for in any timing comparison.

The resumed trial uncovered two additional failures. Action 31's receipt reader
performed a synchronous ORM query inside an async activity; the query now runs
through the thread-sensitive database boundary, missing receipts return tool
feedback, and resume can safely resolve an interrupted read. The regression test
covers pagination and recovery. After resumption, action 32 received an explicit
HTTP 429 rejection, retained with unknown usage. Rate-limit rejections now wait
until their saved `Retry-After` deadline (60 seconds when absent) before retrying;
other ambiguous failures remain blocked. This follows the provider's documented
[error and retry contract](https://openrouter.ai/docs/api_reference/errors-and-debugging).
A later path-contract fix makes workspace writes and exports return relative
paths that subsequent tools accept directly; previously they returned absolute
paths that the next tool rejected. The same task was resumed after each fix; it is a recovery exercise, not an
uninterrupted benchmark.

### Latest recovery checkpoint

The task remains running after action 109. The platform still contains only its
891-row source cell; no prepared training handoff has been published. There are
52 known model responses, 1,237,825 recorded tokens and $0.10540116 in reported
provider charges. Five HTTP 429 rejections have no usage measurement, so these
are partial figures, not a complete cost total; workspace costs are excluded.
The temporary live path probe successfully wrote a file, read it using the
returned relative path, and removed it. The path-only change passed lint and
repository hooks; no additional suite was run for that small change.

The trial continues under the authorised minimal objective. Qwen3-Coder-Next has
not been promoted to the central default. The boolean-conversion defect is in
agent-authored output and was found but not manually changed during this trial.

### Independent check of the generated data

At action 69, the agent successfully wrote 891 decision rows in Daytona. The
actual `output.jsonl` was downloaded read-only and checked against the exact
fresh source export. SHA-256:
`b5ffa7f1cbf11b777691ca6c30619ef2562aa8fa652700cff7f6d990f1e144a5`.

- All 891 rows pass the shared `decision_line` consumer parser.
- All 891 distinct source-row identities are present and all targets match the
  original `survived` labels, with options `[died, survived]`.
- Neither `survived` nor its `alive` proxy appears in `decision.state`.
- `bool(row["adult_male"])` and `bool(row["alone"])` turn every nonempty string
  into `True`. This corrupts 354 values in each feature, 708 feature values in
  total. Schema validation does not detect this semantic error.
- Missing age is represented as null; empty deck values become null. Empty
  embarkation values remain empty strings. No learned preprocessing was fitted.

This is actual workspace output, not a published training handoff. At this check,
there was no new split or published prepared cell. The agent had inspected a
historical partition from another Titanic source, which is not a split for this
trial. It also tried reading Parquet as text, repeated exploration and made
several path/packaging mistakes. No operator edited the generated transformation
or supplied dataset-specific correction instructions. The output is not ready
for training because of the feature corruption and incomplete handoff.

## Fresh trial with platform references

Task `02c37295-4d77-4845-821b-b467387fd1a3` ran on 6 November 2026,
12:25:48–12:30:16 America/New_York, with exactly the same objective:
“I want to train a decision model on this data”. The fresh dataset is
`7f0d123f-3ae1-4cf7-87c5-dafc94d570bc`, source cell
`7b5d2147-0fb4-41b5-9cdc-babdf997e172`. The source bytes and 891-row fingerprint
match the earlier trial. No capability or target mapping was supplied. The worker
used Qwen3-Coder-Next and reference hash
`f86f5747315b31c745b024d5ebb4cda8fd7140868b9a54a498fa486e579d88be`.
No token or action-count limit was set, and no operator changed its prompt or code
during execution.

**Outcome: blocked before transformation; not ready for decision training.**
Temporal event 193 records an activity StartToClose timeout at 17:30:16.841 UTC.
The `decide` activity has a three-minute per-call timeout and no automatic retry.
Action 20 has an unknown outcome; no later tool action executed. The earlier
trial was cancelled before this trial to release its workspace; this trial was subsequently cancelled with its receipts intact to release
the workspace for remediation verification.

| Observed work                                  | Result                                                            |
| ---------------------------------------------- | ----------------------------------------------------------------- |
| Source inspection and full-source profile      | Completed                                                         |
| Label counts                                   | 549 non-survivors, 342 survivors                                  |
| Missing age                                    | 177 rows                                                          |
| Missing deck                                   | 688 empty strings, discovered after a null-only query missed them |
| Target hypothesis                              | `survived`; `alive` identified as leakage                         |
| Training assessment and working notes          | Recorded                                                          |
| Executed transformations, packages, partitions | None                                                              |
| Published prepared output                      | None; only the original 891-row source cell exists                |
| Decision consumer validation                   | Not reached                                                       |

Ten recorded model responses consumed 186,381 tokens with $0.016362 in reported
provider charges. The eleventh call has no usable receipt, so complete usage and
cost are unknown. No completed tool call failed. The run did not reach Daytona
bootstrap or code execution, so it does not measure the new workspace materials'
effect on transformation success.

The assessment still needs scrutiny: it conflates missing values with
prediction-time availability, and its claim that no grouping is needed is not
established by the inspected columns. Its next checks mention training a decision
tree; no retained recipe yet establishes a handoff to the platform's typed decision
consumer. These are plan-level observations, not defects in published data, since
no such data exist.

This trial cannot establish an improvement in output quality or qualify the model
as the default. The immediate blocker is reasoning-call timeout/recovery. A further
trial needs reliable retained model outcomes before format compliance, target
preservation, leakage, split integrity and replay can be assessed on real output.

## Earlier uncapped baseline

Reviewed 6 November 2026. Task `adc159f5-e3df-44f5-99e6-7a7673b32c60` used
Qwen3-Coder-Next through OpenRouter, the qualified persistent Daytona runtime,
and only the objective “I want to train a decision model on this data”. It had
no task-wide token or action-count limit.

## Outcome

The task is blocked after an unknown model response at action 90. No prepared
training output was published. The task ran from 16:57:31 to 17:07:49 UTC before
blocking. There are 45 recorded model responses, 884,260 recorded tokens and
$0.0801488 in reported provider charges. The last request has no usable response
receipt, so total usage and cost are unknown; these figures cover recorded calls
only and exclude workspace costs. Four tool-input/package errors and two command
failures occurred before the interruption.

This does not qualify the model as the default. The interruption and the failed
preparation are separate findings; removing the token budget did not resolve the
format errors observed before interruption.

## Actual preparation

Source dataset: `b75af049-c2d6-4a0d-bcec-f833d20d5978`, cell
`e2392daf-5b07-40c2-b41f-1af7314d57b3`. Original CSV SHA-256:
`81787d320d7f7b03df935e91de8bd19e11d45c5bbcab86ef4d4a76dc91b7d4f2`.

The agent explored 891 rows, selected `survived` as a binary outcome, identified
`alive` as leakage, and created a seeded partition before authoring the transform.
Partition `537c9907-b5a7-4917-a66c-4858b46bd47b` retained all observations:

| Measurement         | Train | Development |
| ------------------- | ----: | ----------: |
| Rows                |   713 |         178 |
| Label 0             |   444 |         105 |
| Label 1             |   269 |          73 |
| Missing age         |   146 |          31 |
| Missing embarkation |     2 |           0 |

Independent inspection of both exported partition cells found no identical full
source records across the split. The platform also reports zero content/declared
group overlap. No declared entity groups or stratification were requested;
near-duplicate and family-level independence remain unverified.

The retained transform copies twelve passenger attributes into `features`, drops
`deck` and the `alive` proxy, and copies the original label into
`target={kind: choice, options: [did_not_survive, survived], label: ...}`. It
preserves `source_row`. A later script converts the two string boolean features,
but there is no imputation, learned preprocessing or fitted parameter artifact.
Missing data remain missing. Lack of numerical scaling or one-hot encoding is
not inherently a defect for a text-encoded decision model.

## Training suitability

- **Wrong consumer format:** the draft lacks `decision.state`, `question` and
  `target_probabilities`. Its `features`/`target` output cannot be consumed by
  Overmind's decision trainer. The manifest omits `consumer=decision_train`.
- **Input mismatch:** the publication run failed with “Row 0 column alone does
  not match boolean.” Both boolean fields are strings in the landed source.
  Converting them inside the script cannot fix validation of its input schema;
  the later package still declares boolean inputs.
- **Incomplete verification:** static validation passed, but the agent skipped
  an executed preview and attempted publication. No decision-ready development
  output or full-row consumer validation exists.
- **Reasoning inconsistency:** the assessment identifies `alive` as leakage but
  also calls it available at prediction time. The actual script excludes it,
  which is correct, but the assessment was not reconciled. Prediction-time
  availability of all other features is asserted without establishing a scenario.

The exploration and split are useful intermediate work. There is no trainable
output, clean successful replay or measured model quality to endorse.

## Harness changes after this trial

The native agent now retains platform responsibilities, consumer formats and
synthetic schema examples outside its rolling observation history. It can read
upstream capability/trace evidence and downstream model/dependency context through
project-scoped read-only tools. Detailed platform, taxonomy, publication and
research sections are available without a model or workspace call; bootstrap also
writes them into Daytona. Publication material comes from the shared platform
contract rather than a second executable specification.

The user-provided [MoE handbook](https://huggingface.co/spaces/huggingface/moe-blogpost#s-1)
informs consumer-reader checks, artifact manifests and controlled comparisons.
Its model-specific pretraining and routing instructions do not define decision
targets. These changes passed 26 focused tests, the Data Workshop journey and
repository hooks. Subsequent trial findings are recorded above; they do not establish end-to-end training readiness.
