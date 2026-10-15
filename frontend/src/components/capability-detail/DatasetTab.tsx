import { useState } from "react";

import floppyDiskIcon from "@/assets/floppy-disk-save.svg";
import { DatasetsBrowser, DatasetsToolbar } from "@/components/datasets/datasets-table";
import { TableSectionHeader } from "@/components/ui/table-section-header";
import { useDatasetsQuery } from "@/hooks/use-datasets";

interface DatasetTabProps {
  capabilityId: string;
  projectId?: string | null;
}

export function DatasetTab({ capabilityId, projectId }: DatasetTabProps) {
  const [search, setSearch] = useState("");
  const [intentFilter, setIntentFilter] = useState("all");

  const datasetsQuery = useDatasetsQuery(projectId ?? undefined, {
    capability: capabilityId,
    pageSize: 200,
  });
  const datasets = datasetsQuery.data?.results ?? [];

  return (
    <div className="space-y-3">
      <TableSectionHeader
        count={datasets.length}
        description="Datasets attached to this capability for training and evaluation."
        icon={
          <img
            alt=""
            aria-hidden="true"
            className="size-6 shrink-0 [image-rendering:pixelated]"
            src={floppyDiskIcon}
          />
        }
        title="Datasets"
        toolbar={
          <DatasetsToolbar
            datasets={datasets}
            intentFilter={intentFilter}
            search={search}
            setIntentFilter={setIntentFilter}
            setSearch={setSearch}
          />
        }
      />
      <DatasetsBrowser
        datasets={datasets}
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
    </div>
  );
}
