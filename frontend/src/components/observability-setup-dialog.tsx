import { useState } from "react";

import { McpClientPicker, mcpClientLogo } from "@/components/mcp-setup-panel";
import { useCopy } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { type McpClient, mcpClientMeta, telemetrySetupPrompt } from "@/lib/mcp-setup";

function Step({
  children,
  index,
  title,
}: {
  children: React.ReactNode;
  index: number;
  title: string;
}) {
  return (
    <li className="grid grid-cols-[1.5rem_1fr] gap-x-3 gap-y-2">
      <span className="flex size-6 items-center justify-center rounded-sm border border-border bg-wash-subtle font-mono text-xs text-muted-foreground">
        {index}
      </span>
      <p className="self-center text-sm font-medium">{title}</p>
      <div className="col-start-2 min-w-0">{children}</div>
    </li>
  );
}

function PromptStep({ client }: { client: McpClient }) {
  const { copied, copy } = useCopy(telemetrySetupPrompt(client));
  const meta = mcpClientMeta(client);
  const Logo = mcpClientLogo(client);

  return (
    <div className="space-y-1">
      <Button
        className="w-full gap-2"
        onClick={copy}
        size="sm"
        type="button"
        variant={copied ? "default" : "outline"}
      >
        {copied ? <Icon.success /> : <Icon.copy />}
        {copied ? "Prompt copied" : `Copy prompt for ${meta.label}`}
        <Logo aria-hidden className="size-3.5 text-muted-foreground" size={14} />
      </Button>
      <p className="text-xs text-muted-foreground">
        Paste it into {meta.label}. It uses the existing MCP setup, installs tracing support,
        instruments each capability, and verifies the traces.
      </p>
    </div>
  );
}

export function ObservabilitySetupDialog({
  onOpenChange,
  open,
}: {
  onOpenChange: (open: boolean) => void;
  open: boolean;
}) {
  const [client, setClient] = useState<McpClient>("cursor");

  const steps = [
    {
      body: <McpClientPicker onChange={setClient} value={client} />,
      title: "Choose your coding agent",
    },
    {
      body: <PromptStep client={client} key={client} />,
      title: "Hand over the telemetry prompt",
    },
  ];

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Instrument your repository</DialogTitle>
          <DialogDescription>
            Choose your coding agent and give it the instrumentation prompt.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <ol className="space-y-4">
            {steps.map(({ body, title }, i) => (
              <Step index={i + 1} key={title} title={title}>
                {body}
              </Step>
            ))}
          </ol>
        </DialogBody>
        <DialogFooter>
          <Button asChild size="sm" variant="secondary">
            <a
              href="https://docs.overmindlab.ai/core/observability"
              rel="noopener noreferrer"
              target="_blank"
            >
              <Icon.docs />
              Full guide
            </a>
          </Button>
          <Button onClick={() => onOpenChange(false)} size="sm">
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
