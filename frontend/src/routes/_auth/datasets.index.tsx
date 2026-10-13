import { useCallback, useEffect, useMemo, useState } from "react";

import { createFileRoute, redirect } from "@tanstack/react-router";

import floppyDiskIcon from "@/assets/floppy-disk-save.svg";
import { DatasetsBrowser, DatasetsToolbar } from "@/components/datasets/datasets-table";
import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { useDatasetInventoryQuery } from "@/hooks/use-datasets";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { featureFlags } from "@/lib/feature-flags";
import { type DatasetsSearch, datasetsSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/datasets/")({
  beforeLoad: () => {
    if (!featureFlags.datasets) throw redirect({ replace: true, to: "/" });
  },
  component: DatasetsIndexPage,
  validateSearch: datasetsSearchSchema,
});

const ICON = (
  <img
    alt=""
    aria-hidden="true"
    className="size-6 shrink-0 [image-rendering:pixelated] invert"
    src={floppyDiskIcon}
  />
);
const DESCRIPTION = "Data to train and evaluate your agent, with every version kept.";

function DatasetsIndexPage() {
  const {
    projectId,
    create: createParam,
    ds_capability: capabilityFilter,
    ds_intent: intentFilter,
    ds_search: searchParam,
  } = Route.useSearch();
  const navigate = Route.useNavigate();
  const guard = useGuestGate();
  const [createOpen, setCreateOpen] = useState(false);

  // Consume `?create=true` once so a refresh doesn't re-open forever.
  useEffect(() => {
    if (!createParam) return;
    setCreateOpen(true);
    void navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, create: undefined }),
    });
  }, [createParam, navigate]);

  const datasetsQuery = useDatasetInventoryQuery(projectId);
  const capabilitiesQuery = useProjectCapabilitiesQuery(projectId);
  const capabilityNameById = useMemo(() => {
    const map: Record<string, string> = {};
    for (const a of capabilitiesQuery.data?.results ?? []) map[a.id] = a.name;
    return map;
  }, [capabilitiesQuery.data]);

  // Filters live in the URL so they survive a reload; `replace` keeps them out
  // of the history stack.
  const patchSearch = useCallback(
    (updates: Partial<DatasetsSearch>) =>
      void navigate({
        replace: true,
        resetScroll: false,
        search: (prev) => ({ ...prev, ...updates }),
      }),
    [navigate]
  );

  const [search, setSearch] = useState(searchParam);
  const debouncedSearch = useDebouncedValue(search, 400);
  useEffect(() => {
    if (debouncedSearch !== searchParam) patchSearch({ ds_search: debouncedSearch });
  }, [debouncedSearch, searchParam, patchSearch]);

  const setIntentFilter = (v: string) =>
    patchSearch({ ds_intent: v as DatasetsSearch["ds_intent"] });
  const setCapabilityFilter = (v: string) => patchSearch({ ds_capability: v });

  if (!projectId) {
    return (
      <PageShell
        header={<PageHeader description={DESCRIPTION} icon={ICON} title="Datasets" />}
        variant="full"
      >
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  const datasets = datasetsQuery.data ?? [];
  const newDataset = (
    <Button onClick={guard(() => setCreateOpen(true))} size="sm">
      <Icon.datasetAdd />
      New dataset
    </Button>
  );

  return (
    <PageShell
      header={
        <PageHeader
          actions={newDataset}
          count={datasets.length}
          description={DESCRIPTION}
          icon={ICON}
          title="Datasets"
        />
      }
      variant="full"
    >
      <div className="relative flex min-h-0 flex-1 flex-col gap-4">
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <DatasetsToolbar
            capabilityFilter={capabilityFilter}
            capabilityNameById={capabilityNameById}
            datasets={datasets}
            intentFilter={intentFilter}
            search={search}
            setCapabilityFilter={setCapabilityFilter}
            setIntentFilter={setIntentFilter}
            setSearch={setSearch}
          />

          <DatasetsBrowser
            capabilityFilter={capabilityFilter}
            capabilityNameById={capabilityNameById}
            datasets={datasets}
            error={datasetsQuery.error}
            intentFilter={intentFilter}
            isLoading={!datasetsQuery.isFetched}
            search={search}
            setCapabilityFilter={setCapabilityFilter}
            setIntentFilter={setIntentFilter}
            setSearch={setSearch}
          />
        </div>

        <NewDatasetDialog
          initialCapabilityId={capabilityFilter !== "all" ? capabilityFilter : undefined}
          onOpenChange={setCreateOpen}
          open={createOpen}
          projectId={projectId}
        />
      </div>
    </PageShell>
  );
}
