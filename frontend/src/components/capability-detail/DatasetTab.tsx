import { useState } from "react";

import { DatasetsBrowser, DatasetsToolbar } from "@/components/datasets/datasets-table";
import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { useDatasetsQuery } from "@/hooks/use-datasets";
import { useGuestGate } from "@/hooks/use-guest-gate";

interface DatasetTabProps {
  capabilityId: string;
  projectId?: string | null;
}

export function DatasetTab({ capabilityId, projectId }: DatasetTabProps) {
  const [createOpen, setCreateOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [intentFilter, setIntentFilter] = useState("all");
  const guard = useGuestGate();
  const openCreate = guard(() => setCreateOpen(true));

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
        emptyAction={
          <Button onClick={openCreate}>
            <Icon.datasetAdd /> New dataset
          </Button>
        }
        emptyDescription="Upload a file, paste rows, or land traces."
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
          onOpenChange={setCreateOpen}
          open={createOpen}
          projectId={projectId}
        />
      )}
    </div>
  );
}
