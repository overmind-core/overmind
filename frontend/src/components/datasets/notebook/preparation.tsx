import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import type { Cell } from "@/openapi";

interface Review {
  rows_before: number;
  rows_after: number;
  rows_removed?: number;
  generated_rows?: number;
  coverage_before?: Record<string, Record<string, number>>;
  coverage_after?: Record<string, Record<string, number>>;
  removed_examples?: unknown[];
  generated_examples?: unknown[];
  output_examples?: unknown[];
  input_examples?: unknown[];
}

export function ProposalImpact({ cell }: { cell: Cell }) {
  const review = cell.review as Review | null;
  if (!review || typeof review.rows_before !== "number") return null;
  const examples =
    review.generated_examples ??
    (review.rows_removed ? review.removed_examples : review.output_examples);
  return (
    <div className="flex flex-col gap-2 text-xs">
      <p className="tabular-nums">
        {review.rows_before.toLocaleString()} → {review.rows_after.toLocaleString()} rows
        {review.generated_rows
          ? ` · ${review.generated_rows} generated`
          : review.rows_removed
            ? ` · ${review.rows_removed} excluded`
            : ""}
      </p>
      {Object.entries(review.coverage_before ?? {}).map(([column, before]) => {
        const after = review.coverage_after?.[column] ?? {};
        return (
          <details key={column}>
            <summary className="cursor-pointer text-muted-foreground">{column} coverage</summary>
            <table className="mt-1 w-full text-left tabular-nums">
              <thead>
                <tr>
                  <th>Value</th>
                  <th>Before</th>
                  <th>After</th>
                </tr>
              </thead>
              <tbody>
                {[...new Set([...Object.keys(before), ...Object.keys(after)])].map((value) => (
                  <tr key={value}>
                    <td className="break-all">{value}</td>
                    <td>{before[value] ?? 0}</td>
                    <td>{after[value] ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        );
      })}
      {!!examples?.length && (
        <details>
          <summary className="cursor-pointer text-muted-foreground">
            {review.generated_rows ? "Generated" : review.rows_removed ? "Excluded" : "Output"}{" "}
            examples
          </summary>
          <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-sm border border-border bg-background p-2">
            {JSON.stringify(examples, null, 2)}
          </pre>
        </details>
      )}
      {!!review.input_examples?.length && !review.generated_rows && (
        <details>
          <summary className="cursor-pointer text-muted-foreground">Input examples</summary>
          <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-sm border border-border bg-background p-2">
            {JSON.stringify(review.input_examples, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

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
          className={
            passed
              ? "h-6 rounded-sm border border-border px-1.5 text-xs text-muted-foreground"
              : "h-6 rounded-sm border border-warning/40 bg-warning/10 px-1.5 text-xs text-warning"
          }
          type="button"
        >
          {passed ? "Quality passed" : "Review recommended"}
        </button>
      </HoverCardTrigger>
      <HoverCardContent className="max-h-80 w-80 overflow-auto text-xs">
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
