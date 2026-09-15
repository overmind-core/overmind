import { getProviderIcon } from "@/components/model-provider-chip";
import { useCopy } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import {
  API_KEY_PLACEHOLDER,
  MCP_CLIENTS,
  type McpClient,
  mcpClientMeta,
  mcpInitCommand,
} from "@/lib/mcp-setup";
import { cn } from "@/lib/utils";

type ClientLogo = React.ComponentType<{ className?: string; size?: number | string }>;

export const mcpClientLogo = (client: McpClient): ClientLogo => {
  if (client === "cursor") return getProviderIcon("cursor") ?? Icon.terminal;
  if (client === "claude") return getProviderIcon("claude") ?? Icon.terminal;
  if (client === "codex") return getProviderIcon("openai") ?? Icon.terminal;
  return Icon.terminal;
};

function CopySnippet({
  copyLabel,
  disabled = false,
  heading,
  text,
}: {
  copyLabel: string;
  disabled?: boolean;
  heading?: string;
  text: string;
}) {
  const { copied, copy } = useCopy(text);
  return (
    <div className="space-y-1">
      {heading ? <p className="text-xs text-muted-foreground">{heading}</p> : null}
      <div className="relative">
        <button
          aria-label={copied ? "Copied" : copyLabel}
          className="absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded-sm border border-border/60 bg-background/80 px-1.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
          disabled={disabled}
          onClick={copy}
          title={copied ? "Copied" : copyLabel}
          type="button"
        >
          {copied ? (
            <Icon.success className="size-3 text-success" />
          ) : (
            <Icon.copy className="size-3" />
          )}
          {copied ? "Copied" : "Copy"}
        </button>
        <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-border bg-wash-subtle p-3 pr-16 font-mono text-xs leading-relaxed text-foreground">
          {text}
        </pre>
      </div>
    </div>
  );
}

export function McpClientPicker({
  onChange,
  value,
}: {
  onChange: (client: McpClient) => void;
  value: McpClient;
}) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {MCP_CLIENTS.map(({ id, label }) => {
        const Logo = mcpClientLogo(id);
        return (
          <Button
            aria-pressed={value === id}
            className="w-full justify-start gap-2"
            key={id}
            onClick={() => onChange(id)}
            size="sm"
            type="button"
            variant={value === id ? "default" : "outline"}
          >
            <Logo className="size-3.5" size={14} />
            <span className="truncate">{label}</span>
          </Button>
        );
      })}
    </div>
  );
}

/** Init and sync command for the selected agent. */
export function McpConnectSnippet({
  apiKey,
  className,
  client,
  extra,
  projectId,
}: {
  apiKey: string | null;
  className?: string;
  client: McpClient;
  extra?: "tracing";
  projectId?: string;
}) {
  const meta = mcpClientMeta(client);
  const key = apiKey ?? API_KEY_PLACEHOLDER;

  return (
    <div className={cn("space-y-2", className)}>
      <CopySnippet
        copyLabel="Copy command"
        disabled={!apiKey}
        heading={`Run from the project directory. Writes ${meta.destination} and the overmind skill.`}
        text={mcpInitCommand(client, key, undefined, extra, projectId)}
      />
    </div>
  );
}
