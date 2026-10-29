import { createFileRoute } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";

import { STATUS_LABEL, STATUS_TONE } from "@/components/inference/dedicated-deployment";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { Badge } from "@/components/ui/badge";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { useDedicatedDeploymentsQuery } from "@/hooks/use-inference";
import { dedicatedSearchSchema } from "@/lib/schemas";
import type { DedicatedDeployment } from "@/openapi";

const HEADER = (
  <PageHeader
    description="Current dedicated deployment for each model."
    icon={<Icon.server />}
    title="Dedicated"
  />
);

export const Route = createFileRoute("/_auth/dedicated")({
  component: DedicatedPage,
  validateSearch: dedicatedSearchSchema,
});

function rate(raw: string | null | undefined): string {
  if (raw == null || raw === "") return "—";
  const amount = Number(raw);
  return Number.isFinite(amount)
    ? amount.toLocaleString("en-US", { maximumFractionDigits: 2 })
    : raw;
}

function DedicatedPage() {
  const { page, page_size: pageSize, projectId } = Route.useSearch();
  const navigate = Route.useNavigate();
  const { data, error, isLoading } = useDedicatedDeploymentsQuery({ page, pageSize, projectId });

  const columns: ColumnDef<DedicatedDeployment>[] = [
    {
      accessorKey: "modelName",
      cell: ({ row }) => <span className="font-medium">{row.original.modelName}</span>,
      header: "Model",
      meta: { noTruncate: true },
      minSize: 160,
      size: 240,
    },
    {
      accessorKey: "gpuType",
      header: "GPU",
      minSize: 90,
      size: 110,
    },
    {
      accessorKey: "status",
      cell: ({ row }) => {
        const status = row.original.status ?? "";
        return (
          <Badge size="chip" variant={STATUS_TONE[status as keyof typeof STATUS_TONE] ?? "neutral"}>
            {STATUS_LABEL[status] ?? status}
          </Badge>
        );
      },
      header: "Status",
      meta: { noTruncate: true },
      minSize: 110,
      size: 140,
    },
    {
      accessorKey: "runningReplicas",
      cell: ({ row }) => (
        <span className="font-mono tabular-nums text-muted-foreground">
          {row.original.runningReplicas ?? 0}
        </span>
      ),
      header: "Replicas",
      minSize: 90,
      size: 110,
    },
    {
      accessorKey: "creditsPerGpuHour",
      cell: ({ row }) => (
        <span className="font-mono tabular-nums text-muted-foreground">
          {rate(row.original.creditsPerGpuHour)}
        </span>
      ),
      header: "Credits/hour",
      minSize: 110,
      size: 130,
    },
  ];

  if (!projectId) {
    return (
      <PageShell header={HEADER} variant="full">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  return (
    <PageShell header={HEADER} variant="full">
      <div className="flex min-h-0 flex-1 flex-col">
        <DataTable
          columns={columns}
          data={data}
          emptyState={
            <EmptyState
              className="flex-1"
              description="Start one from a model's inference page."
              icon={Icon.server}
              size="section"
              title="No dedicated deployments"
            />
          }
          error={error}
          getRowId={(row) => row.id ?? row.deployedModel}
          isLoading={isLoading}
          onPageChange={(p) =>
            navigate({
              replace: true,
              resetScroll: false,
              search: (prev) => ({ ...prev, page: p }),
            })
          }
          onPageSizeChange={(s) =>
            navigate({
              replace: true,
              resetScroll: false,
              search: (prev) => ({ ...prev, page: 1, page_size: s }),
            })
          }
          onRowClick={(row) => {
            if (!row.deployedModel) return;
            navigate({
              params: { modelId: row.deployedModel },
              search: { projectId },
              to: "/inference/$modelId",
            });
          }}
          page={page}
          pageSize={pageSize}
          storageKey="dedicated:deployments-table-cols"
        />
      </div>
    </PageShell>
  );
}
