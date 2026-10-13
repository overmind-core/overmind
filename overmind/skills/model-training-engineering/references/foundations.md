# Practice foundations

Reviewed 2026-10-13. These are primary engineering guides and framework references,
not a single formal standard. Framework behavior must match the installed version.
Use the core skill for the workflow; read relevant sources when choosing or changing
a method. Do not force every project to use the same model, split, metric or recipe.

## How to apply guidance

- **Validity safeguards:** protect the held-out claim, prevent target leakage, retain
  source/configuration identity, and report measured coverage honestly. A known
  defect invalidating the claim blocks that claim until repaired.
- **Conditional recommendations:** group/time splitting, cross-validation, calibration,
  pilot runs, augmentation, adapters, early stopping and repeated seeds depend on
  the task, available data, compute and intended use. Explain the choice.
- **Execution reliability:** stable launch identities and provider recovery prevent
  duplicate work and preserve evidence. Overmind-specific
  mechanisms belong in overmind.md; historical examples belong in lessons.md.
- **Unmeasured findings:** mark unknown, deferred or not applicable with reasons.
  Do not turn incomplete semantic evidence into a technical pass or invent a score.

## Data and experimental design

1. [Hugging Face dataset cards](https://huggingface.co/docs/hub/datasets-cards)
   supports documenting data contents, intended context, bias and license metadata.
   Application: retain a concise source/target/population note alongside versions;
   the existence of a card does not verify its claims.
1. [scikit-learn common pitfalls](https://scikit-learn.org/stable/common_pitfalls.html)
   explains inconsistent preprocessing and leakage.
   Application: separate fitted preprocessing from deterministic parsing, fit on
   training only, and apply the same fitted transformation to held-out inputs.
1. [scikit-learn cross-validation](https://scikit-learn.org/stable/modules/cross_validation.html)
   covers held-out evaluation, grouped observations and time-ordered validation.
   Application: select the independent unit and split for the generalization question;
   random folds are not interchangeable with unseen-entity or future-time tests.
1. [scikit-learn metrics](https://scikit-learn.org/stable/modules/model_evaluation.html)
   documents task-specific measures and averaging.
   Application: define the primary outcome and diagnostic metrics before selection;
   record class order, support and conventions rather than comparing unlike scores.
1. [Probability calibration](https://scikit-learn.org/stable/modules/calibration.html)
   distinguishes probability reliability from aggregate scoring losses.
   Application: use disjoint or cross-fitted calibration evidence; report raw and
   calibrated outputs, and do not equate lower Brier/log loss with better calibration alone.
1. [Decision-threshold tuning](https://scikit-learn.org/stable/modules/classification_threshold.html)
   separates probability estimation from the action taken.
   Application: choose an operating rule from the task's error costs/constraints
   using development/calibration data, not the final test.

## Model and training configuration

7. [Google Rules of ML](https://developers.google.com/machine-learning/guides/rules-of-ml),
   particularly rules 2, 4, 5 and 37, motivates early metrics, a simple baseline,
   infrastructure tests and training-serving consistency.
   Application: prove data and artifact paths separately from claims of useful quality.
1. [Deep Learning Tuning Playbook](https://github.com/google-research/tuning_playbook),
   by Google Research/Harvard authors, guides initial architectures/optimizers,
   incremental experiments, batch/learning-rate interactions, evaluation and tracking.
   Application: use a justified starting recipe, plan development experiments,
   retain their configurations, and distinguish throughput from model quality.
   Its authors explicitly describe practitioner guidance, not an optimal universal recipe.

## Runtime and reproducibility

9. [PyTorch reproducibility](https://docs.pytorch.org/docs/main/notes/randomness.html)
   explains nondeterminism and cross-platform/version limitations.
   Application: record seeds and runtime/hardware, deliberately choose deterministic
   settings when needed, and disclose their limits and performance tradeoffs.
1. [Hugging Face TRL SFT Trainer](https://huggingface.co/docs/trl/sft_trainer)
   documents loss scope, chat-template requirements, packing and sequence length.
   Application: inspect the actual prepared tensors/decoded examples and supervision;
   choose task-appropriate masks rather than blindly copying chat defaults.
1. [PyTorch saving/loading](https://docs.pytorch.org/tutorials/beginner/saving_loading_models.html)
   distinguishes inference weights from training-resume state and evaluation mode.
   Application: verify a reloaded artifact separately from restart/resume behavior;
   retain the state the selected trainer actually needs.

## Pre-launch evidence

Keep this concise in the experiment manifest or report; link artifacts rather than
copying all raw data. This checklist is an engineering synthesis, not a certification.

| Stage                    | Evidence to retain before substantial training                                                                                           |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Task and source          | Prediction unit, target interpretation, inference-time features, source/version, privacy/use constraints and unresolved assumptions.     |
| Transformation           | Versioned code, schema/target tests, counts/exclusions, representative examples and training-only fitted preprocessing.                  |
| Evaluation design        | Split/fold membership, independence rationale, overlap checks, coverage, primary metric and locked final-test policy.                    |
| Baselines and sanity     | Attributable train/development baseline; appropriate fitting/input checks, with static versus measured scope explicit.                   |
| Model and recipe         | Exact model/tokenizer, objective, method/trainable parameters, effective batch, optimizer/schedule, context, precision, steps and seeds. |
| Runtime and recovery     | Supported hardware/runtime, length/memory evidence, artifact location, checkpoint/resume policy, logs and failure signals.               |
| Observation and dispatch | Validation cadence/selection, failure policy, one launch identity and frozen comparison plan.                                            |

Stop dispatch for known broken supervision, unauthorized data, incompatible
execution, unresolved duplicate submission or an invalid required evaluation design.
For optional checks or genuinely unknown model quality, state the limitation and
let the authorized experimental scope determine whether to proceed. A low baseline
is evidence to interpret, not automatically a prohibition on research.

## Scope limits

The playbook primarily covers supervised learning and fine-tuning. RL reward design,
multimodal safety and regulated production deployment need additional specialist
guidance when relevant. Offline training success is not production certification.
