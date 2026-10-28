import { useState } from "react";

import floppyDiskIcon from "@/assets/floppy-disk-save.svg";
import { DatasetsBrowser, DatasetsToolbar } from "@/components/datasets/datasets-table";
import { TableSectionHeader } from "@/components/ui/table-section-header";
import { useProjectDatasetsQuery } from "@/hooks/use-datasets";
import { datasetWorkspaces } from "@/lib/dataset-workspaces";

interface DatasetTabProps {
  capabilityId: string;
  projectId?: string | null;
}

export function DatasetTab({ capabilityId, projectId }: DatasetTabProps) {
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("all");

  const datasetsQuery = useProjectDatasetsQuery(projectId ?? undefined);
  const inventory = datasetsQuery.data ?? [];
  const attachedWorkspaces = new Set(
    inventory
      .filter((dataset) => dataset.capability === capabilityId)
      .map((dataset) => dataset.workspaceId)
  );
  const datasets = datasetWorkspaces(inventory).filter((dataset) =>
    attachedWorkspaces.has(dataset.id)
  );

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
            roleFilter={roleFilter}
            search={search}
            setRoleFilter={setRoleFilter}
            setSearch={setSearch}
          />
        }
      />
      <DatasetsBrowser
        datasets={datasets}
        error={datasetsQuery.error}
        fill={false}
        hideCapabilityColumn
        // The query is gated on the project and this tab has no project early-return,
        // so a capability seen without one must settle empty rather than skeleton forever.
        isLoading={!!projectId && !datasetsQuery.isFetched}
        roleFilter={roleFilter}
        search={search}
        setRoleFilter={setRoleFilter}
        setSearch={setSearch}
      />
    </div>
  );
}
