import { type ReactNode, useMemo, useState } from "react";

import type { ColumnDef } from "@tanstack/react-table";

import { CreditsAmount } from "@/components/ui/credits";
import { DataTable } from "@/components/ui/data-table";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { useBillingLedgerQuery, useCommercialBilling } from "@/hooks/use-subscription";
import { PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { BillingTelemetry } from "@/openapi";

const PAGE_SIZE = 10;

function amountCell(amount: string): { node: ReactNode; className: string } {
  const n = Number(amount);
  if (!Number.isFinite(n)) return { className: "text-foreground", node: amount };
  const value = (
    <>
      {n > 0 ? "+" : n < 0 ? "−" : ""}
      <CreditsAmount usd={Math.abs(n)} />
    </>
  );
  if (n > 0) return { className: "text-success", node: value };
  if (n < 0) return { className: "text-foreground", node: value };
  return { className: "text-muted-foreground", node: value };
}

const columns: ColumnDef<BillingTelemetry>[] = [
  {
    accessorKey: "timestamp",
    cell: ({ row }) => (
      <span className="text-muted-foreground">
        <DateTime value={row.original.timestamp} />
      </span>
    ),
    header: "When",
    minSize: 140,
    size: 180,
  },
  {
    accessorKey: "serviceLabel",
    cell: ({ row }) => (
      <span className="font-medium text-foreground">{row.original.serviceLabel}</span>
    ),
    header: "Service",
    minSize: 120,
    size: 160,
  },
  {
    accessorKey: "projectName",
    cell: ({ row }) => (
      <span className="text-muted-foreground">{row.original.projectName ?? "—"}</span>
    ),
    header: "Project",
    minSize: 120,
    size: 160,
  },
  {
    accessorKey: "amount",
    cell: ({ row }) => {
      const amount = amountCell(row.original.amount);
      return (
        <span className={cn("font-medium tabular-nums", amount.className)}>{amount.node}</span>
      );
    },
    header: "Amount",
    meta: { noTruncate: true },
    minSize: 100,
    size: 120,
  },
];

export function BillingLedgerTable({ className }: { className?: string }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const query = useBillingLedgerQuery(page, pageSize);
  const { enabled: commercial } = useCommercialBilling();

  const emptyState = useMemo(
    () => (
      <EmptyState
        description={
          commercial
            ? "Credits added and spent will show up here once your projects start using them."
            : "Usage will show up here once your projects start using compute."
        }
        icon={Icon.credits}
        size="section"
        title={commercial ? "No billing activity yet" : "No usage yet"}
      />
    ),
    [commercial]
  );

  // The table brings its own frame — no card wrapper, or it is a border around a border.
  return (
    <section aria-labelledby="usage-history-heading" className={cn("space-y-4", className)}>
      <div>
        <h3 className={TITLE.card} id="usage-history-heading">
          Usage history
        </h3>
        <p className={cn(PROSE, "mt-1 text-sm text-muted-foreground")}>
          {commercial ? "Credits added and spent. Newest first." : "Usage. Newest first."}
        </p>
      </div>
      <DataTable
        columns={columns}
        data={query.data}
        emptyState={emptyState}
        error={query.error}
        getRowId={(row) => row.id}
        isLoading={query.isLoading}
        onPageChange={setPage}
        onPageSizeChange={(s) => {
          setPage(1);
          setPageSize(s);
        }}
        page={page}
        pageSize={pageSize}
        storageKey="billing:ledger-cols"
      />
    </section>
  );
}
