import { type KeyboardEvent, type ReactNode, type UIEvent, useMemo, useRef } from "react";

import { cn } from "@/lib/utils";

type CellKind = "python" | "sql";

const SQL_KEYWORDS = new Set(
  "ALL AND AS ASC BETWEEN BY CASE CAST DESC DISTINCT ELSE END EXISTS FROM GROUP HAVING IN INNER IS JOIN LEFT LIKE LIMIT NOT NULL ON OR ORDER OUTER RIGHT SELECT THEN UNION USING WHEN WHERE WITH".split(
    " "
  )
);

const PY_KEYWORDS = new Set(
  "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield".split(
    " "
  )
);

const SQL_TOKEN_RE =
  /(--[^\n]*)|('(?:''|[^'])*'|"(?:""|[^"])*")|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_]\w*)\b/g;

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

function highlight(source: string, kind: CellKind): ReactNode[] {
  const re = kind === "sql" ? SQL_TOKEN_RE : PY_TOKEN_RE;
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const match of source.matchAll(re)) {
    const text = match[0];
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(source.slice(cursor, start));
    let tone: string | null = null;
    if (kind === "sql") {
      const [, comment, string, number, identifier] = match;
      tone = comment
        ? TONE.comment
        : string
          ? TONE.string
          : number
            ? TONE.number
            : identifier && SQL_KEYWORDS.has(identifier.toUpperCase())
              ? TONE.keyword
              : null;
    } else {
      const [, comment, string, number, decorator, identifier] = match;
      tone = comment
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
    }
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

/** A script with keyword highlighting: a transparent textarea over a painted backdrop. */
export function ScriptCode({
  source,
  kind = "python",
  onChange,
  onRun,
  onFocus,
  "aria-label": ariaLabel,
}: {
  source: string;
  kind?: CellKind;
  onChange?: (value: string) => void;
  onRun?: () => void;
  onFocus?: () => void;
  "aria-label"?: string;
}) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const nodes = useMemo(() => highlight(source, kind), [kind, source]);
  const lines = source.split("\n");
  const editable = onChange != null;

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      onRun?.();
      return;
    }
    if (event.key === "Tab" && editable) {
      event.preventDefault();
      const el = event.currentTarget;
      const { selectionStart, selectionEnd } = el;
      onChange(`${source.slice(0, selectionStart)}  ${source.slice(selectionEnd)}`);
      requestAnimationFrame(() => {
        el.selectionStart = el.selectionEnd = selectionStart + 2;
      });
    }
  };

  const handleScroll = (event: UIEvent<HTMLTextAreaElement>) => {
    const backdrop = backdropRef.current;
    if (!backdrop) return;
    backdrop.scrollTop = event.currentTarget.scrollTop;
    backdrop.scrollLeft = event.currentTarget.scrollLeft;
  };

  return (
    <div className="flex min-w-0 gap-3">
      <ol className={cn("w-5 shrink-0 select-none text-right text-muted-foreground/70", METRICS)}>
        {lines.map((_, index) => (
          <li key={index}>{index + 1}</li>
        ))}
      </ol>
      <div className="relative min-w-0 flex-1">
        {editable ? (
          <>
            <div
              aria-hidden
              className={cn(
                "pointer-events-none absolute inset-0 overflow-hidden whitespace-pre-wrap break-words text-foreground/90",
                METRICS
              )}
              ref={backdropRef}
            >
              {nodes}
              {source.endsWith("\n") ? " " : null}
            </div>
            <textarea
              aria-label={ariaLabel}
              className={cn(
                "relative block w-full resize-none overflow-hidden whitespace-pre-wrap break-words bg-transparent text-transparent caret-foreground outline-none field-sizing-content",
                METRICS
              )}
              onChange={(event) => onChange(event.target.value)}
              onFocus={onFocus}
              onKeyDown={handleKeyDown}
              onScroll={handleScroll}
              spellCheck={false}
              value={source}
            />
          </>
        ) : (
          <pre className={cn("whitespace-pre-wrap break-words text-foreground/90", METRICS)}>
            <code>{nodes}</code>
          </pre>
        )}
      </div>
    </div>
  );
}
