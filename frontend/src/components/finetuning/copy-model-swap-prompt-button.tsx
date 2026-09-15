import { useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { modelSwapPrompt } from "@/client";
import {
  eligibleModelSwapJobs,
  hasInProgressModelSwapJobs,
} from "@/components/finetuning/eligible-model-swap-jobs";
import {
  getModelProviderInfo,
  getProviderIcon,
  ProviderLogo,
} from "@/components/model-provider-chip";
import { Alert } from "@/components/ui/alert";
import { useCopy } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Icon } from "@/components/ui/icons";
import { LoadingState } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { FinetuningJobList } from "@/openapi";

const WAITING_FOR_TRAINING_TOOLTIP = "Available once training finishes.";

function PromptBlock({ text }: { text: string }) {
  const { copied, copy } = useCopy(text);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-end">
        <Button
          aria-label={copied ? "Copied" : "Copy prompt"}
          disabled={!text}
          onClick={copy}
          size="sm"
          type="button"
          variant="secondary"
        >
          {copied ? (
            <Icon.success className="size-3.5 text-success" />
          ) : (
            <Icon.copy className="size-3.5" />
          )}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border/60 bg-wash-raised p-3 font-mono text-xs leading-relaxed text-foreground/85">
        {text}
      </pre>
    </div>
  );
}

function ModelSwapPromptDialog({
  jobId,
  open,
  onOpenChange,
  allowPin,
}: {
  jobId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  allowPin: boolean;
}) {
  const [pin, setPin] = useState(false);
  const query = useQuery({
    enabled: open && !!jobId,
    queryFn: () => modelSwapPrompt({ id: jobId!, pin }),
    queryKey: ["model-swap-prompt", jobId, pin],
  });

  const handleOpenChange = (next: boolean) => {
    if (!next) setPin(false);
    onOpenChange(next);
  };

  return (
    <Dialog onOpenChange={handleOpenChange} open={open}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Use this model</DialogTitle>
          <DialogDescription>Paste this in your coding agent.</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          {query.isPending ? (
            <LoadingState />
          ) : query.isError ? (
            <Alert variant="destructive">{(query.error as Error).message}</Alert>
          ) : (
            <PromptBlock text={query.data?.prompt ?? ""} />
          )}
        </DialogBody>
        {allowPin && (
          <DialogFooter>
            {pin ? (
              <p className="mr-auto text-xs text-muted-foreground">Pins the exact model id.</p>
            ) : (
              <Button onClick={() => setPin(true)} size="sm" type="button" variant="secondary">
                Pin this exact model id
              </Button>
            )}
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function CopyModelSwapPromptButton({
  allowPin = false,
  jobs,
}: {
  allowPin?: boolean;
  jobs: FinetuningJobList[];
}) {
  const eligibleJobs = eligibleModelSwapJobs(jobs);
  const single = eligibleJobs.length === 1 ? eligibleJobs[0] : null;
  const waitingForTraining = eligibleJobs.length === 0 && hasInProgressModelSwapJobs(jobs);
  const [openJobId, setOpenJobId] = useState<string | null>(null);

  const openPrompt = (jobId: string) => setOpenJobId(jobId);

  const dialog = (
    <ModelSwapPromptDialog
      allowPin={allowPin}
      jobId={openJobId}
      onOpenChange={(open) => {
        if (!open) setOpenJobId(null);
      }}
      open={!!openJobId}
    />
  );

  if (eligibleJobs.length === 0) {
    if (!waitingForTraining) return null;
    const waitingButton = (
      <Button
        aria-disabled="true"
        aria-label="Copy a prompt for a trained model"
        onClick={(event) => event.preventDefault()}
        size="sm"
        type="button"
        variant="secondary"
      >
        <Icon.copy />
        Copy prompt
      </Button>
    );
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">{waitingButton}</span>
        </TooltipTrigger>
        <TooltipContent side="top">{WAITING_FOR_TRAINING_TOOLTIP}</TooltipContent>
      </Tooltip>
    );
  }

  if (single) {
    const createButton = (
      <Button
        aria-label={`Copy a prompt switching to ${single.outputModelName}`}
        onClick={() => openPrompt(single.id)}
        size="sm"
        type="button"
        variant="secondary"
      >
        <Icon.copy />
        Copy prompt
      </Button>
    );
    return (
      <>
        {createButton}
        {dialog}
      </>
    );
  }

  const multiTrigger = (
    <DropdownMenuTrigger asChild>
      <Button
        aria-label="Copy a prompt for a trained model"
        size="sm"
        type="button"
        variant="secondary"
      >
        <Icon.copy />
        Copy prompt
        <Icon.chevronDown />
      </Button>
    </DropdownMenuTrigger>
  );

  return (
    <>
      <DropdownMenu>
        {multiTrigger}
        <DropdownMenuContent align="end">
          <DropdownMenuLabel>Pick a model</DropdownMenuLabel>
          <DropdownMenuSeparator />
          {eligibleJobs.map((job) => {
            const info = getModelProviderInfo(job.baseModel);
            return (
              <DropdownMenuItem key={job.id} onSelect={() => openPrompt(job.id)}>
                <ProviderLogo
                  Icon={getProviderIcon(info.id)}
                  providerLabel={info.providerLabel}
                  providerSlug={info.providerSlug}
                />
                <span className="shrink-0 text-xs font-medium">{info.providerLabel}</span>
                <span className="min-w-0 truncate text-xs text-muted-foreground">
                  {info.modelLabel}
                </span>
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
      {dialog}
    </>
  );
}
