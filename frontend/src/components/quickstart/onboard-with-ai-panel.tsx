import { useState } from "react";

import apiClient from "@/client";
import { McpClientPicker, mcpClientLogo } from "@/components/mcp-setup-panel";
import { Alert } from "@/components/ui/alert";
import { useCopy } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { config } from "@/config";
import { useValidatedAccountApiKey } from "@/hooks/use-validated-cached-api-key";
import { writeClipboardText } from "@/lib/clipboard";
import {
  type McpClient,
  manualSetupCommand,
  mcpClientMeta,
  mcpInitEnvFlag,
  onboardWithAiBootstrapPrompt,
  sdkPipInstall,
} from "@/lib/mcp-setup";
import { writeAccountApiKey } from "@/lib/project-api-key";
import { LABEL } from "@/lib/typography";
import { cn } from "@/lib/utils";
import { APITokenScopeScopeEnum } from "@/openapi";

export function OnboardWithAiPanel({
  className,
  onPromptCopied,
  projectId,
  showManualSetup = true,
  waitingHint,
}: {
  className?: string;
  onPromptCopied?: () => void;
  projectId?: string;
  showManualSetup?: boolean;
  waitingHint?: string;
}) {
  const apiUrl = config.apiUrl.replace(/\/$/, "");
  const [client, setClient] = useState<McpClient>("cursor");
  const { apiKey, ready: keyReady, setApiKey } = useValidatedAccountApiKey(apiUrl);
  const [mintPending, setMintPending] = useState(false);
  const [mintError, setMintError] = useState("");
  const [promptCopied, setPromptCopied] = useState(false);

  const keyValue = apiKey || "YOUR_API_KEY";
  const meta = mcpClientMeta(client);
  const Logo = mcpClientLogo(client);
  const manualCommands = manualSetupCommand(client, keyValue, apiUrl, projectId);
  const initEnvFlag = client === "codex" ? "" : mcpInitEnvFlag(apiUrl);
  const { copied: manualCopied, copy: copyManual } = useCopy(manualCommands);

  const mintKey = async (): Promise<string | null> => {
    if (apiKey) return apiKey;
    setMintError("");
    setMintPending(true);
    try {
      const response = await apiClient.auth.authApiKeysCreate({
        aPITokenCreateRequestRequest: {
          name: `Onboarding — ${new Date().toISOString().slice(0, 10)}`,
          scope: { scope: APITokenScopeScopeEnum.account },
        },
      });
      writeAccountApiKey(response.key, apiUrl);
      setApiKey(response.key);
      return response.key;
    } catch (err) {
      setMintError((err as Error).message || "Failed to create API key");
      return null;
    } finally {
      setMintPending(false);
    }
  };

  const copyOnboardingPrompt = () => {
    const prompt = mintKey().then((key) => {
      if (!key) throw new Error("API key creation failed");
      return onboardWithAiBootstrapPrompt(client, key, apiUrl);
    });

    void writeClipboardText(prompt).then(
      () => {
        setPromptCopied(true);
        onPromptCopied?.();
        setTimeout(() => setPromptCopied(false), 2000);
      },
      () =>
        setMintError(
          (current) => current || "Couldn't copy — select the prompt text and copy it manually."
        )
    );
  };

  return (
    <div className={cn("space-y-4", className)}>
      <div className="space-y-3">
        <McpClientPicker onChange={setClient} value={client} />
        <p className="text-xs text-muted-foreground">
          Open your repository in {meta.label} with the repo root as the workspace folder, then
          paste the prompt. It contains a temporary account key — paste only into your local coding
          agent.
        </p>
        <Button
          className="w-full gap-2"
          disabled={mintPending || !keyReady}
          onClick={() => void copyOnboardingPrompt()}
          size="sm"
          type="button"
        >
          {mintPending || !keyReady ? (
            <Spinner className="text-current" size="sm" />
          ) : promptCopied ? (
            <Icon.success />
          ) : (
            <Icon.copy />
          )}
          {mintPending
            ? "Creating key…"
            : !keyReady
              ? "Checking key…"
              : promptCopied
                ? "Prompt copied"
                : `Copy onboarding prompt for ${meta.label}`}
          <Logo aria-hidden className="size-3.5 text-muted-foreground" size={14} />
        </Button>
        {mintError ? <Alert variant="destructive">{mintError}</Alert> : null}
        {waitingHint ? <p className="text-xs text-muted-foreground">{waitingHint}</p> : null}
      </div>

      {showManualSetup ? (
        <details className="overflow-hidden rounded-md border border-border">
          <summary className="cursor-pointer border-b border-border/70 bg-muted px-3 py-2 text-sm text-muted-foreground hover:text-foreground">
            Manual setup
          </summary>
          <div className="space-y-0">
            <div className="flex items-center justify-between gap-2 border-b border-border/70 px-3 py-2">
              <span
                className={cn(
                  LABEL.pixel,
                  "inline-flex items-center gap-1.5 text-muted-foreground"
                )}
              >
                <Icon.terminal className="size-3.5" />
                Terminal
              </span>
              <Button onClick={copyManual} size="sm" type="button" variant="secondary">
                {manualCopied ? <Icon.success /> : <Icon.copy />}
                {manualCopied ? "Copied" : "Copy"}
              </Button>
            </div>
            <div className="space-y-2.5 bg-wash-subtle p-4 text-left font-mono text-xs leading-relaxed">
              <p className="break-all text-foreground">
                <span className="text-muted-foreground">$ </span>
                {sdkPipInstall(apiUrl)}
              </p>
              <div className="space-y-1.5 border-t border-border/70 pt-2.5">
                <EnvLine name="OVERMIND_API_URL" value={apiUrl} />
                {projectId ? <EnvLine name="OVERMIND_PROJECT_ID" value={projectId} /> : null}
                <EnvLine muted={!apiKey} name="OVERMIND_API_KEY" value={keyValue} />
              </div>
              <p className="border-t border-border/70 pt-2.5 text-foreground">
                <span className="text-muted-foreground">$ </span>
                overmind init --ide {client}
                {initEnvFlag}
              </p>
              <p className="border-t border-border/70 pt-2.5 text-foreground">
                <span className="text-muted-foreground">$ </span>
                overmind sync
              </p>
            </div>
          </div>
        </details>
      ) : null}
    </div>
  );
}

function EnvLine({ name, value, muted }: { name: string; value: string; muted?: boolean }) {
  return (
    <p className={cn("break-all", muted ? "text-muted-foreground" : "text-foreground")}>
      <span className="text-muted-foreground">export {name}=</span>
      {value}
    </p>
  );
}
