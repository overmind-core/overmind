import { useCallback, useEffect, useRef, useState } from "react";

import { CreditsRequiredAlert } from "@/components/billing/credits-required";
import { ModelsPanel } from "@/components/finetuning/train/models-panel";
import { SetupPanel } from "@/components/finetuning/train/setup-panel";
import { TrainingStartButton } from "@/components/finetuning/train/start-button";
import { useTrainWizard } from "@/components/finetuning/train/use-train-wizard";
import { Alert } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CreditsAmount } from "@/components/ui/credits";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { Label } from "@/components/ui/label";
import { TooltipProvider } from "@/components/ui/tooltip";

interface CloseGuard {
  dirty: boolean;
  launching: boolean;
}

export interface TrainWizardProps {
  open: boolean;
  projectId: string;
  groupId: string;
  initialCapabilityId?: string;
  initialDatasetId?: string;
  initialEvalDatasetId?: string;
  onLaunched: (groupId: string) => void;
  onCancel: () => void;
}

// Radix mounts `TrainWizardForm` only while the dialog is open, so every reopen
// starts clean and none of its queries run from the jobs list.
export function TrainWizard({ open, onCancel, ...props }: TrainWizardProps) {
  const guard = useRef<CloseGuard>({ dirty: false, launching: false });
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const onGuardChange = useCallback((next: CloseGuard) => {
    guard.current = next;
  }, []);

  const requestClose = () => {
    // Leaving mid-launch would hide a run that is already being created.
    if (guard.current.launching) return;
    if (guard.current.dirty) {
      setConfirmDiscard(true);
      return;
    }
    onCancel();
  };

  return (
    <Dialog
      onOpenChange={(next) => {
        if (!next) requestClose();
      }}
      open={open}
    >
      {/* Outside-click never dismisses: a stray click would discard a whole setup. */}
      <DialogContent onInteractOutside={(event) => event.preventDefault()} size="wizard">
        <TooltipProvider>
          <TrainWizardForm onGuardChange={onGuardChange} {...props} />
        </TooltipProvider>

        <ConfirmDialog
          confirmLabel="Discard"
          description="The capability, datasets and models you picked will be lost."
          destructive
          onConfirm={() => {
            setConfirmDiscard(false);
            onCancel();
          }}
          onOpenChange={setConfirmDiscard}
          open={confirmDiscard}
          title="Discard this training setup?"
        />
      </DialogContent>
    </Dialog>
  );
}

function TrainWizardForm({
  projectId,
  groupId,
  initialCapabilityId,
  initialDatasetId,
  initialEvalDatasetId,
  onLaunched,
  onGuardChange,
}: Omit<TrainWizardProps, "open" | "onCancel"> & {
  onGuardChange: (guard: CloseGuard) => void;
}) {
  const wizard = useTrainWizard({
    groupId,
    initialCapabilityId,
    initialDatasetId,
    initialEvalDatasetId,
    onLaunched,
    projectId,
  });
  const { dirty, launching, selectedDrafts, totals } = wizard;
  useEffect(() => {
    onGuardChange({ dirty, launching });
    return () => onGuardChange({ dirty: false, launching: false });
  }, [dirty, launching, onGuardChange]);

  return (
    <>
      <DialogHeader>
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
          <DialogTitle>Train model</DialogTitle>
          <DialogDescription>Choose training data, model and evaluations.</DialogDescription>
        </div>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-0">
        <section aria-label="Training data" className="pb-3 pt-5">
          <SetupPanel projectId={projectId} section="data" wizard={wizard} />
        </section>

        <section aria-labelledby="training-model-heading" className="space-y-3 pb-5 pt-0">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h3 className="text-base font-medium" id="training-model-heading">
              Model
            </h3>
            <p className="text-xs text-muted-foreground">
              Select a model and adjust its training settings.
            </p>
          </div>
          <ModelsPanel wizard={wizard} />
        </section>

        <section aria-labelledby="training-evaluation-heading" className="space-y-4 py-5">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Checkbox
              aria-label={
                wizard.nativeDecision ? "Run pre-training baseline evaluation" : "Run evaluations"
              }
              checked={
                wizard.nativeDecision ? wizard.preTrainingBaseline : wizard.evaluationEnabled
              }
              id="train-evaluation-enabled"
              onCheckedChange={(value) => {
                if (wizard.nativeDecision) wizard.setPreTrainingBaseline(value === true);
                else wizard.setEvaluationEnabled(value === true);
              }}
            />
            <h3 className="text-base font-medium" id="training-evaluation-heading">
              <Label
                className="cursor-pointer text-base font-medium"
                htmlFor="train-evaluation-enabled"
              >
                Evaluation
              </Label>
            </h3>
            <p className="ml-1 text-xs text-muted-foreground">
              {wizard.nativeDecision
                ? "Measure base-model performance before training."
                : "Measure model performance on an evaluation dataset."}
            </p>
          </div>
          {(wizard.evaluationEnabled || wizard.nativeDecision) && (
            <SetupPanel projectId={projectId} section="evaluation" wizard={wizard} />
          )}
        </section>

        <CreditsRequiredAlert action="start a training run" />

        {wizard.launchError && (
          <Alert variant="destructive">
            <Icon.warning className="size-4 shrink-0" />
            <span className="text-sm">{wizard.launchError}</span>
          </Alert>
        )}
      </DialogBody>

      <DialogFooter className="justify-between gap-3">
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          {selectedDrafts.length > 0 && (
            <>
              <span>
                {selectedDrafts.length} {selectedDrafts.length === 1 ? "model" : "models"} ·
              </span>
              <span className="font-mono tabular-nums text-foreground">
                {totals.pricedCount > 0 ? <CreditsAmount usd={totals.usd} /> : "No published price"}
              </span>
              {totals.longest && <span>· ~{totals.longest}</span>}
            </>
          )}
        </p>
        <TrainingStartButton wizard={wizard} />
      </DialogFooter>
    </>
  );
}
