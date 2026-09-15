import { useLayoutEffect, useRef, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import type { Capability } from "@/openapi";

type Flow = Capability["flow"];
type PromptSection = { id: string; label: string; text: string };

/** The `*Excerpt` fields are the fallback for capabilities analysed before full-prompt
 * capture shipped. */
const collectPromptSections = (flow: Flow): PromptSection[] => {
  const sections: PromptSection[] = [];
  const seen = new Set<string>();

  const push = (id: string, label: string, raw: string | undefined | null) => {
    const text = (raw ?? "").trim();
    if (!text || seen.has(text)) return;
    seen.add(text);
    sections.push({ id, label, text });
  };

  push("system", "System prompt", flow.systemPrompt || flow.systemPromptExcerpt);
  (flow.modes ?? []).forEach((mode, index) => {
    const label = mode.name?.trim() ? `${mode.name} task` : `Task ${index + 1}`;
    push(`mode-${index}`, label, mode.prompt || mode.promptExcerpt);
  });

  return sections;
};

const wordCount = (text: string): number => text.split(/\s+/).filter(Boolean).length;

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable (insecure context) — silently ignore.
    }
  };

  return (
    <button
      className="inline-flex h-6 shrink-0 items-center gap-1 rounded-sm border border-border/60 px-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={handleCopy}
      type="button"
    >
      {copied ? <Icon.success className="size-3 text-success" /> : <Icon.copy className="size-3" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function PromptDialog({
  sections,
  open,
  onOpenChange,
}: {
  sections: PromptSection[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [activeId, setActiveId] = useState(sections[0]?.id ?? "");
  const active = sections.find((section) => section.id === activeId) ?? sections[0];

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent size="full">
        <DialogHeader>
          <DialogTitle className="inline-flex items-center gap-2">
            <Icon.terminal className="size-4 text-muted-foreground" />
            Capability prompt
          </DialogTitle>
          <DialogDescription>
            The verbatim prompt text captured from the capability's source.
          </DialogDescription>
        </DialogHeader>

        {/* Two panes that scroll independently, so this is the body region rather
            than a `DialogBody` — each column owns its padding. The rail column
            exists only alongside the rail, or a lone prompt lands in the 13rem track. */}
        <div
          className={cn(
            "flex min-h-0 flex-1 flex-col",
            sections.length > 1 && "md:grid md:grid-cols-[minmax(0,13rem)_minmax(0,1fr)]"
          )}
        >
          {sections.length > 1 && (
            <div className="flex shrink-0 flex-col gap-0.5 overflow-y-auto border-b border-border/70 p-2 md:border-b-0 md:border-r">
              {sections.map((section) => (
                <button
                  className={cn(
                    "flex w-full flex-col items-start gap-0.5 rounded-sm px-2 py-1.5 text-left transition-colors duration-150",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                    section.id === activeId
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:bg-wash-raised hover:text-foreground"
                  )}
                  key={section.id}
                  onClick={() => setActiveId(section.id)}
                  type="button"
                >
                  <span className="w-full truncate text-xs">{section.label}</span>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {wordCount(section.text)} words
                  </span>
                </button>
              ))}
            </div>
          )}

          <div className="flex min-h-0 min-w-0 flex-col">
            <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border/70 px-4 py-2">
              <p className="truncate text-xs text-foreground">{active?.label}</p>
              {active && <CopyButton text={active.text} />}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-foreground">
                {active?.text}
              </pre>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The preview measures real overflow rather than guessing from text length — a
 * length heuristic silently clipped short-but-tall prompts.
 */
export function CapabilityPromptCard({ flow }: { flow: Flow }) {
  const sections = collectPromptSections(flow);
  const [open, setOpen] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const previewRef = useRef<HTMLPreElement>(null);

  const primaryText = sections[0]?.text ?? "";

  useLayoutEffect(() => {
    const pre = previewRef.current;
    if (!pre) return;
    const measure = () => setOverflowing(pre.scrollHeight > pre.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(pre);
    return () => observer.disconnect();
  }, []);

  if (sections.length === 0) return null;

  const primary = sections[0];
  const expandable = sections.length > 1 || overflowing;
  const taskCount = sections.length - 1;

  return (
    <>
      <section className="overflow-hidden rounded-md border border-border bg-card">
        <div className="flex items-center justify-between gap-3 border-b border-border/70 bg-wash-raised px-4 py-2.5">
          <span className="inline-flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
            <Icon.terminal className="size-3.5 shrink-0" />
            <span className="truncate">
              {primary.label}
              {taskCount > 0 ? ` · ${taskCount} task prompt${taskCount === 1 ? "" : "s"}` : ""}
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-2">
            <span className="text-xs tabular-nums text-muted-foreground">
              {wordCount(primary.text)} words
            </span>
            {expandable && (
              <button
                className="inline-flex h-6 items-center gap-1 rounded-sm border border-border/60 px-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => setOpen(true)}
                type="button"
              >
                <Icon.expand className="size-3" />
                View full
              </button>
            )}
          </span>
        </div>

        <div className="p-4">
          <pre
            className="line-clamp-4 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-foreground"
            ref={previewRef}
          >
            {primaryText}
          </pre>
        </div>
      </section>

      <PromptDialog onOpenChange={setOpen} open={open} sections={sections} />
    </>
  );
}
