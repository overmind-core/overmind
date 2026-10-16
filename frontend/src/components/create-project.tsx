import { useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { DataProjectForm } from "@/components/onboarding/data-project-form";
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

function CreateProjectForm({
  onCancel,
  onSuccess,
  onBusyChange,
  busy,
}: {
  onSuccess?: () => void;
  onBusyChange: (busy: boolean) => void;
  busy: boolean;
  onCancel?: () => void;
}) {
  const [source, setSource] = useState("data");
  const navigate = useNavigate();
  return (
    <>
      <DialogBody>
        <Tabs onValueChange={setSource} value={source}>
          <TabsList>
            <TabsTrigger disabled={busy} value="data">
              From data
            </TabsTrigger>
            <TabsTrigger disabled={busy} value="repository">
              From repository
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="mt-4">
          {source === "data" ? (
            <DataProjectForm
              onBusyChange={onBusyChange}
              onCreated={({ projectId, datasetId }) => {
                onBusyChange(false);
                onSuccess?.();
                void navigate({
                  params: { datasetId },
                  search: { prepare: "training", projectId },
                  to: "/datasets/$datasetId",
                });
              }}
            />
          ) : (
            <OnboardWithAiPanel
              onPromptCopied={onSuccess}
              showManualSetup={false}
              waitingHint="The project appears right after init sync; capabilities follow /overmind setup."
            />
          )}
        </div>
      </DialogBody>
      <DialogFooter>
        <Button disabled={busy} onClick={onCancel} type="button" variant="secondary">
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
  const [busy, setBusy] = useState(false);
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = controlledOnOpenChange ?? setInternalOpen;

  const showTrigger = trigger != null || controlledOpen === undefined;

  return (
    <Dialog
      onOpenChange={(next) => {
        if (!busy) setOpen(next);
      }}
      open={open}
    >
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
            New project
          </DialogTitle>
          <DialogDescription>
            Start with uploaded data or an instrumented repository.
          </DialogDescription>
        </DialogHeader>
        {open ? (
          <CreateProjectForm
            busy={busy}
            onBusyChange={setBusy}
            onCancel={() => setOpen(false)}
            onSuccess={() => setOpen(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
