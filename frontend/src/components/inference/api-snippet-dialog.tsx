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
import { config } from "@/config";

export function inferenceBaseUrl(apiUrl = config.apiUrl): string {
  return `${apiUrl.replace(/\/$/, "")}/api/v1`;
}

export function buildCurlSnippet({
  baseUrl,
  modelId,
  apiKey,
}: {
  baseUrl: string;
  modelId: string;
  apiKey: string;
}): string {
  return [
    `curl ${baseUrl}/chat/completions \\`,
    `  -H "Authorization: Bearer ${apiKey}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{`,
    `    "model": "${modelId}",`,
    `    "messages": [{"role": "user", "content": "Hello"}]`,
    `  }'`,
  ].join("\n");
}

export function buildPythonSnippet({
  baseUrl,
  modelId,
  apiKey,
}: {
  baseUrl: string;
  modelId: string;
  apiKey: string;
}): string {
  return [
    `from openai import OpenAI`,
    ``,
    `client = OpenAI(`,
    `    base_url="${baseUrl}",`,
    `    api_key="${apiKey}",`,
    `)`,
    ``,
    `resp = client.chat.completions.create(`,
    `    model="${modelId}",`,
    `    messages=[{"role": "user", "content": "Hello"}],`,
    `)`,
    `print(resp.choices[0].message.content)`,
  ].join("\n");
}

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
          variant="ghost"
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
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  modelId: string;
}) {
  const baseUrl = inferenceBaseUrl();
  const apiKey = "<project_api_key>";
  const curl = buildCurlSnippet({ apiKey, baseUrl, modelId });
  const python = buildPythonSnippet({ apiKey, baseUrl, modelId });

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Call this model</DialogTitle>
          <DialogDescription>
            OpenAI-compatible chat completions at the project API base URL.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-4">
          <SnippetBlock label="cURL" text={curl} />
          <SnippetBlock label="Python (OpenAI SDK)" text={python} />
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
