import { lazy, Suspense } from "react";

import type { MarkdownContentProps } from "@/components/ui/markdown-content";
import { withChunkReloadRetry } from "@/lib/chunk-error";
import { cn } from "@/lib/utils";

export function isLikelyMarkdown(text: string): boolean {
  // `__` is excluded deliberately: it false-positives on Python dunders (__init__) and
  // technical identifiers, and `\*\*` already covers bold.
  return /^#{1,6}\s|^\s*[-*+]\s|^\s*\d+\.\s|^\s*\|.+\|\s*$|\*\*|\[.+\]\(|^```|^>/m.test(text);
}

// react-markdown + remark-gfm are ~160KB and otherwise land in the initial bundle. This
// module stays dep-free so `isLikelyMarkdown` and its call sites remain light.
const LazyMarkdown = lazy(withChunkReloadRetry(() => import("@/components/ui/markdown-content")));

export function MarkdownContent(props: MarkdownContentProps) {
  const { children, compact, className } = props;
  return (
    <Suspense
      fallback={
        <div
          className={cn(
            "whitespace-pre-wrap text-muted-foreground",
            compact ? "text-xs" : "text-sm leading-relaxed",
            className
          )}
        >
          {children}
        </div>
      }
    >
      <LazyMarkdown {...props} />
    </Suspense>
  );
}
