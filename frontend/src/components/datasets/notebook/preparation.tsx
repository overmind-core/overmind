import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { cn } from "@/lib/utils";
import type { Cell, PreparationPlan } from "@/openapi";

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

export function PreparationPlanDetails({ plan }: { plan?: PreparationPlan | null }) {
  if (!plan) return null;
  const spec = plan.specification;
  return (
    <details className="mb-2 mr-2 rounded-md border border-border p-2.5 text-xs">
      <summary className="cursor-pointer">
        Preparation plan · {spec.steps.length} steps
        {plan.stale ? " · Source or intent changed" : ""}
      </summary>
      <div className="mt-3 flex flex-col gap-3">
        <p>{spec.objective}</p>
        <p className="text-muted-foreground">{spec.understanding}</p>
        <p>
          Consumer: {spec.consumer.replaceAll("_", " ")} · Source: {plan.sourceCell}
        </p>
        <div className="flex flex-col gap-2">
          {spec.families.map((family) => (
            <div key={family.name}>
              <p className="font-medium">{family.name}</p>
              <p className="text-muted-foreground">{family.evidence}</p>
              <p>Inputs: {family.inputColumns.join(", ") || "Unknown"}</p>
              <p>Targets: {family.targetColumns.join(", ") || "Unknown"}</p>
              <p>Groups: {family.groupColumns.join(", ") || "Undeclared"}</p>
              <p>Coverage: {family.coverageColumns?.join(", ") || "Undeclared"}</p>
            </div>
          ))}
        </div>
        {!!Object.keys(spec.mapping).length && (
          <table className="w-full text-left">
            <caption className="mb-1 text-left font-medium">Field mapping</caption>
            <thead>
              <tr>
                <th>Source</th>
                <th>Consumer field</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(spec.mapping).map(([target, source]) => (
                <tr key={target}>
                  <td className="break-all pr-3">{source}</td>
                  <td className="break-all">{target}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!!Object.keys(spec.constants).length && (
          <pre className="whitespace-pre-wrap break-all">
            Constants: {JSON.stringify(spec.constants)}
          </pre>
        )}
        <ol className="list-inside list-decimal space-y-1">
          {spec.steps.map((step) => {
            const executions = plan.executions?.filter((item) => item.stepId === step.id) ?? [];
            return (
              <li key={step.id}>
                {step.description} ·{" "}
                {executions.length
                  ? executions.map((item) => `${item.state} (${item.rows} rows)`).join(", ")
                  : step.kind === "audit"
                    ? "Audit"
                    : step.kind === "inspect"
                      ? "Inspection"
                      : "No saved cell"}
              </li>
            );
          })}
        </ol>
        <ul className="space-y-1">
          {spec.checks.map((check) => (
            <li key={check.name}>
              {check.category} · {check.method}: {check.question}
            </li>
          ))}
        </ul>
        <p>
          Semantic audit budget: {spec.semanticRowBudget} rows · {plan.semanticRowsReserved ?? 0}{" "}
          reserved
        </p>
        {!!spec.assumptions.length && (
          <div>
            <p className="font-medium">Assumptions</p>
            <ul className="mt-1 list-inside list-disc">
              {spec.assumptions.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
        {!!spec.unresolvedQuestions.length && (
          <div>
            <p className="font-medium">Unresolved</p>
            <ul className="mt-1 list-inside list-disc">
              {spec.unresolvedQuestions.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
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
