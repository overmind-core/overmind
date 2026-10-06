import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";

export function QualityChip({ cell }: { cell: Cell }) {
  const reviewed = cell.readiness?.qualityReviewed;
  const passed = cell.readiness?.qualityPassed;
  const report = cell.qualityReport as {
    checks?: { name: string; result: string; evidence: string }[];
  } | null;
  return (
    <HoverCard>
      <HoverCardTrigger asChild>
        <button
          className={cn(
            "inline-flex h-6 shrink-0 items-center whitespace-nowrap rounded-sm border px-1.5 text-xs",
            passed
              ? "border-border text-muted-foreground"
              : "border-warning/40 bg-warning/10 text-warning"
          )}
          type="button"
        >
          {passed ? "Checks passed" : "Review recommended"}
        </button>
      </HoverCardTrigger>
      <HoverCardContent className="max-h-80 w-80 overflow-auto text-xs">
        <dl className="mb-3 grid grid-cols-2 gap-1">
          {Object.entries(cell.readiness?.assessment ?? {}).map(([name, value]) => (
            <div className="contents" key={name}>
              <dt className="capitalize">{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        {cell.readiness?.qualityReason && <p className="mb-2">{cell.readiness.qualityReason}</p>}
        {reviewed ? (
          <ul className="flex flex-col gap-2">
            {report?.checks?.map((check, index) => (
              <li key={`${check.name}-${index}`}>
                <p>
                  {check.name} · {check.result}
                </p>
                <p className="text-muted-foreground">{check.evidence}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p>No current quality review for this version and capability.</p>
        )}
        <p className="mt-2 text-muted-foreground">Model and context checks run before training.</p>
      </HoverCardContent>
    </HoverCard>
  );
}

export function ContaminationReport({ spec }: { spec: unknown }) {
  const report = (
    spec as {
      contamination_report?: {
        train_rows: number;
        eval_rows: number;
        duplicates_removed: number;
        content_overlap: number;
        group_overlap: number;
        group_by: string[];
        stratify_by: string | null;
      };
    } | null
  )?.contamination_report;
  if (!report) return null;
  return (
    <details className="mb-2 mr-2 rounded-md border border-border p-2.5 text-xs">
      <summary className="cursor-pointer">
        Data split · {report.train_rows} train · {report.eval_rows} eval
      </summary>
      <div className="mt-2 flex flex-col gap-1 text-muted-foreground">
        <p>
          {report.duplicates_removed} duplicates removed · {report.content_overlap} matching inputs
          across splits · {report.group_overlap} shared groups
        </p>
        <p>
          Groups: {report.group_by.join(", ") || "matching content"} · Stratification:{" "}
          {report.stratify_by ?? "none"}
        </p>
        <p>Source snapshot only. Near-duplicate similarity not checked.</p>
      </div>
    </details>
  );
}
