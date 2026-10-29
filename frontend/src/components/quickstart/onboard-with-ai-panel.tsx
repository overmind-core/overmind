import { useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { config } from "@/config";
import { writeClipboardText } from "@/lib/clipboard";
import { ONBOARDING_GUIDE_URL, onboardingPrompt } from "@/lib/mcp-setup";
import { cn } from "@/lib/utils";

export function OnboardWithAiPanel({
  className,
  compact = false,
  datasetId,
  onPromptCopied,
  projectId,
  waitingHint,
}: {
  className?: string;
  compact?: boolean;
  datasetId?: string;
  onPromptCopied?: () => void;
  projectId?: string;
  waitingHint?: string;
}) {
  const [promptCopied, setPromptCopied] = useState(false);
  const [copyError, setCopyError] = useState("");

  const prompt = onboardingPrompt(
    window.location.origin,
    config.apiUrl,
    datasetId && projectId ? { datasetId, projectId } : undefined
  );

  const guide =
    datasetId && projectId ? `${window.location.origin}/setup.md` : ONBOARDING_GUIDE_URL;

  const copyPrompt = () =>
    writeClipboardText(prompt).then(
      () => {
        setCopyError("");
        setPromptCopied(true);
        onPromptCopied?.();
        setTimeout(() => setPromptCopied(false), 2000);
      },
      () => setCopyError("Couldn't copy — select the prompt text and copy it manually.")
    );

  return (
    <div className={cn(compact ? "space-y-2" : "space-y-3", className)}>
      {!compact && (
        <p className="text-xs text-muted-foreground">
          {datasetId
            ? "Open a workspace in your coding agent and paste the prompt. It prepares the uploaded data here; no repository is needed."
            : "Open your repository in your coding agent with the repo root as the workspace folder, then paste the prompt."}{" "}
          Your agent signs in to Overmind through its MCP connection.
        </p>
      )}
      <p
        aria-label={compact ? "Setup prompt" : undefined}
        className={cn(
          "rounded-sm border border-border bg-wash-subtle px-2 py-1.5 text-sm text-foreground",
          compact
            ? "overflow-x-auto whitespace-nowrap focus-visible:outline focus-visible:outline-ring"
            : "break-all whitespace-break-spaces rounded-md px-3 py-2"
        )}
        tabIndex={compact ? 0 : undefined}
      >
        {compact ? (
          <>
            {prompt.slice(0, -guide.length).replace(/\s+/g, " ")}
            <a
              className="underline underline-offset-2"
              href={guide}
              rel="noreferrer"
              target="_blank"
            >
              {guide}
            </a>
          </>
        ) : (
          prompt
        )}
      </p>
      <Button className="w-full gap-2" onClick={() => void copyPrompt()} size="sm" type="button">
        {promptCopied ? <Icon.success /> : <Icon.copy />}
        {promptCopied ? "Prompt copied" : "Copy onboarding prompt"}
      </Button>
      {copyError ? <Alert variant="destructive">{copyError}</Alert> : null}
      {waitingHint ? <p className="text-xs text-muted-foreground">{waitingHint}</p> : null}
    </div>
  );
}
