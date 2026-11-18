import { Field } from "@/components/datasets/notebook/field";

/** The split landing made, as measured counts. */
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
      };
    } | null
  )?.contamination_report;
  if (!report) return null;
  return (
    <div aria-label="Data split" className="mb-2 mr-2 flex flex-wrap gap-2">
      <Field label="Train">{report.train_rows.toLocaleString()} rows</Field>
      <Field label="Eval">{report.eval_rows.toLocaleString()} rows</Field>
      <Field label="Duplicates removed">{report.duplicates_removed.toLocaleString()}</Field>
      <Field label="Shared across splits">
        {(report.content_overlap + report.group_overlap).toLocaleString()}
      </Field>
      {report.group_by.length > 0 && (
        <Field label="Grouped by">
          <span className="font-mono">{report.group_by.join(", ")}</span>
        </Field>
      )}
    </div>
  );
}
