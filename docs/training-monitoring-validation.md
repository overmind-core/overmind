# Training monitoring validation

## Failure contracts recorded before implementation

- Invalid, unknown, contradictory or unsupported monitoring configuration must
  not be silently accepted or clamped.
- Duplicate visits and protected groups must survive probe selection; a frozen
  sample must not change as scheduling adapts or a worker restarts.
- Validation/checkpoint time must not inflate measured optimiser step duration.
- Slow checks must not create a queue of overdue checks. Overhead conflicts must
  remain visible without silently shrinking samples or removing metrics.
- Generated outputs must use input-only prompts; failed generations and invalid
  labels must remain in coverage and cannot be scored as successful examples.
- Check records must survive poll duplication, restart, cancellation and partial
  publication. Failed scorer construction must not remain pending indefinitely.
- Development evidence must not be mistaken for final-test evidence; selection
  and early stopping cannot use unavailable/non-finite metrics.
- Checks/checkpoints must remain project scoped and passive reads must not call
  providers. Cancellation must preserve already completed evidence.
- The old job and its immutable launch record must remain unchanged.

## Local verification

Use the repository uv development/test environment and local test database. Tests
replace provider calls; they are not evidence of a live GPU qualification.

Initial command:

```sh
uv run pytest tests/test_training_monitoring.py -q
```

Live qualification uses the fresh $100 allowance authorised on 2026-10-10,
separate from earlier testing. Native mixed-target training completed three times;
the latest run retains sample weights and reproduces the prior loss values and
all checkpoint probability vectors exactly. Structured-output training succeeded
with development checks and checkpoint verification; its deployment is ready but
was not activated. The first Qwen3.5-4B qualification failed with GPU memory
exhaustion after the initial check. The repaired GPU retest completed eight updates,
all 44,800 final development rows and four reload-verified checkpoints. The provider
reports success; artifact registration is separate. Loss fell substantially but
the small generated-output probe remains poor, demonstrating the importance of
coverage and task metrics alongside loss. The full original-data relaunch is now
submitted as `d41d3526-d703-499b-aaee-663c2da0908d`, with adaptive monitoring and a
two-hour provider limit. The original cancelled job is unchanged.

Exact recipes, commands, observed metrics, failures and qualification limits are
recorded in `tests/evidence/training-monitoring-implementation-2026-10-10.md`.
CPU qualification does not substitute for model-family GPU verification.
