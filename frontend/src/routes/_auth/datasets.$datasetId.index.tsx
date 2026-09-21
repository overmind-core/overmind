import { createFileRoute } from "@tanstack/react-router";
import z from "zod";

import { DatasetNotebook } from "@/components/datasets/notebook/notebook-page";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { PageShell } from "@/components/ui/page-shell";
import { projectIdSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/datasets/$datasetId/")({
  component: DatasetNotebookPage,
  validateSearch: projectIdSearchSchema.extend({
    /** A cell to scroll to; back-links from runs carry it. */
    cell: z.string().optional(),
    request: z.string().max(6000).optional(),
  }),
});

function DatasetNotebookPage() {
  const { datasetId } = Route.useParams();
  const { projectId, cell, request } = Route.useSearch();

  if (!projectId) {
    return (
      <PageShell header={null} variant="full">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  // Keyed by dataset so a breadcrumb switch remounts local notebook state.
  return (
    <PageShell className="absolute inset-0 overflow-clip" header={null} variant="full">
      <DatasetNotebook
        cellParam={cell}
        datasetId={datasetId}
        initialRequest={request}
        key={datasetId}
        projectId={projectId}
      />
    </PageShell>
  );
}
