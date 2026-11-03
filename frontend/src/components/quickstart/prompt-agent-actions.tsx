import { useSyncExternalStore } from "react";

import { toast } from "sonner";

import { CodingAgentIcon } from "@/components/coding-agent-icon";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Icon } from "@/components/ui/icons";
import { writeClipboardText } from "@/lib/clipboard";
import { isMobileBrowser } from "@/lib/platform";
import { cn } from "@/lib/utils";

// OpenCode's desktop link needs a local directory, which the Console does not know.
const agents: { id: string; label: string; url?: string }[] = [
  { id: "claude", label: "Claude Code", url: "claude://code/new?q=" },
  { id: "codex", label: "Codex", url: "codex://new?prompt=" },
  { id: "cursor", label: "Cursor", url: "cursor://anysphere.cursor-deeplink/prompt?text=" },
  { id: "opencode", label: "OpenCode" },
];

const preferenceKey = "preferred-coding-agent";
const preferenceEvent = "coding-agent-preference";
let unsavedPreference: string | null = null;

function readPreference() {
  if (unsavedPreference) return unsavedPreference;
  try {
    const value = localStorage.getItem(preferenceKey);
    return agents.some((agent) => agent.id === value) ? value : null;
  } catch {
    return null;
  }
}

function subscribePreference(listener: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== preferenceKey) return;
    unsavedPreference = null;
    listener();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(preferenceEvent, listener);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(preferenceEvent, listener);
  };
}

function chooseAgent(value: string) {
  try {
    localStorage.setItem(preferenceKey, value);
    unsavedPreference = null;
  } catch {
    unsavedPreference = value;
    toast.error("Couldn't save the coding-agent preference.");
  }
  window.dispatchEvent(new Event(preferenceEvent));
}

export function PromptAgentActions({
  prompt,
  defaultAgent,
  inverted = false,
  onCopied,
  onCopyFailed,
  onOpenClicked,
}: {
  prompt: string;
  defaultAgent?: string | null;
  inverted?: boolean;
  onCopied?: () => void;
  onCopyFailed?: () => void;
  onOpenClicked?: (agent: string) => void;
}) {
  const selectedAgent = useSyncExternalStore(subscribePreference, readPreference, () => null);
  const agent = agents.find((option) => option.id === (selectedAgent ?? defaultAgent));
  // Agent links open desktop apps, so a phone can only copy.
  const mobile = isMobileBrowser();
  const url = !mobile && agent?.url;
  const colors = inverted
    ? "bg-primary-foreground text-primary hover:bg-primary-foreground/90"
    : "";
  const divider = inverted ? "border-l-primary/40" : "border-l-primary-foreground/40";

  async function copyPrompt() {
    try {
      await writeClipboardText(prompt);
      toast.success("Prompt copied");
      onCopied?.();
    } catch {
      toast.error("Couldn't copy the prompt. Try again.");
      onCopyFailed?.();
    }
  }

  return (
    <div aria-label="Coding agent actions" className="inline-flex shrink-0" role="group">
      {url ? (
        <>
          <Button
            aria-label="Copy prompt"
            className={cn("rounded-r-none", colors)}
            onClick={() => void copyPrompt()}
            size="icon-sm"
          >
            <Icon.copy />
          </Button>
          <Button asChild className={cn("rounded-none", divider, colors)} size="sm">
            <a
              aria-label={`Open in ${agent.label}`}
              href={url + encodeURIComponent(prompt)}
              onClick={() => onOpenClicked?.(agent.id)}
            >
              <CodingAgentIcon kind={agent.id} />
              Open in {agent.label}
            </a>
          </Button>
        </>
      ) : (
        <Button
          className={cn(!mobile && "rounded-r-none", colors)}
          onClick={() => void copyPrompt()}
          size="sm"
        >
          <Icon.copy />
          Copy prompt
        </Button>
      )}
      {!mobile && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              aria-label="Choose coding agent"
              className={cn("rounded-l-none", divider, colors)}
              size="icon-sm"
            >
              <Icon.chevronDown />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {agents.map((option) => (
              <DropdownMenuItem
                aria-label={option.label}
                key={option.id}
                onSelect={() => chooseAgent(option.id)}
              >
                <CodingAgentIcon kind={option.id} />
                {option.label}
                {agent?.id === option.id && <Icon.success className="ml-auto" />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}
