import { type ReactNode, useMemo } from "react";

import { cn } from "@/lib/utils";

const PY_KEYWORDS = new Set(
  "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield".split(
    " "
  )
);

const PY_TOKEN_RE =
  /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|\b(\d+(?:\.\d+)?)\b|(@\w+)|\b([A-Za-z_]\w*)\b/g;

const TONE = {
  comment: "italic text-muted-foreground/80",
  decorator: "text-warning",
  keyword: "text-info",
  number: "text-warning",
  string: "text-foreground",
} as const;

const METRICS = "font-mono text-xs leading-5";

function highlight(source: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const match of source.matchAll(PY_TOKEN_RE)) {
    const text = match[0];
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(source.slice(cursor, start));
    const [, comment, string, number, decorator, identifier] = match;
    const tone = comment
      ? TONE.comment
      : string
        ? TONE.string
        : number
          ? TONE.number
          : decorator
            ? TONE.decorator
            : identifier && PY_KEYWORDS.has(identifier)
              ? TONE.keyword
              : null;
    nodes.push(
      tone ? (
        <span className={tone} key={key++}>
          {text}
        </span>
      ) : (
        text
      )
    );
    cursor = start + text.length;
  }
  if (cursor < source.length) nodes.push(source.slice(cursor));
  return nodes;
}

/** A version's script, read-only. `nodrag nopan nowheel` keep the canvas from
 *  taking selection, drags and scrolls inside it. */
export function ScriptCode({
  source,
  "aria-label": ariaLabel,
}: {
  source: string;
  "aria-label": string;
}) {
  const nodes = useMemo(() => highlight(source), [source]);
  const lines = source.split("\n");
  return (
    <section
      aria-label={ariaLabel}
      className="nodrag nopan nowheel flex max-h-80 min-w-0 cursor-text gap-3 overflow-auto overscroll-contain"
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must take keyboard focus
      tabIndex={0}
    >
      <ol className={cn("w-5 shrink-0 select-none text-right text-muted-foreground/70", METRICS)}>
        {lines.map((_, index) => (
          <li key={index}>{index + 1}</li>
        ))}
      </ol>
      <pre
        className={cn("min-w-0 flex-1 whitespace-pre-wrap break-words text-foreground/90", METRICS)}
      >
        <code>{nodes}</code>
      </pre>
    </section>
  );
}
