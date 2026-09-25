import { useCallback, useEffect, useRef, useState } from "react";

import { CreditsRequiredAlert } from "@/components/billing/credits-required";
import { ModelsPanel } from "@/components/finetuning/train/models-panel";
import { SetupPanel } from "@/components/finetuning/train/setup-panel";
import { TrainingStartButton } from "@/components/finetuning/train/start-button";
import { useTrainWizard } from "@/components/finetuning/train/use-train-wizard";
import { Alert } from "@/components/ui/alert";
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
        <DialogTitle>Train model</DialogTitle>
        <DialogDescription>
          Fine-tune a model on one of this capability&apos;s datasets.
        </DialogDescription>
      </DialogHeader>

      <DialogBody className="flex flex-col gap-4">
        <SetupPanel projectId={projectId} wizard={wizard} />
        <ModelsPanel wizard={wizard} />

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
