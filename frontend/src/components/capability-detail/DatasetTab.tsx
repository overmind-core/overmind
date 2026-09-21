import { useState } from "react";

import { DatasetsBrowser, DatasetsToolbar } from "@/components/datasets/datasets-table";
import { type DatasetSource, NewDatasetButton } from "@/components/datasets/new-dataset-button";
import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { useDatasetsQuery } from "@/hooks/use-datasets";

interface DatasetTabProps {
  capabilityId: string;
  projectId?: string | null;
}

export function DatasetTab({ capabilityId, projectId }: DatasetTabProps) {
  const [createOpen, setCreateOpen] = useState(false);
  const [source, setSource] = useState<DatasetSource>("file");
  const [search, setSearch] = useState("");
  const [intentFilter, setIntentFilter] = useState("all");
  const openCreate = (source: DatasetSource) => {
    setSource(source);
    setCreateOpen(true);
  };

  const datasetsQuery = useDatasetsQuery(projectId ?? undefined, {
    capability: capabilityId,
    pageSize: 200,
  });
  const datasets = datasetsQuery.data?.results ?? [];

  return (
    <div className="flex flex-col gap-4">
      {/* Empty, the toolbar would be one button in an empty row, so the CTA lives
          in the empty state instead. */}
      {datasets.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1">
            <DatasetsToolbar
              datasets={datasets}
              intentFilter={intentFilter}
              onCreate={openCreate}
              search={search}
              setIntentFilter={setIntentFilter}
              setSearch={setSearch}
            />
          </div>
        </div>
      )}

      <DatasetsBrowser
        datasets={datasets}
        emptyAction={<NewDatasetButton onSelect={openCreate} />}
        emptyDescription="Upload files or select data from traces."
        error={datasetsQuery.error}
        fill={false}
        hideCapabilityColumn
        intentFilter={intentFilter}
        // The query is gated on the project and this tab has no project early-return,
        // so a capability seen without one must settle empty rather than skeleton forever.
        isLoading={!!projectId && !datasetsQuery.isFetched}
        search={search}
        setIntentFilter={setIntentFilter}
        setSearch={setSearch}
      />

      {projectId && (
        <NewDatasetDialog
          initialCapabilityId={capabilityId}
          initialSource={source}
          onOpenChange={setCreateOpen}
          open={createOpen}
          projectId={projectId}
        />
      )}
    </div>
  );
}
