import { formatPayload } from "@/components/evaluations/payload-format";
import type { IconName } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

export const TOOL_ICONS: Record<string, IconName> = {
  add_cell: "code",
  diff: "diff",
  edit_cell: "code",
  inspect: "search",
  install: "tool",
  notebook_cell: "code",
  notebook_read: "code",
  notebook_write: "code",
  query: "search",
  set_intent: "target",
  status: "dataset",
  try_script: "code",
};

function IoBlock({ caption, text, failed }: { caption: string; text: string; failed?: boolean }) {
  return (
    <div className="space-y-0.5">
      <span className="text-xs font-medium text-muted-foreground">{caption}</span>
      <pre
        className={cn(
          "max-h-32 overflow-auto whitespace-pre-wrap font-mono text-xs leading-relaxed text-muted-foreground",
          failed && "text-destructive/90"
        )}
      >
        {text}
      </pre>
    </div>
  );
}

export function toolDetail(step: {
  summary?: string;
  preview?: string;
  ok?: boolean;
  input?: unknown;
  output?: unknown;
}) {
  const inn = formatPayload(step.input) || formatPayload(step.summary);
  const out = formatPayload(step.output) || formatPayload(step.preview);
  if (!inn && !out) return undefined;
  return (
    <div className="space-y-1.5">
      {inn ? <IoBlock caption="In" text={inn} /> : null}
      {out ? <IoBlock caption="Out" failed={step.ok === false} text={out} /> : null}
    </div>
  );
}
