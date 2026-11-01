import { trackEvent } from "@/analytics";
import { PromptAgentActions } from "@/components/quickstart/prompt-agent-actions";
import { config } from "@/config";
import { onboardingPrompt } from "@/lib/mcp-setup";
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
  const prompt = onboardingPrompt(
    window.location.origin,
    config.apiUrl,
    projectId ? { datasetId, projectId } : undefined
  );

  const guide = `${window.location.origin}/setup.md`;
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
          "rounded-sm border border-border bg-wash-subtle px-3 py-2 text-sm text-foreground [overflow-wrap:anywhere]",
          !compact && "whitespace-break-spaces rounded-md"
        )}
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
      <PromptAgentActions
        onCopyFailed={() =>
          trackEvent("setup_prompt_copy_failed", { dataset_id: datasetId, project_id: projectId })
        }
        onPromptCopied={() => {
          trackEvent("setup_prompt_copied", { dataset_id: datasetId, project_id: projectId });
          onPromptCopied?.();
        }}
        prompt={prompt}
      />
      {waitingHint ? <p className="text-xs text-muted-foreground">{waitingHint}</p> : null}
    </div>
  );
}
