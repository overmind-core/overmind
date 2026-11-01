import { useState } from "react";

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
import { cn } from "@/lib/utils";

const agents = [
  { id: "codex", label: "Codex", url: "codex://new?prompt=" },
  { id: "cursor", label: "Cursor", url: "cursor://anysphere.cursor-deeplink/prompt?text=" },
  { id: "claude", label: "Claude Code", url: "claude://code/new?q=" },
];

export function PromptAgentActions({
  prompt,
  defaultAgent,
  inverted = false,
  onPromptCopied,
  onCopyFailed,
}: {
  prompt: string;
  defaultAgent?: string | null;
  inverted?: boolean;
  onPromptCopied?: () => void;
  onCopyFailed?: () => void;
}) {
  const [selectedAgent, setSelectedAgent] = useState<string>();
  const agent = agents.find((option) => option.id === (selectedAgent ?? defaultAgent)) ?? agents[0];
  const colors = inverted
    ? "bg-primary-foreground text-primary hover:bg-primary-foreground/90"
    : "";
  const divider = inverted ? "border-l-primary/40" : "border-l-primary-foreground/40";

  async function copyPrompt() {
    try {
      await writeClipboardText(prompt);
      toast.success("Prompt copied");
      onPromptCopied?.();
    } catch {
      toast.error("Couldn't copy the prompt. Try again.");
      onCopyFailed?.();
    }
  }

  return (
    <div aria-label="Coding agent actions" className="inline-flex shrink-0" role="group">
      <Button
        aria-label="Copy prompt"
        className={cn("rounded-r-none", colors)}
        onClick={() => void copyPrompt()}
        size="icon-sm"
      >
        <Icon.copy />
      </Button>
      <Button asChild className={cn("rounded-none", divider, colors)} size="sm">
        <a href={agent.url + encodeURIComponent(prompt)}>
          <CodingAgentIcon kind={agent.id} />
          Open in {agent.label}
        </a>
      </Button>
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
            <DropdownMenuItem key={option.id} onSelect={() => setSelectedAgent(option.id)}>
              <CodingAgentIcon kind={option.id} />
              {option.label}
              {agent.id === option.id && <Icon.success className="ml-auto" />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
