import { type ComponentProps, useRef } from "react";

import { cn } from "@/lib/utils";

export type PromptSegment = { type: "text" | "chip"; value: string };

// Requires the closing braces, so a half-typed `{{inp` stays plain text.
const TOKEN_RE = /\{\{[^{}]*\}\}/g;

export function tokenizePrompt(text: string): PromptSegment[] {
  const segments: PromptSegment[] = [];
  let last = 0;
  for (const match of text.matchAll(TOKEN_RE)) {
    const start = match.index;
    if (start > last) segments.push({ type: "text", value: text.slice(last, start) });
    segments.push({ type: "chip", value: match[0] });
    last = start + match[0].length;
  }
  if (last < text.length) segments.push({ type: "text", value: text.slice(last) });
  return segments;
}

// Must stay byte-for-byte identical between textarea and backdrop, or the
// invisible caret drifts. Chips may paint colour only, never padding or size.
const TEXT_METRICS = "px-3 py-2 font-mono text-xs leading-relaxed";

type PromptTemplateEditorProps = {
  value: string;
  onChange: (value: string) => void;
  className?: string;
} & Omit<ComponentProps<"textarea">, "value" | "onChange" | "className">;

/** Transparent textarea over an aria-hidden backdrop that paints the tokens:
 * editing stays native and the value stays the raw `{{...}}` string. Border and
 * fill live on the wrapper so the overlay cannot paint past them. */
export function PromptTemplateEditor({
  value,
  onChange,
  className,
  ...textareaProps
}: PromptTemplateEditorProps) {
  const backdropRef = useRef<HTMLDivElement>(null);

  // field-sizing-content removes internal scroll in the common case; this covers the rest.
  const syncScroll = (e: React.UIEvent<HTMLTextAreaElement>) => {
    const backdrop = backdropRef.current;
    if (!backdrop) return;
    backdrop.scrollTop = e.currentTarget.scrollTop;
    backdrop.scrollLeft = e.currentTarget.scrollLeft;
  };

  const segments = tokenizePrompt(value);

  return (
    <div
      className={cn(
        "relative w-full min-w-0 max-w-full overflow-hidden rounded-md border border-input bg-input/30 transition-[color,box-shadow] focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50",
        className
      )}
    >
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-0 overflow-hidden break-words whitespace-pre-wrap text-foreground",
          TEXT_METRICS
        )}
        ref={backdropRef}
      >
        {segments.map((seg, i) =>
          seg.type === "chip" ? (
            <span className="rounded-sm bg-primary/15 font-medium text-primary" key={i}>
              {seg.value}
            </span>
          ) : (
            <span key={i}>{seg.value}</span>
          )
        )}
        {/* A div collapses the final empty line a textarea keeps; pad it so the
            caret stays aligned on the last row. */}
        {value.endsWith("\n") ? " " : null}
      </div>
      <textarea
        className={cn(
          "relative block min-h-40 w-full min-w-0 max-w-full resize-y overflow-x-hidden break-words whitespace-pre-wrap border-0 bg-transparent text-transparent caret-foreground outline-none field-sizing-content disabled:cursor-not-allowed disabled:opacity-50",
          TEXT_METRICS
        )}
        data-slot="prompt-template-editor"
        onChange={(e) => onChange(e.target.value)}
        onScroll={syncScroll}
        spellCheck={false}
        value={value}
        {...textareaProps}
      />
    </div>
  );
}
