import { useCallback, useEffect, useMemo, useState } from "react";

import { createFileRoute, redirect } from "@tanstack/react-router";

import floppyDiskIcon from "@/assets/floppy-disk-save.svg";
import { DatasetsBrowser, DatasetsToolbar } from "@/components/datasets/datasets-table";
import type { DatasetSource } from "@/components/datasets/new-dataset-button";
import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { useDatasetsQuery } from "@/hooks/use-datasets";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
import { featureFlags } from "@/lib/feature-flags";
import { type DatasetsSearch, datasetsSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/datasets/")({
  beforeLoad: () => {
    if (!featureFlags.datasets) throw redirect({ replace: true, to: "/" });
  },
  component: DatasetsIndexPage,
  validateSearch: datasetsSearchSchema,
});

const HEADER = (
  <PageHeader
    description="Land data, shape it cell by cell, and hand a version to runs."
    icon={
      <img
        alt=""
        aria-hidden="true"
        className="size-6 shrink-0 [image-rendering:pixelated] dark:invert"
        src={floppyDiskIcon}
      />
    }
    title="Datasets"
  />
);

function DatasetsIndexPage() {
  const {
    projectId,
    create: createParam,
    ds_capability: capabilityFilter,
    ds_intent: intentFilter,
    ds_search: searchParam,
  } = Route.useSearch();
  const navigate = Route.useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [source, setSource] = useState<DatasetSource>("file");
  const [pageDragging, setPageDragging] = useState(false);

  const openCreate = useCallback((source: DatasetSource, files: File[] = []) => {
    setSource(source);
    setPendingFiles(files);
    setCreateOpen(true);
  }, []);

  // Consume `?create=true` once so a refresh doesn't re-open forever.
  useEffect(() => {
    if (!createParam) return;
    openCreate("file");
    void navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, create: undefined }),
    });
  }, [createParam, openCreate, navigate]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setPageDragging(false);
      if (!projectId || createOpen) return;
      const files = Array.from(e.dataTransfer.files);
      if (files.length) openCreate("file", files);
    },
    [openCreate, projectId, createOpen]
  );

  const datasetsQuery = useDatasetsQuery(projectId, { pageSize: 200 });
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
      <PageShell header={HEADER} variant="full">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  const datasets = datasetsQuery.data?.results ?? [];

  return (
    <PageShell header={HEADER} variant="full">
      {/* Separate from PageShell: the shell doesn't forward DOM event handlers. */}
      <div
        className="relative flex min-h-0 flex-1 flex-col gap-4"
        onDragLeave={(e) => {
          if (e.currentTarget === e.target) setPageDragging(false);
        }}
        onDragOver={(e) => {
          if (!createOpen && e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            setPageDragging(true);
          }
        }}
        onDrop={handleDrop}
      >
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <DatasetsToolbar
            capabilityFilter={capabilityFilter}
            capabilityNameById={capabilityNameById}
            datasets={datasets}
            intentFilter={intentFilter}
            onCreate={openCreate}
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

        {pageDragging && (
          <div className="pointer-events-none fixed inset-0 z-40 flex items-center justify-center bg-background/70">
            <div className="flex flex-col items-center gap-3 rounded-md border-2 border-dashed border-primary/60 bg-background px-10 py-8">
              <Icon.upload className="size-7 text-primary" />
              <p className="text-base font-medium">Drop to create a dataset</p>
              <p className="text-xs text-muted-foreground">CSV, TSV, JSON, JSONL, Parquet</p>
            </div>
          </div>
        )}

        <NewDatasetDialog
          initialCapabilityId={capabilityFilter !== "all" ? capabilityFilter : undefined}
          initialFiles={pendingFiles}
          initialSource={source}
          onOpenChange={(o) => {
            setCreateOpen(o);
            if (!o) setPendingFiles([]);
          }}
          open={createOpen}
          projectId={projectId}
        />
      </div>
    </PageShell>
  );
}
