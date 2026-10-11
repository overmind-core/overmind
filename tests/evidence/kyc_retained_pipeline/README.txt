KYC supplied-text to risk-bucket supervised conversations

Authored by the native coding agent. Overmind executes these retained scripts.
This package re-expresses recorded declarative revision
033236f1-4301-4772-8397-ef756144cac5 as three executable Python stages.

1. validate.py checks text, supplied targets and row identities, retaining values.
2. project.py selects tokens as question and kyc_risk_bucket as answer. It
   excludes every other data column from model input, preserving provenance.
3. messages.py constructs one user turn and one supplied assistant target.

Every script takes input.jsonl, output.jsonl and parameters.json as argv.
No parameters, network, models or external dependencies are needed.
The runtime uses memory-backed scratch. A 489 MB source plus unchanged validation
output needs more than 512 MB; this revision declares a 2 GiB memory budget.
All rows, source order, duplicate observations, Unicode and whitespace survive.
Missing/blank/non-string input or target fields fail without inventing repairs.
Unknown nonempty string labels are preserved rather than assigned new meanings.
Original full rows remain in the source and validation cells.

The source is already the train member of a recorded thread-preserving partition.
This package does not resplit data, change the held-out dataset, or create labels.
Reuse requires compatible field meanings as well as compatible column types.

Scope: reproduce existing input/target semantics, not validate their correctness.
Risk-bucket prediction from tokens alone does not establish coverage of the linked
KYC Screener's entity extraction, firm rules, screening or escalation workflow.
The prior declarative run remains historical and was not executed from this code.
Training, evaluation, activation and hosted services are not invoked.
