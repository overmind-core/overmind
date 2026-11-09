# Iterative native Workshop agent

## What changed

The observable coding-agent workflow is a continuing investigation: inspect a
contract and source, identify an unanswered question, run an experiment, read the
result, change the code or plan, and verify the resulting artifact. Reproducing
this requires conversation state, executable tools and feedback. A persistent
sandbox alone does not supply that behaviour.

The old Workshop loop rebuilt each request from the objective and twelve raw
receipts. It exposed a single structured dispatch output, omitted previous model
messages and mixed large unrelated recipe histories into the context. The revised
loop uses actual deferred function tools, stored assistant messages and paired
tool results. Temporal still owns durable scheduling; the backend still owns
permissions and publication; Daytona runs exploration and code.

| Coding-agent behaviour           | Native implementation                                                                          |
| -------------------------------- | ---------------------------------------------------------------------------------------------- |
| Carry previous work forward      | Reconstruct the conversation from durable model/action receipts                                |
| Revise a plan after observations | Persistent working notes with evidence, assumptions and next checks                            |
| Experiment and debug             | Write/read files and run commands in the persistent project workspace                          |
| Inspect failures                 | Return command exit status, stdout/stderr and tool validation errors to the next turn          |
| Keep context focused             | Bounded complete conversation pairs and result previews; retrieve full old results by sequence |
| Avoid duplicate work             | Reuse identical acknowledged work-starting domain calls within the task                        |
| Bound execution                  | Provider-wire usage accounting, cumulative token budget, action cap and cancellation           |

Working notes contain an evidence summary, not a copy of private model reasoning.
This reproduces observable behaviour; it is not a claim to reproduce Codex's
internal implementation or model capability.

## Titanic verification, 2026-11-06

Fresh local dataset: `53ec86f6-301c-489f-8ae4-f2fa5bbf523b`.
Native task: `91e4fa94-45c9-4f11-af20-84ecf8498eba`.
The source bytes and objective match the earlier [hosted Qwen trial](titanic-qwen-trial.md),
with an explicit 700,000-token allowance for this run. No transformation code or
corrective instructions were supplied during the run.

Observed behaviour:

- Started one profile job and inspected its result; recorded a plan and findings.
- Exported the actual retained CSV to Daytona and explored the columns.
- After a pandas import failed, wrote a Polars exploration script.
- After executing the wrong path failed, corrected the path and executed the script.
- Created one partition request; did not repeat workbench inspections or submit
  duplicate partitions as the previous attempts had done.
- Wrote a transformation, corrected an indentation/variable error in a subsequent
  edit, executed it successfully and wrote a verification script.
- Paused automatically at the token boundary before executing the next proposed
  action. The supervisor subsequently cancelled the qualification task to release
  the project workspace; retained files and receipts remain intact.

Measured: 37 model requests, 36 tool actions, 702,405 reported tokens and
$0.06458968 provider-reported inference cost. The task reached its budget pause
in 8m 29s. All 37 model responses had measured usage. The final request crossed
the allowance by 2,405 tokens; budgets gate actions between requests rather than
providing a hard provider-side spend cap. Costs exclude Daytona and local services.

Independent inspection of the generated Parquet found 891 rows, labels matching
all original rows in source order, no remaining null feature values and no `alive`
column. However, the output lacks `source_row` and typed `decision` supervision.
The script computes age median and embarkation mode over the complete source,
not solely the training partition. It never published a validated pipeline.
The verification script was written but not executed before the budget pause.

**Result:** iterative exploration and error recovery are demonstrated; end-to-end
Titanic decision preparation still fails qualification. Exploration was inefficient:
21 command actions included separate shell counts that one profiling script could
have answered. Better tool use did not establish semantic correctness. The central
model selection is unchanged, and Qwen is not promoted by this result.

Evidence is retained in the local database, Daytona task directory and ignored
artifacts `tests/evidence/titanic-iterative-trial.json` and
`tests/evidence/titanic-iterative-validation.json`. These contain the actual code,
commands, failures, model identities, usage and validation findings.

## Remaining limits

Context trimming preserves recent complete tool pairs and the latest notes, but
notes are model-authored and can omit evidence. Full receipts remain retrievable.
There is no independent semantic reviewer, automatic promotion, hard monetary cap
or guarantee of complete Data Workshop feature parity. Completion still uses the
shared lineage/publication service. The next comparison should hold this scaffold
and acceptance checks fixed while testing a stronger reasoning model and measuring
whether it proceeds efficiently from evidence to a correct published artifact.
