import { Link } from "@tanstack/react-router";

import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Icon } from "@/components/ui/icons";

interface SplitSpec {
  split?: { role?: "train" | "eval"; sibling?: string };
  contamination_report?: {
    train_rows: number;
    eval_rows: number;
    duplicates_removed: number;
    content_overlap: number;
    group_overlap: number;
    group_by: string[];
    stratify_by: string | null;
  };
}

const rows = (n: number) => `${n.toLocaleString()} ${n === 1 ? "row" : "rows"}`;

export const isSplit = (spec: unknown) => !!(spec as SplitSpec | null)?.contamination_report;

export function SplitSummary({ spec, projectId }: { spec: unknown; projectId: string }) {
  const { split, contamination_report: report } = (spec ?? {}) as SplitSpec;
  if (!report) return null;
  const sibling = split?.sibling;
  const otherLabel = split?.role === "eval" ? "Training set" : "Evaluation set";
  const overlap = report.content_overlap + report.group_overlap;
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
      <Icon.gitBranch className="size-3.5 shrink-0" />
      <span className="hidden truncate sm:inline">
        Split · {report.train_rows.toLocaleString()} training, {report.eval_rows.toLocaleString()}{" "}
        evaluation rows
      </span>
      <HoverCard openDelay={150}>
        <HoverCardTrigger asChild>
          <button
            aria-label="Split details"
            className="inline-flex shrink-0 items-center rounded-xs p-0.5 hover:bg-wash-raised hover:text-foreground"
            type="button"
          >
            <Icon.info className="size-3.5" />
          </button>
        </HoverCardTrigger>
        <HoverCardContent align="start" className="w-80 p-3 text-xs">
          <ul className="flex flex-col gap-1.5 text-muted-foreground">
            <li>
              {report.duplicates_removed
                ? `${rows(report.duplicates_removed)} removed as duplicates.`
                : "No duplicates found."}
            </li>
            <li>
              {overlap
                ? `${rows(overlap)} share an input or group with the other set.`
                : "No input or group appears in both sets."}
            </li>
            {report.group_by.length > 0 && (
              <li>Rows with the same {report.group_by.join(", ")} stay in one set.</li>
            )}
            {report.stratify_by && <li>Both sets keep the mix of {report.stratify_by}.</li>}
            <li>Near-duplicates are not checked.</li>
          </ul>
        </HoverCardContent>
      </HoverCard>
      {sibling && (
        <Link
          className="inline-flex shrink-0 items-center gap-1 text-foreground hover:underline"
          params={{ datasetId: sibling }}
          search={{ projectId }}
          to="/datasets/$datasetId"
        >
          {otherLabel}
          <Icon.forward className="size-3" />
        </Link>
      )}
    </span>
  );
}
