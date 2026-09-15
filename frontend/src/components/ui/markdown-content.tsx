import ReactMarkdown from "react-markdown";

import remarkGfm from "remark-gfm";

import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";

export interface MarkdownContentProps {
  children: string;
  compact?: boolean;
  className?: string;
}

/** Import the wrapper in `markdown.tsx`, not this module, so react-markdown and
 *  remark-gfm stay out of the initial bundle. The default export is React.lazy's. */
export default function MarkdownContentImpl({
  children,
  compact = false,
  className,
}: MarkdownContentProps) {
  return (
    <div className={cn(PROSE, compact ? "text-xs" : "text-sm leading-relaxed", className)}>
      <ReactMarkdown
        components={{
          a: ({ href, children }) => {
            const isExternal = !!href && (href.startsWith("http") || href.startsWith("//"));
            return (
              <a
                className="text-primary underline underline-offset-2 hover:text-primary/80"
                href={href}
                {...(isExternal ? { rel: "noopener noreferrer", target: "_blank" } : {})}
              >
                {children}
              </a>
            );
          },
          blockquote: ({ children }) => (
            <blockquote className="mb-2 border-l-2 border-muted-foreground/40 pl-3 text-muted-foreground last:mb-0">
              {children}
            </blockquote>
          ),
          code: ({ children, className }) => {
            const isBlock = className?.includes("language-");
            return isBlock ? (
              <code className="block overflow-x-auto rounded-sm bg-muted px-3 py-2 font-mono text-xs">
                {children}
              </code>
            ) : (
              <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-xs">{children}</code>
            );
          },
          em: ({ children }) => <em className="italic">{children}</em>,
          h1: ({ children }) => (
            <h1
              className={cn(
                "mb-2 mt-4 border-b border-border/70 pb-1 font-semibold tracking-tight first:mt-0",
                compact ? "text-xs" : "text-base"
              )}
            >
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2
              className={cn(
                "mb-1.5 mt-3 font-semibold first:mt-0",
                compact ? "text-xs" : "text-sm"
              )}
            >
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3
              className={cn(
                "mb-1 mt-2 font-semibold text-foreground first:mt-0",
                compact ? "text-xs" : "text-sm"
              )}
            >
              {children}
            </h3>
          ),
          hr: () => <hr className="my-3 border-border/60" />,
          li: ({ children }) => <li className="mb-1">{children}</li>,
          ol: ({ children }) => (
            <ol className="mb-3 list-decimal pl-5 last:mb-0 space-y-0.5">{children}</ol>
          ),
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          pre: ({ children }) => (
            <pre className="mb-2 overflow-x-auto rounded-md bg-wash-raised px-3 py-2 last:mb-0">
              {children}
            </pre>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-foreground">{children}</strong>
          ),
          table: ({ children }) => (
            <div className="mb-2 overflow-x-auto last:mb-0">
              <table className="w-full border-collapse text-xs">{children}</table>
            </div>
          ),
          td: ({ children }) => <td className="border border-border px-2 py-1">{children}</td>,
          th: ({ children }) => (
            <th className="border border-border bg-muted px-2 py-1 text-left font-semibold">
              {children}
            </th>
          ),
          ul: ({ children }) => (
            <ul className="mb-3 list-disc pl-5 last:mb-0 space-y-0.5">{children}</ul>
          ),
        }}
        remarkPlugins={[remarkGfm]}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
