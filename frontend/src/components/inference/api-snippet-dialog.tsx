import { useState } from "react";

import { Link } from "@tanstack/react-router";

import { CreateApiKeyDialog } from "@/components/create-api-key-dialog";
import { useCopy } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { useCapabilityDetailQuery } from "@/hooks/use-query";
import { buildCurlSnippet, buildPythonSnippet, inferenceBaseUrl } from "@/lib/inference-snippets";

function SnippetBlock({ label, text }: { label: string; text: string }) {
  const { copied, copy } = useCopy(text);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <Button
          aria-label={copied ? "Copied" : `Copy ${label}`}
          onClick={copy}
          size="sm"
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
      <pre className="max-h-56 overflow-auto whitespace-pre rounded-md border border-border/60 bg-wash-raised p-3 font-mono text-xs leading-relaxed text-foreground/85">
        {text}
      </pre>
    </div>
  );
}

export function ApiSnippetDialog({
  open,
  onOpenChange,
  modelId,
  capabilityId,
  projectId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  modelId: string;
  capabilityId?: string | null;
  projectId?: string;
}) {
  const [pin, setPin] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const capability = useCapabilityDetailQuery(open ? (capabilityId ?? "") : "").data;
  const routedModelId = capabilityId && !pin ? `overmind/${capabilityId}` : modelId;
  const baseUrl = inferenceBaseUrl();
  const curl = buildCurlSnippet({ baseUrl, modelId: routedModelId });
  const python = buildPythonSnippet({ baseUrl, modelId: routedModelId });

  return (
    <>
      <Dialog
        onOpenChange={(next) => {
          if (!next) setPin(false);
          onOpenChange(next);
        }}
        open={open}
      >
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Call this model</DialogTitle>
            <DialogDescription>
              OpenAI-compatible chat completions at the project API base URL.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-4">
            {capabilityId ? (
              <div className="flex items-center justify-between gap-3 text-xs">
                <p className="text-muted-foreground">
                  {pin ? "Pinned to this version." : "Follows the capability’s live model."}
                </p>
                <Button onClick={() => setPin(!pin)} size="sm" variant="secondary">
                  {pin ? "Use live alias" : "Pin this version"}
                </Button>
              </div>
            ) : null}
            {capabilityId && !pin && capability && !capability.activeModel ? (
              <p className="text-xs text-warning">Make a model live before calling this alias.</p>
            ) : null}
            <p className="text-xs text-muted-foreground">
              Set OVERMIND_API_KEY to an existing project key.
            </p>
            {projectId ? (
              <div className="flex items-center gap-2">
                <Button asChild size="sm" variant="secondary">
                  <Link params={{ projectId }} to="/projects/$projectId">
                    Project API keys
                  </Link>
                </Button>
                <Button onClick={() => setKeyOpen(true)} size="sm" variant="secondary">
                  New API key
                </Button>
              </div>
            ) : null}
            <SnippetBlock label="cURL" text={curl} />
            <SnippetBlock label="Python (OpenAI SDK)" text={python} />
          </DialogBody>
        </DialogContent>
      </Dialog>
      {projectId ? (
        <CreateApiKeyDialog onOpenChange={setKeyOpen} open={keyOpen} projectId={projectId} />
      ) : null}
    </>
  );
}
