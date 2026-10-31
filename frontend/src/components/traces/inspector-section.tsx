import { type ReactNode, useId, useState } from "react";

import { BlockActions } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Icon } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function InspectorSection({
  actions,
  badge,
  children,
  id,
  label,
  defaultOpen = true,
}: {
  actions?: ReactNode;
  badge?: ReactNode;
  children: ReactNode;
  id?: string;
  label: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const SectionIcon =
    label === "Input"
      ? Icon.forward
      : label === "Output"
        ? Icon.back
        : label === "Attributes"
          ? Icon.code
          : Icon.list;
  return (
    <Collapsible asChild onOpenChange={setOpen} open={open}>
      <section className="min-w-0 py-1" id={id}>
        <div className="flex min-h-8 items-center gap-2">
          <CollapsibleTrigger asChild>
            <button
              aria-label={`${open ? "Collapse" : "Expand"} ${label}`}
              className="flex min-w-0 flex-1 items-center gap-2 py-1 text-left text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              type="button"
            >
              <span className="flex size-5 shrink-0 items-center justify-center rounded-sm bg-muted text-muted-foreground">
                <SectionIcon className="size-3.5" />
              </span>
              <span className="break-words">{label}</span>
              {badge}
              <Icon.chevronRight
                aria-hidden
                className={cn(
                  "ml-auto size-3.5 shrink-0 text-muted-foreground",
                  open && "rotate-90"
                )}
              />
            </button>
          </CollapsibleTrigger>
          {open && actions}
        </div>
        <CollapsibleContent className="mt-1 min-w-0 pb-2">{children}</CollapsibleContent>
      </section>
    </Collapsible>
  );
}

export function CodeTokens({ text }: { text: string }) {
  return text
    .split(
      /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:true|false|null|True|False|None|await|raise|return|async|def|in)\b|\b\d+(?:\.\d+)?\b)/g
    )
    .map((part, index) => (
      <span
        className={
          /^['"]/.test(part)
            ? "text-cat-2"
            : /^(true|false|null|True|False|None|await|raise|return|async|def|in)$/.test(part)
              ? "text-cat-1"
              : /^\d/.test(part)
                ? "text-cat-4"
                : undefined
        }
        key={index}
      >
        {part}
      </span>
    ));
}

export function StackTraceContent({ label, text }: { label: string; text: string }) {
  const lines = text.split("\n");
  const isPython = text.includes("Traceback (most recent call last):");
  const LanguageIcon = isPython ? Icon.python : Icon.code;
  return (
    <ExpandableContent
      label={label}
      render={(content, toggle) => (
        <Card className="min-w-0 overflow-hidden bg-wash-subtle">
          <div className="flex items-center justify-between gap-2 border-b border-border bg-background px-4 py-2">
            <span className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
              <LanguageIcon className="size-4" />
              {isPython ? "python" : "Stack trace"}
            </span>
            <div className="flex items-center gap-1">
              {toggle}
              <BlockActions
                mode="raw"
                onToggleMode={() => undefined}
                showToggle={false}
                text={text}
              />
            </div>
          </div>
          <div className="p-3">{content}</div>
        </Card>
      )}
      text={text}
    >
      <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5 [counter-reset:line]">
        <code>
          {lines.map((line, index) => (
            <span
              className="relative block pl-9 [counter-increment:line] before:absolute before:left-0 before:w-6 before:select-none before:text-right before:text-muted-foreground before:content-[counter(line)]"
              key={index}
            >
              <CodeTokens text={line} />
              {index < lines.length - 1 ? "\n" : ""}
            </span>
          ))}
        </code>
      </pre>
    </ExpandableContent>
  );
}

export function ExpandableContent({
  children,
  text,
  label,
  render,
}: {
  children: ReactNode;
  text: string;
  label: string;
  render?: (content: ReactNode, toggle: ReactNode) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const long = text.length > 900 || text.split("\n").length > 12;
  const toggle = long ? (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          aria-controls={id}
          aria-expanded={expanded}
          aria-label={`${expanded ? "Collapse" : "Expand"} ${label}`}
          onClick={() => setExpanded(!expanded)}
          size="icon-xs"
          variant="ghost"
        >
          {expanded ? <Icon.collapse /> : <Icon.expand />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {expanded ? "Collapse" : "Expand"} {label}
      </TooltipContent>
    </Tooltip>
  ) : null;
  const content = (
    <div className={cn("min-w-0", long && !expanded && "max-h-56 overflow-hidden")} id={id}>
      {children}
    </div>
  );
  if (render) return render(content, toggle);
  return (
    <div className="min-w-0">
      {toggle && <div className="mb-1 flex justify-end">{toggle}</div>}
      {content}
    </div>
  );
}
