import { diffLines } from "diff";

import { cn } from "@/lib/utils";

interface PromptDiffProps {
  oldText: string;
  newText: string;
  className?: string;
}

export function PromptDiff({ oldText, newText, className }: PromptDiffProps) {
  const hunks = diffLines(oldText, newText, { newlineIsToken: false });

  const addedCount = hunks.filter((h) => h.added).reduce((n, h) => n + (h.count ?? 0), 0);
  const removedCount = hunks.filter((h) => h.removed).reduce((n, h) => n + (h.count ?? 0), 0);

  if (addedCount === 0 && removedCount === 0) {
    return (
      <div
        className={cn(
          "border border-border bg-muted/20 p-4 text-center text-xs text-muted-foreground",
          className
        )}
      >
        No changes from previous version.
      </div>
    );
  }

  // Build a line-number-aware view
  let oldLine = 1;
  let newLine = 1;

  const rows: {
    kind: "added" | "removed" | "context";
    oldNum: number | null;
    newNum: number | null;
    text: string;
  }[] = [];

  for (const hunk of hunks) {
    const lines = hunk.value.replace(/\n$/, "").split("\n");
    for (const line of lines) {
      if (hunk.added) {
        rows.push({ kind: "added", newNum: newLine++, oldNum: null, text: line });
      } else if (hunk.removed) {
        rows.push({ kind: "removed", newNum: null, oldNum: oldLine++, text: line });
      } else {
        rows.push({ kind: "context", newNum: newLine++, oldNum: oldLine++, text: line });
      }
    }
  }

  return (
    <div className={cn("overflow-hidden border border-border font-mono text-xs", className)}>
      {/* header */}
      <div className="flex items-center gap-3 border-b border-border bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground">
        <span className="text-green-600 dark:text-green-400">+{addedCount} added</span>
        <span className="text-red-500 dark:text-red-400">−{removedCount} removed</span>
      </div>

      {/* diff lines */}
      <div className="max-h-[360px] overflow-y-auto">
        <table className="w-full border-collapse">
          <tbody>
            {rows.map((row) => (
              <tr
                className={cn(
                  row.kind === "added" && "bg-green-500/10 dark:bg-green-400/10",
                  row.kind === "removed" && "bg-red-500/10 dark:bg-red-400/10"
                )}
                key={`${row.kind}-${row.oldNum ?? "n"}-${row.newNum ?? "n"}`}
              >
                {/* old line number */}
                <td className="w-8 select-none border-r border-border py-0.5 pr-2 text-right text-muted-foreground/60">
                  {row.oldNum ?? ""}
                </td>
                {/* new line number */}
                <td className="w-8 select-none border-r border-border py-0.5 pr-2 text-right text-muted-foreground/60">
                  {row.newNum ?? ""}
                </td>
                {/* gutter symbol */}
                <td
                  className={cn(
                    "w-5 select-none py-0.5 pl-2 pr-1 font-bold",
                    row.kind === "added" && "text-green-600 dark:text-green-400",
                    row.kind === "removed" && "text-red-500 dark:text-red-400",
                    row.kind === "context" && "text-muted-foreground/40"
                  )}
                >
                  {row.kind === "added" ? "+" : row.kind === "removed" ? "−" : " "}
                </td>
                {/* line content */}
                <td
                  className={cn(
                    "py-0.5 pl-1 pr-3 whitespace-pre-wrap break-all",
                    row.kind === "added" && "text-green-800 dark:text-green-200",
                    row.kind === "removed" && "text-red-800 dark:text-red-200",
                    row.kind === "context" && "text-foreground/80"
                  )}
                >
                  {row.text}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
