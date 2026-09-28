import { useMutation } from "@tanstack/react-query";

import { modelSwapPrompt } from "@/client";
import {
  eligibleModelSwapJobs,
  hasInProgressModelSwapJobs,
} from "@/components/finetuning/eligible-model-swap-jobs";
import { ModelOptionLabel } from "@/components/model-option-label";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { notify } from "@/lib/notify";
import type { FinetuningJobList } from "@/openapi";

const WAITING_FOR_TRAINING_TOOLTIP = "Available once training finishes.";

export function CopyModelSwapPromptButton({ jobs }: { jobs: FinetuningJobList[] }) {
  const eligibleJobs = eligibleModelSwapJobs(jobs);
  const single = eligibleJobs.length === 1 ? eligibleJobs[0] : null;
  const waitingForTraining = eligibleJobs.length === 0 && hasInProgressModelSwapJobs(jobs);
  const copyPrompt = useMutation({
    mutationFn: async (jobId: string) => {
      const { prompt } = await modelSwapPrompt({ id: jobId, pin: false });
      await navigator.clipboard.writeText(prompt);
    },
    onError: (error) => notify.error(error, "Could not copy prompt"),
    onSuccess: () => notify.success("Prompt copied"),
  });

  if (eligibleJobs.length === 0) {
    if (!waitingForTraining) return null;
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
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
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">{WAITING_FOR_TRAINING_TOOLTIP}</TooltipContent>
      </Tooltip>
    );
  }

  if (single) {
    return (
      <Button
        aria-label={`Copy a prompt switching to ${single.outputModelName}`}
        disabled={copyPrompt.isPending}
        onClick={() => copyPrompt.mutate(single.id)}
        size="sm"
        type="button"
        variant="secondary"
      >
        {copyPrompt.isPending ? <Spinner /> : <Icon.copy />}
        Copy prompt
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label="Copy a prompt for a trained model"
          disabled={copyPrompt.isPending}
          size="sm"
          type="button"
          variant="secondary"
        >
          {copyPrompt.isPending ? <Spinner /> : <Icon.copy />}
          Copy prompt
          <Icon.chevronDown />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Pick a model</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {eligibleJobs.map((job) => (
          <DropdownMenuItem
            disabled={copyPrompt.isPending}
            key={job.id}
            onSelect={() => copyPrompt.mutate(job.id)}
          >
            <ModelOptionLabel model={job.baseModel} />
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
