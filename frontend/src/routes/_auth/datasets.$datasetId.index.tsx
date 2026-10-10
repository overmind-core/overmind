import { createFileRoute } from "@tanstack/react-router";
import z from "zod";

import { DatasetNotebook } from "@/components/datasets/notebook/notebook-page";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { PageShell } from "@/components/ui/page-shell";
import { projectIdSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/datasets/$datasetId/")({
  component: DatasetNotebookPage,
  validateSearch: projectIdSearchSchema.extend({
    /** Exact cell focus carried by run back-links. */
    cell: z.string().optional(),
    iteration: z.string().optional(),
  }),
});

function DatasetNotebookPage() {
  const { datasetId } = Route.useParams();
  const { projectId, cell, iteration } = Route.useSearch();

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
        iteration={iteration}
        key={datasetId}
        projectId={projectId}
      />
    </PageShell>
  );
}
