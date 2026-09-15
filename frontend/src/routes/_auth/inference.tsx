import { useEffect, useState } from "react";

import { createFileRoute } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";

import { InferenceToolbar } from "@/components/inference/inference-toolbar";
import {
  BaseModelCell,
  CapabilityChip,
  CopyableModelRef,
  FineTunedModelIdentity,
  TrainingJobChip,
} from "@/components/inference/model-identity";
import { ServingStatusBadge } from "@/components/inference/serving-status";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { DataTable } from "@/components/ui/data-table";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { useDeployedModelsQuery, useModelLiveQuery } from "@/hooks/use-inference";
import { type InferenceSearch, inferenceSearchSchema } from "@/lib/schemas";
import type { DeployedModel } from "@/openapi";

const DASH = <span className="text-muted-foreground">—</span>;
const fmtInt = (n: number | null | undefined) => (n == null ? DASH : n.toLocaleString());
const fmtTps = (n: number | null | undefined) => (n == null ? DASH : `${n.toFixed(0)} tok/s`);
const fmtMs = (n: number | null | undefined) => (n == null ? DASH : `${n.toFixed(0)} ms`);

const NUM_CELL = "font-mono tabular-nums text-muted-foreground";

const INFERENCE_HEADER = (
  <PageHeader
    description="Serve, monitor and call your fine-tuned models."
    icon={
      <Icon.inferenceTitle
        aria-hidden
        className="size-6 shrink-0 [image-rendering:pixelated] dark:invert"
      />
    }
    title="Inference"
  />
);

export const Route = createFileRoute("/_auth/inference")({
  component: InferencePage,
  validateSearch: inferenceSearchSchema,
});

const isDeleted = (m: DeployedModel) => m.status === "deleting" || m.status === "deleted";

function InferencePage() {
  const {
    inf_capability: capability,
    inf_search: search,
    page,
    page_size: pageSize,
    projectId,
    status,
  } = Route.useSearch();
  const navigate = Route.useNavigate();
  const [searchText, setSearchText] = useState(search);
  const debouncedSearch = useDebouncedValue(searchText, 400);

  // Publish settled draft → URL. No-op when equal so mount doesn't reset page.
  useEffect(() => {
    const next = debouncedSearch.trim();
    if (next === search) return;
    void navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, inf_search: next, page: 1 }),
    });
  }, [debouncedSearch, search, navigate]);

  // Back/forward wins over stale keystrokes; trim-equal keeps in-flight typing.
  useEffect(() => {
    setSearchText((cur) => (search === cur.trim() ? cur : search));
  }, [search]);

  const { data, isLoading, error } = useDeployedModelsQuery({
    capability: capability === "all" ? undefined : capability,
    page,
    pageSize,
    projectId,
    search,
    status,
  });

  const hasActiveFilters = Boolean(searchText.trim() || status || capability !== "all");

  const patchFilters = (updates: Partial<InferenceSearch>) =>
    void navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, ...updates, page: 1 }),
    });

  const handleClearFilters = () => {
    setSearchText("");
    patchFilters({ inf_capability: "all", inf_search: "", status: undefined });
  };

  // No sortable headers: the backend's `ordering_fields` omit latency and
  // tokens/sec (its `list()` shows medians, not the SQL means it would sort on).
  const columns: ColumnDef<DeployedModel>[] = [
    {
      accessorKey: "modelId",
      cell: ({ row }) => (
        <FineTunedModelIdentity
          baseModelId={row.original.baseModelId}
          capabilityName={row.original.capabilityName}
          displayName={row.original.finetuningJobName}
          modelId={row.original.modelId}
        />
      ),
      header: "Model",
      meta: { noTruncate: true },
      minSize: 160,
      size: 240,
    },
    {
      accessorKey: "baseModelId",
      cell: ({ row }) => <BaseModelCell baseModelId={row.original.baseModelId} />,
      header: "Base model",
      meta: { noTruncate: true },
      minSize: 120,
      size: 150,
    },
    {
      accessorKey: "capabilityName",
      cell: ({ row }) =>
        row.original.capabilityId ? (
          <CapabilityChip
            capabilityId={row.original.capabilityId}
            capabilityName={row.original.capabilityName}
          />
        ) : (
          DASH
        ),
      header: "Capability",
      meta: { noTruncate: true },
      minSize: 120,
      size: 150,
    },
    {
      accessorKey: "finetuningJobName",
      cell: ({ row }) => (
        <TrainingJobChip
          baseModelId={row.original.baseModelId}
          capabilityName={row.original.capabilityName}
          jobId={row.original.finetuningJobId}
          jobName={row.original.finetuningJobName}
          projectId={projectId}
        />
      ),
      header: "Training job",
      meta: { noTruncate: true },
      minSize: 120,
      size: 170,
    },
    {
      cell: ({ row }) => <CopyableModelRef modelId={row.original.modelId} />,
      header: "Reference",
      id: "reference",
      meta: { noTruncate: true },
      minSize: 130,
      size: 180,
    },
    {
      accessorKey: "status",
      cell: ({ row }) => <ModelStatusCell id={row.original.id} status={row.original.status} />,
      header: "Status",
      meta: { noTruncate: true },
      minSize: 110,
      size: 130,
    },
    {
      accessorKey: "requestCount",
      cell: ({ row }) => (
        <span className={NUM_CELL}>{(row.original.requestCount ?? 0).toLocaleString()}</span>
      ),
      header: "Requests",
      minSize: 90,
      size: 110,
    },
    {
      accessorKey: "totalTokens",
      cell: ({ row }) => <span className={NUM_CELL}>{fmtInt(row.original.totalTokens)}</span>,
      header: "Tokens",
      minSize: 90,
      size: 110,
    },
    {
      accessorKey: "avgTokensPerSecond",
      cell: ({ row }) => (
        <span className={NUM_CELL}>{fmtTps(row.original.avgTokensPerSecond)}</span>
      ),
      header: "Throughput",
      minSize: 100,
      size: 120,
    },
    {
      accessorKey: "avgLatencyMs",
      cell: ({ row }) => <span className={NUM_CELL}>{fmtMs(row.original.avgLatencyMs)}</span>,
      header: "Latency",
      minSize: 90,
      size: 110,
    },
    {
      accessorKey: "lastActiveAt",
      cell: ({ row }) => (
        <span className="text-muted-foreground">
          <DateTime fallback="Never" value={row.original.lastActiveAt} />
        </span>
      ),
      header: "Last active",
      minSize: 110,
      size: 140,
    },
  ];

  if (!projectId) {
    return (
      <PageShell header={INFERENCE_HEADER} variant="full">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  return (
    <PageShell header={INFERENCE_HEADER} variant="full">
      <div className="flex min-h-0 flex-1 flex-col">
        <DataTable
          columns={columns}
          data={data}
          emptyState={
            <EmptyState
              className="flex-1"
              description="Deploy a fine-tuned model from the Training page to serve it here."
              icon={Icon.inferenceTitle}
              iconClassName="dark:invert [image-rendering:pixelated]"
              size="section"
              title="No deployed models"
            />
          }
          error={error}
          getRowId={(row) => row.id}
          hasActiveFilters={hasActiveFilters}
          isLoading={isLoading}
          onClearFilters={handleClearFilters}
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
          onRowClick={(m) => {
            if (isDeleted(m)) return;
            navigate({
              params: { modelId: m.id },
              search: (prev) => prev,
              to: "/inference/$modelId",
            });
          }}
          page={page}
          pageSize={pageSize}
          rowClassName={(m) => (isDeleted(m) ? "cursor-default opacity-60" : undefined)}
          storageKey="inference:models-table-cols"
          toolbar={
            <InferenceToolbar
              capabilityFilter={capability}
              hasActiveFilters={hasActiveFilters}
              onCapabilityFilterChange={(value) => patchFilters({ inf_capability: value })}
              onClearFilters={handleClearFilters}
              onSearchChange={setSearchText}
              onSearchClear={() => {
                setSearchText("");
                patchFilters({ inf_search: "" });
              }}
              onStatusFilterChange={(value) => patchFilters({ status: value })}
              projectId={projectId}
              resultCount={data?.count}
              search={searchText}
              statusFilter={status}
            />
          }
        />
      </div>
    </PageShell>
  );
}

/** Only ready rows call Modal for live serving state. */
function ModelStatusCell({ id, status }: { id: string; status: string }) {
  const { data: live } = useModelLiveQuery(id, status === "ready", 0);
  return <ServingStatusBadge live={live} status={status} />;
}
