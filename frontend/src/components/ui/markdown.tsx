import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

export function isLikelyMarkdown(text: string): boolean {
  return /^#{1,6}\s|^\s*[-*+]\s|\*\*|__|\[.+\]\(|^```|^>/m.test(text);
}

/**
 * Renders markdown content with consistent styling.
 * @param compact - adds `text-xs` to prose elements (p, ul, ol, blockquote).
 *                  Use for dense UI contexts like trace panels.
 */
export function MarkdownContent({
  children,
  compact = false,
}: {
  children: string;
  compact?: boolean;
}) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children }) => (
          <a
            className="text-primary underline underline-offset-2 hover:text-primary/80"
            href={href}
            rel="noopener noreferrer"
            target="_blank"
          >
            {children}
          </a>
        ),
        blockquote: ({ children }) => (
          <blockquote
            className={cn(
              "mb-2 border-l-2 border-muted-foreground/40 pl-3 text-muted-foreground last:mb-0",
              compact && "text-xs",
            )}
          >
            {children}
          </blockquote>
        ),
        code: ({ children, className }) => {
          const isBlock = className?.includes("language-");
          return isBlock ? (
            <code className="block overflow-x-auto rounded bg-muted px-3 py-2 font-mono text-xs">
              {children}
            </code>
          ) : (
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">{children}</code>
          );
        },
        em: ({ children }) => <em className="italic">{children}</em>,
        h1: ({ children }) => (
          <h1 className="mb-2 mt-4 border-b border-border/50 pb-1 text-sm font-bold first:mt-0">
            {children}
          </h1>
        ),
        h2: ({ children }) => (
          <h2 className="mb-1.5 mt-3 text-sm font-semibold first:mt-0">{children}</h2>
        ),
        h3: ({ children }) => (
          <h3 className="mb-1 mt-2 text-xs font-semibold uppercase tracking-wide text-foreground/70 first:mt-0">
            {children}
          </h3>
        ),
        hr: () => <hr className="my-2 border-border" />,
        li: ({ children }) => <li className="mb-0.5">{children}</li>,
        ol: ({ children }) => (
          <ol className={cn("mb-2 list-decimal pl-4 last:mb-0", compact && "text-xs")}>
            {children}
          </ol>
        ),
        p: ({ children }) => (
          <p className={cn("mb-2 last:mb-0", compact && "text-xs leading-relaxed")}>{children}</p>
        ),
        pre: ({ children }) => (
          <pre className="mb-2 overflow-x-auto rounded-lg bg-muted/70 px-3 py-2 last:mb-0">
            {children}
          </pre>
        ),
        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
        table: ({ children }) => (
          <div className="mb-2 overflow-x-auto last:mb-0">
            <table className="w-full border-collapse text-xs">{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th className="border border-border bg-muted px-2 py-1 text-left font-semibold">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="border border-border px-2 py-1">{children}</td>
        ),
        ul: ({ children }) => (
          <ul className={cn("mb-2 list-disc pl-4 last:mb-0", compact && "text-xs")}>
            {children}
          </ul>
        ),
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
