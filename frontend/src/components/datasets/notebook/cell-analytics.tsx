import { useId, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import type { DatasetAnalytics, DatasetAnalyticsCheck } from "@/openapi";

function tone(status: DatasetAnalyticsCheck["status"]) {
  return status === "error" ? "error" : status === "warning" ? "warning" : "neutral";
}

export function AnalyticsAlerts({ report }: { report?: DatasetAnalytics }) {
  if (report?.status !== "completed")
    return (
      <Badge size="chip" variant="neutral">
        Metrics not measured
      </Badge>
    );
  const alerts = report.checks.filter(
    (check) => check.status === "warning" || check.status === "error"
  );
  const unchecked = report.checks.filter((check) => check.status === "not_checked").length;
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1">
      {alerts.slice(0, 2).map((check) => (
        <Badge key={check.id} size="chip" title={check.summary} variant={tone(check.status)}>
          {check.title}
        </Badge>
      ))}
      {alerts.length > 2 && (
        <Badge size="chip" variant="warning">
          +{alerts.length - 2} alerts
        </Badge>
      )}
      {unchecked > 0 && (
        <Badge size="chip" variant="neutral">
          {unchecked} not checked
        </Badge>
      )}
    </span>
  );
}

function CheckGroup({
  title,
  checks,
  preview = false,
}: {
  title: string;
  checks: DatasetAnalyticsCheck[];
  preview?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!checks.length) return null;
  return (
    <section className="space-y-2">
      <Button
        aria-controls={id}
        aria-expanded={open}
        className="h-auto w-full justify-start whitespace-normal py-2 text-left text-xs"
        onClick={() => setOpen(!open)}
        variant="secondary"
      >
        <Icon.chevronRight className={open ? "size-3 rotate-90" : "size-3"} />
        {checks.length} {title}
      </Button>
      {preview && !open && (
        <p className="leading-relaxed text-muted-foreground">
          {checks.map((check) => check.title).join(" · ")}
        </p>
      )}
      <dl
        className="-mx-3 divide-y divide-border/60 border-y border-border/60"
        hidden={!open}
        id={id}
      >
        {checks.map((check) => (
          <div
            className="grid gap-1 px-3 py-2.5 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-4"
            key={check.id}
          >
            <dt className="text-foreground">{check.title}</dt>
            <dd className="max-w-prose break-words leading-relaxed text-muted-foreground">
              {check.summary}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Finding({ check, report }: { check: DatasetAnalyticsCheck; report: DatasetAnalytics }) {
  const familyIds = check.metrics?.families;
  const families =
    check.id === "targets.class_balance" && Array.isArray(familyIds)
      ? report.families.filter(
          (family) =>
            familyIds.includes(family.id) &&
            family.semantics === "categorical_gold" &&
            family.majorityBaseline != null &&
            family.counts &&
            family.counts.length > 1 &&
            family.counts.length === family.labels.length &&
            Math.min(...family.counts) > 0
        )
      : [];
  return (
    <section
      aria-label={check.title}
      className={`-mx-3 space-y-3 border-y px-3 py-4 ${
        check.status === "error"
          ? "border-destructive/40 bg-destructive/10"
          : "border-warning/40 bg-warning/10"
      }`}
    >
      <h3
        className={`flex items-start gap-2 text-base font-medium ${
          check.status === "error" ? "text-destructive" : "text-warning"
        }`}
      >
        <Icon.warning className="mt-0.5 size-4 shrink-0" />
        {families.length ? "Accuracy alone is misleading here" : check.title}
      </h3>
      {families.length ? (
        families.map((family) => {
          const counts = family.counts!;
          const majority = counts.indexOf(Math.max(...counts));
          const minority = counts.indexOf(Math.min(...counts));
          return (
            <div className="max-w-prose space-y-3 text-sm leading-relaxed" key={family.id}>
              <p>{family.question}</p>
              <p>
                Predicting “{family.labels[majority]}” for every row gets{" "}
                <strong className="font-semibold">
                  {(family.majorityBaseline! * 100).toFixed(2)}% accuracy
                </strong>{" "}
                while missing all {counts[minority].toLocaleString()} “{family.labels[minority]}”
                {counts[minority] === 1 ? " example" : " examples"}.
              </p>
              <p>
                <strong className="font-semibold">Before training:</strong> track precision and
                recall for “{family.labels[minority]}”, not accuracy alone.
              </p>
            </div>
          );
        })
      ) : (
        <p className="max-w-prose text-sm leading-relaxed">{check.summary}</p>
      )}
      {families.length > 0 && <p>Baseline illustration — not a trained model result.</p>}
    </section>
  );
}

export function AnalyticsDetails({ report }: { report?: DatasetAnalytics }) {
  if (report?.status !== "completed")
    return (
      <p className="px-3 pb-3 text-xs text-muted-foreground">
        This version has not been scanned. Ask the agent to run dataset analytics in Overmind.
      </p>
    );
  const alerts = report.checks
    .filter((check) => check.status === "warning" || check.status === "error")
    .sort((a, b) => Number(b.status === "error") - Number(a.status === "error"));
  const unchecked = report.checks.filter((check) => check.status === "not_checked");
  const completed = report.checks.filter((check) => check.status === "pass");
  const weighted = report.checks.some(
    (check) => check.id === "targets.weights" && check.status === "warning"
  );
  return (
    <div aria-label="Dataset analytics" className="nodrag nowheel space-y-5 px-3 pb-3 text-xs">
      {alerts.map((check) => (
        <Finding check={check} key={check.id} report={report} />
      ))}
      {report.families.map((family) => {
        const majority = family.counts ? family.counts.indexOf(Math.max(...family.counts)) : -1;
        const showMass = !family.counts || weighted;
        return (
          <section className="space-y-2" key={family.id}>
            <h3 className="break-words font-medium text-foreground">{family.question}</h3>
            <div className="-mx-3 overflow-x-auto">
              <table className="w-full text-left tabular-nums">
                <caption className="sr-only">Target distribution for {family.question}</caption>
                <thead className="text-muted-foreground">
                  <tr className="border-b border-border/70">
                    <th className="px-3 py-1.5 font-normal" scope="col">
                      Label
                    </th>
                    <th className="px-3 py-1.5 text-right font-normal" scope="col">
                      Examples
                    </th>
                    <th className="px-3 py-1.5 text-right font-normal" scope="col">
                      Share
                    </th>
                    {showMass && (
                      <th className="px-3 py-1.5 text-right font-normal" scope="col">
                        Weighted target total
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {family.labels.map((label, index) => (
                    <tr className="border-b border-border/60" key={`${family.id}-${index}`}>
                      <th className="max-w-48 break-words px-3 py-2 font-normal" scope="row">
                        {label}
                      </th>
                      <td className="px-3 py-2 text-right">
                        {family.counts?.[index].toLocaleString() ?? "—"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {family.counts
                          ? `${((100 * family.counts[index]) / family.rows).toFixed(2)}%`
                          : "—"}
                      </td>
                      {showMass && (
                        <td className="px-3 py-2 text-right">
                          {family.targetMass?.[index].toLocaleString() ?? "—"}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {family.majorityBaseline != null && majority >= 0 ? (
              !alerts.some(
                (check) =>
                  check.id === "targets.class_balance" &&
                  Array.isArray(check.metrics?.families) &&
                  check.metrics.families.includes(family.id)
              ) && (
                <p className="leading-relaxed">
                  Always predicting “{family.labels[majority]}” scores{" "}
                  {(family.majorityBaseline * 100).toFixed(2)}% accuracy.
                  <span className="ml-1 text-muted-foreground">
                    Baseline only, not a trained model result.
                  </span>
                </p>
              )
            ) : (
              <p className="leading-relaxed text-muted-foreground">
                {family.semantics === "unknown"
                  ? "Target meaning is undeclared. Label counts and an accuracy baseline cannot be reported."
                  : "These targets are distributions or numeric means, not observed labels. No label counts or accuracy baseline were inferred."}
              </p>
            )}
            {family.labelsTruncated && (
              <p className="text-muted-foreground">
                Long labels are abbreviated; their original order is preserved.
              </p>
            )}
          </section>
        );
      })}
      <CheckGroup checks={unchecked} preview title="checks not assessed" />
      <CheckGroup checks={completed} title="completed checks" />
      <div className="space-y-1 text-muted-foreground" title={report.fingerprint}>
        <p>
          {report.rowsScanned?.toLocaleString()} rows scanned · Entire dataset
          {report.measuredAt && <> · {report.measuredAt.toLocaleString()}</>}
        </p>
        <p>
          Measured by Overmind · Checks v{report.version} · Data fingerprint{" "}
          {report.fingerprint.slice(0, 12)}
        </p>
      </div>
    </div>
  );
}
