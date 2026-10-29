import { useState } from "react";

import { Link } from "@tanstack/react-router";

import { ScriptCode } from "@/components/datasets/notebook/script-code";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LoadingState } from "@/components/ui/spinner";
import { useFinetuningJobQuery } from "@/hooks/use-finetuning";
import { useCapabilityDetailQuery } from "@/hooks/use-query";
import {
  buildCurlSnippet,
  buildDecisionSnippets,
  buildPythonSnippet,
  inferenceBaseUrl,
} from "@/lib/inference-snippets";
import type { DecisionInputRequest } from "@/openapi";

function SnippetBlock({
  label,
  text,
  kind,
}: {
  label: string;
  text: string;
  kind: "python" | "shell";
}) {
  const { copied, copy } = useCopy(text);
  return (
    <div className="overflow-hidden rounded-md border border-border bg-code-surface">
      <div className="flex items-center justify-between gap-2 px-4 pt-3">
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
      <div className="p-4">
        <ScriptCode
          aria-label={`${label} API example`}
          className="[&_.font-mono]:[font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace] [&_ol]:w-auto [&_ol]:min-w-5 [&_ol]:text-muted-foreground [&_pre]:w-max [&_pre]:min-w-full [&_pre]:whitespace-pre [&_pre]:break-normal"
          kind={kind}
          source={text}
        />
      </div>
    </div>
  );
}

export function ApiSnippetDialog({
  open,
  onOpenChange,
  modelId,
  capabilityId,
  projectId,
  decisionDeploymentId,
  trainingJobId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  modelId: string;
  capabilityId?: string | null;
  projectId?: string;
  decisionDeploymentId?: string;
  trainingJobId?: string | null;
}) {
  const [exampleIndex, setExampleIndex] = useState("0");
  const training = useFinetuningJobQuery(
    open && decisionDeploymentId ? (trainingJobId ?? undefined) : undefined
  );
  const summary = training.data?.record?.prediction_labels;
  const examples: (Omit<DecisionInputRequest, "state"> & { example_state?: string | null })[] =
    summary?.state === "available" ? (summary.label_sets ?? []) : [];
  const example = examples[Number(exampleIndex)];
  const [pin, setPin] = useState(false);
  const capability = useCapabilityDetailQuery(open ? (capabilityId ?? "") : "").data;
  const routedModelId = capabilityId && !pin ? `overmind/${capabilityId}` : modelId;
  const baseUrl = inferenceBaseUrl();
  const decision =
    decisionDeploymentId && example && typeof example.example_state === "string"
      ? buildDecisionSnippets(modelId, {
          kind: example.kind,
          options: example.options,
          question: example.question,
          state: example.example_state,
        })
      : null;
  const curl = decision?.curl ?? buildCurlSnippet({ baseUrl, modelId: routedModelId });
  const python = decision?.python ?? buildPythonSnippet({ baseUrl, modelId: routedModelId });

  return (
    <Dialog
      onOpenChange={(next) => {
        if (!next) {
          setPin(false);
          setExampleIndex("0");
        }
        onOpenChange(next);
      }}
      open={open}
    >
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Call this model</DialogTitle>
          <DialogDescription>
            {decisionDeploymentId
              ? "TypeSafe systemone. Point the SDK base URL at this API."
              : "OpenAI-compatible chat completions at the project API base URL."}
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
            {projectId ? (
              <>
                {" "}
                If you don't have an API key,{" "}
                <Link
                  className="text-foreground underline underline-offset-2"
                  hash="api-keys-heading"
                  params={{ projectId }}
                  to="/projects/$projectId"
                >
                  generate here
                </Link>
                .
              </>
            ) : null}
          </p>
          {decisionDeploymentId && training.isFetching && !training.data ? (
            <LoadingState className="min-h-40" label="Loading model examples" />
          ) : (
            <>
              {decisionDeploymentId && examples.length > 1 && (
                <Select onValueChange={setExampleIndex} value={exampleIndex}>
                  <SelectTrigger aria-label="Decision example" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {examples.map((item, index) => (
                      <SelectItem key={index} value={String(index)}>
                        {item.question}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              {decisionDeploymentId && !decision ? (
                <p className="text-sm text-muted-foreground">
                  A complete example input is unavailable for this model’s pinned training version.
                </p>
              ) : (
                <>
                  {decisionDeploymentId && (
                    <p className="text-xs text-muted-foreground">
                      Uses an example from this model’s training version. Replace state with your
                      input.
                      {summary?.total_sets > examples.length &&
                        ` Showing ${examples.length} of ${summary.total_sets} decision examples.`}
                    </p>
                  )}
                  <SnippetBlock kind="shell" label="cURL" text={curl} />
                  <SnippetBlock
                    kind="python"
                    label={decisionDeploymentId ? "Python (TypeSafe SDK)" : "Python (OpenAI SDK)"}
                    text={python}
                  />
                </>
              )}
            </>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
