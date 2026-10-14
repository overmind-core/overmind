import { useState } from "react";

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
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { usePreparePromptQuery } from "@/hooks/use-datasets";
import { writeClipboardText } from "@/lib/clipboard";
import { notify } from "@/lib/notify";

export function PreparePromptDialog({
  datasetId,
  cellId,
  version,
}: {
  datasetId: string;
  cellId: string;
  version: string;
}) {
  const [open, setOpen] = useState(false);
  const prompt = usePreparePromptQuery(datasetId, cellId, open);
  const copy = useMutation({
    mutationFn: (text: string) => writeClipboardText(text),
    onError: (error) => notify.error(error, "Couldn't copy the prompt"),
    onSuccess: () => notify.success("Prompt copied"),
  });
  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button
          aria-label={`Prepare ${version} with agent`}
          className="h-full rounded-none"
          onClick={(e) => e.stopPropagation()}
          size="icon-xs"
          title="Prepare with agent"
          type="button"
          variant="ghost"
        >
          <Icon.terminal />
        </Button>
      </DialogTrigger>
      <DialogContent onClick={(e) => e.stopPropagation()} size="md">
        <DialogHeader>
          <DialogTitle>Prepare {version} with agent</DialogTitle>
          <DialogDescription>
            A prompt for a coding agent connected to Overmind. Its runs appear in Activity.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {prompt.isError ? (
            <p className="text-xs text-destructive" role="alert">
              Couldn't load the prompt.
            </p>
          ) : prompt.data == null ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <Textarea
              aria-label="Preparation prompt"
              className="h-64 resize-none text-sm"
              readOnly
              value={prompt.data}
            />
          )}
        </DialogBody>
        <DialogFooter>
          <Button
            disabled={prompt.data == null || copy.isPending}
            onClick={() => prompt.data != null && copy.mutate(prompt.data)}
          >
            <Icon.copy />
            Copy prompt
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
