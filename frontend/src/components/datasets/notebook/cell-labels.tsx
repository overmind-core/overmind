import type { Cell } from "@/openapi";

/** How a decision version's rows spread over each question's labels. */
export function CellLabels({ cell }: { cell: Cell }) {
  const families = cell.analytics?.status === "completed" ? cell.analytics.families : [];
  return (
    <div aria-label="Labels" className="nodrag nowheel space-y-4 px-3 pb-3 text-xs">
      {families.map((family) => (
        <section className="space-y-2" key={family.id}>
          <h3 className="line-clamp-2 break-words text-foreground" title={family.question}>
            {family.question}
          </h3>
          <div className="-mx-3 overflow-x-auto">
            <table className="w-full text-left tabular-nums">
              <caption className="sr-only">Labels for {family.question}</caption>
              <thead className="text-muted-foreground">
                <tr className="border-b border-border/70">
                  <th className="px-3 py-1.5 font-normal" scope="col">
                    Label
                  </th>
                  <th className="px-3 py-1.5 text-right font-normal" scope="col">
                    Rows
                  </th>
                  <th className="w-40 px-3 py-1.5 text-right font-normal" scope="col">
                    Share
                  </th>
                </tr>
              </thead>
              <tbody>
                {family.labels.map((label, index) => {
                  const amount = family.counts?.[index] ?? family.targetMass?.[index];
                  return (
                    <tr className="border-b border-border/60" key={`${family.id}-${label}`}>
                      <th className="max-w-48 break-words px-3 py-2 font-normal" scope="row">
                        {label}
                      </th>
                      <td className="px-3 py-2 text-right">{amount?.toLocaleString() ?? "—"}</td>
                      <td className="w-40 px-3 py-2">
                        {amount == null ? (
                          "—"
                        ) : (
                          <span className="flex items-center gap-2">
                            <span className="h-1.5 flex-1 overflow-hidden rounded-xs bg-muted">
                              <span
                                className="block h-full bg-primary/70"
                                style={{ width: `${(100 * amount) / family.rows}%` }}
                              />
                            </span>
                            <span className="w-14 text-right">
                              {((100 * amount) / family.rows).toFixed(2)}%
                            </span>
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}
