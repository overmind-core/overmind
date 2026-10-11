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
