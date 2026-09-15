import { useState } from "react";

import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";

function CreateProjectForm({
  onCancel,
  onSuccess,
}: {
  onSuccess?: () => void;
  onCancel?: () => void;
}) {
  return (
    <>
      <DialogBody>
        <OnboardWithAiPanel
          onPromptCopied={onSuccess}
          showManualSetup={false}
          waitingHint="The project appears right after init sync; capabilities follow /overmind setup."
        />
      </DialogBody>
      <DialogFooter>
        <Button onClick={onCancel} type="button" variant="secondary">
          <Icon.close />
          Close
        </Button>
      </DialogFooter>
    </>
  );
}

interface CreateProjectDialogProps {
  trigger?: React.ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function CreateProjectDialog({
  trigger,
  open: controlledOpen,
  onOpenChange: controlledOnOpenChange,
}: CreateProjectDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = controlledOnOpenChange ?? setInternalOpen;

  const showTrigger = trigger != null || controlledOpen === undefined;

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      {showTrigger ? (
        <DialogTrigger asChild>
          {trigger ?? (
            <Button className="gap-2">
              <Icon.projectAdd />
              New project
            </Button>
          )}
        </DialogTrigger>
      ) : null}
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Icon.folderAdd className="size-5 text-foreground" />
            Onboard a repository
          </DialogTitle>
          <DialogDescription>
            Copy the prompt into your coding agent. The project is created when you overmind sync
            from that repo.
          </DialogDescription>
        </DialogHeader>
        {open ? (
          <CreateProjectForm onCancel={() => setOpen(false)} onSuccess={() => setOpen(false)} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
