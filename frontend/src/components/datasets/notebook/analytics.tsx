interface LabelBalance {
  field: string;
  rows: number;
  labels: number;
  classes: Array<{ label: string; rows: number }>;
  majority: { label: string; share: number };
}

interface FieldCount {
  field: string;
  rows: number;
  of: number;
}

export interface Analytics {
  labels?: LabelBalance[];
  inputs?: { empty?: FieldCount[]; constant?: FieldCount[] };
}

const count = (n: number) => n.toLocaleString();
const percent = (share: number) => `${Math.round(share * 100)}%`;
const share = (part: number, whole: number) =>
  whole ? `${((100 * part) / whole).toFixed(1)}%` : "—";
const fieldName = (field: string) => field.replace(/^expected_output\./, "");

interface Warning {
  key: string;
  field: string;
  detail: string;
}

function warningsOf(analytics: Analytics): Warning[] {
  const inputs = analytics.inputs ?? {};
  return [
    ...(inputs.empty ?? []).map((item) => ({
      detail: `Empty in ${count(item.rows)} of ${count(item.of)} rows`,
      field: item.field,
      key: `empty-${item.field}`,
    })),
    ...(inputs.constant ?? []).map((item) => ({
      detail: `One value in ${count(item.rows)} of ${count(item.of)} rows`,
      field: item.field,
      key: `constant-${item.field}`,
    })),
  ];
}

export function sameAnalytics(a: Analytics, b: Analytics): boolean {
  const shown = (analytics: Analytics) =>
    JSON.stringify([analytics.labels ?? [], warningsOf(analytics)]);
  return shown(a) === shown(b);
}

export function hasAnalytics(analytics: Analytics): boolean {
  return !!analytics.labels?.length || warningsOf(analytics).length > 0;
}

export function AnalyticsSummary({ analytics }: { analytics: Analytics }) {
  const labels = analytics.labels ?? [];
  const warnings = warningsOf(analytics).length;
  const parts: Array<{ key: string; text: string; warning?: boolean }> = [];
  if (labels.length === 1) {
    const [balance] = labels;
    parts.push({
      key: "labels",
      text: `${fieldName(balance.field)} · ${balance.majority.label} ${percent(balance.majority.share)}`,
    });
  } else if (labels.length > 1) {
    parts.push({ key: "labels", text: `${labels.length} label fields` });
  }
  if (warnings)
    parts.push({
      key: "warnings",
      text: `${warnings} ${warnings === 1 ? "field warning" : "field warnings"}`,
      warning: true,
    });
  return parts.map((part, index) => (
    <span key={part.key}>
      {index > 0 && <span aria-hidden> · </span>}
      <span className={part.warning ? "text-warning" : undefined}>{part.text}</span>
    </span>
  ));
}

const HEAD = "px-3 py-1.5 font-normal";
const CELL = "px-3 py-1.5";

export function AnalyticsDetails({ analytics }: { analytics: Analytics }) {
  const labels = analytics.labels ?? [];
  const warnings = warningsOf(analytics);
  return (
    <div
      aria-label="Analytics"
      className="nodrag nowheel flex flex-col gap-4 px-3 pt-1 pb-3 text-xs"
    >
      {labels.map((balance) => {
        const shown = balance.classes.reduce((sum, item) => sum + item.rows, 0);
        const others = balance.labels - balance.classes.length;
        return (
          <section className="flex flex-col gap-1.5" key={balance.field}>
            <h3 className="font-mono text-foreground">{fieldName(balance.field)}</h3>
            <div className="-mx-3">
              <table className="w-full table-fixed text-left tabular-nums">
                <caption className="sr-only">Labels of {fieldName(balance.field)}</caption>
                <colgroup>
                  <col />
                  <col className="w-28" />
                  <col className="w-28" />
                </colgroup>
                <thead className="text-muted-foreground">
                  <tr className="border-b border-border/70">
                    <th className={HEAD} scope="col">
                      Label
                    </th>
                    <th className={`${HEAD} text-right`} scope="col">
                      Rows
                    </th>
                    <th className={`${HEAD} text-right`} scope="col">
                      Share
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {balance.classes.map((item) => (
                    <tr className="border-b border-border/60" key={item.label}>
                      <th
                        className={`${CELL} max-w-48 truncate font-mono font-normal text-foreground`}
                        scope="row"
                        title={item.label}
                      >
                        {item.label}
                      </th>
                      <td className={`${CELL} text-right`}>{count(item.rows)}</td>
                      <td className={`${CELL} text-right`}>{share(item.rows, balance.rows)}</td>
                    </tr>
                  ))}
                  {others > 0 && (
                    <tr className="border-b border-border/60 text-muted-foreground">
                      <th className={`${CELL} font-normal`} scope="row">
                        {others} other {others === 1 ? "label" : "labels"}
                      </th>
                      <td className={`${CELL} text-right`}>{count(balance.rows - shown)}</td>
                      <td className={`${CELL} text-right`}>
                        {share(balance.rows - shown, balance.rows)}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <p className="text-muted-foreground">
              Majority baseline{" "}
              <span className="font-mono text-foreground">{balance.majority.label}</span>{" "}
              <span className="tabular-nums text-foreground">
                {percent(balance.majority.share)}
              </span>
            </p>
          </section>
        );
      })}
      {warnings.length > 0 && (
        <section className="flex flex-col gap-1.5">
          <h3 className="text-foreground">Field warnings</h3>
          <div className="-mx-3">
            <table className="w-full border-t border-border/70 text-left tabular-nums">
              <caption className="sr-only">Field warnings</caption>
              <tbody>
                {warnings.map((warning) => (
                  <tr className="border-b border-border/60" key={warning.key}>
                    <th
                      className={`${CELL} max-w-48 truncate font-mono font-normal text-foreground`}
                      scope="row"
                      title={warning.field}
                    >
                      {warning.field}
                    </th>
                    <td className={`${CELL} text-right text-warning`}>{warning.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
