import { createFileRoute } from "@tanstack/react-router";
import z from "zod";

import { DatasetNotebook } from "@/components/datasets/notebook/notebook-page";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PageShell } from "@/components/ui/page-shell";
import { projectIdSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/datasets/$datasetId/")({
  component: DatasetNotebookPage,
  validateSearch: projectIdSearchSchema.extend({
    /** Exact cell focus carried by run back-links. */
    cell: z.string().optional(),
    prepare: z.literal("training").optional(),
  }),
});

function DatasetNotebookPage() {
  const { datasetId } = Route.useParams();
  const { projectId, cell, prepare } = Route.useSearch();
  const navigate = Route.useNavigate();
  const closePreparation = () =>
    void navigate({ replace: true, search: (previous) => ({ ...previous, prepare: undefined }) });

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
      <Dialog
        onOpenChange={(open) => {
          if (!open) closePreparation();
        }}
        open={prepare === "training"}
      >
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Prepare your training data</DialogTitle>
            <DialogDescription>
              Copy the prompt into your coding agent. The prepared data and scripts will appear in
              this Workshop.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <OnboardWithAiPanel datasetId={datasetId} projectId={projectId} />
          </DialogBody>
          <DialogFooter>
            <Button onClick={closePreparation} variant="secondary">
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <DatasetNotebook
        cellParam={cell}
        datasetId={datasetId}
        key={datasetId}
        projectId={projectId}
      />
    </PageShell>
  );
}
