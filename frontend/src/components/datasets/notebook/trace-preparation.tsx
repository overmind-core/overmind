import { useMutation } from "@tanstack/react-query";

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
import { Textarea } from "@/components/ui/textarea";
import { config } from "@/config";
import { writeClipboardText } from "@/lib/clipboard";
import { notify } from "@/lib/notify";
import type { Cell, Dataset } from "@/openapi";

export function TracePreparation({ dataset, source }: { dataset: Dataset; source: Cell }) {
  const prompt = `Prepare the existing Overmind trace dataset ${JSON.stringify(dataset.name)} in Data Workshop.

Use the Overmind connection at ${config.apiUrl.replace(/\/$/, "")}/api/mcp/. Verify the endpoint with list_projects and pass project_id=${dataset.project} on every project operation. If the connected endpoint differs, stop and report the mismatch.
Dataset: ${dataset.id}
Source cell: ${source.id}
Source fingerprint: ${source.fingerprint}
Purpose: ${dataset.intent}
User brief: ${JSON.stringify(dataset.brief || "No brief supplied")}

Inspect this dataset, its source rows, and its associated capability contract. Verify the pinned source fingerprint before processing. Preserve the original trace evidence and row lineage. Captured outputs and scores are not verified reference answers; do not invent targets or silently treat observed outputs as ground truth. If the purpose is pending, ask whether the user wants training, evaluation or exploration. Resolve missing target meaning or references with the user before preparing consumer data.

Follow the connected author-dataset-transformation workflow. Inspect the workbench for a compatible retained recipe. Author or adapt a retained Python package for the required stages, upload it with the Overmind CLI, and save the pipeline against this dataset before processing the full source. Validate and preview it, then publish from the pinned original source and inspect the exact run and resulting preparation graph. Corrections revise the same recipe family and rerun from the original source. Do not upload transformed rows as an unrelated dataset.

Report the source-to-output changes, remaining uncertainties and measured consumer compatibility separately from semantic quality. Leave training, evaluation and inference unlaunched.`;
  const copy = useMutation({
    mutationFn: () => writeClipboardText(prompt),
    onError: (error) => notify.error(error, "Could not copy preparation prompt"),
    onSuccess: () => notify.success("Prompt copied"),
  });

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-2.5 py-2">
      <Dialog>
        <DialogTrigger asChild>
          <Button className="nodrag" size="xs" variant="secondary">
            <Icon.copy />
            Prepare with agent
          </Button>
        </DialogTrigger>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Prepare trace data</DialogTitle>
            <DialogDescription>
              Paste this prompt into your coding agent connected to Overmind. Preparation runs
              appear in this workshop.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <Textarea
              aria-label="Preparation prompt"
              className="h-64 resize-none text-sm"
              readOnly
              value={prompt}
            />
          </DialogBody>
          <DialogFooter>
            <Button disabled={copy.isPending} onClick={() => copy.mutate()} size="sm">
              <Icon.copy />
              Copy preparation prompt
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <span className="text-xs text-muted-foreground">
        Prepare and review these traces in your coding agent.
      </span>
    </div>
  );
}
