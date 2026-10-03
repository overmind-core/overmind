import { useCallback, useEffect, useRef, useState } from "react";

import { createFileRoute, redirect } from "@tanstack/react-router";

import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { WorkshopSwitcher } from "@/components/datasets/workshop-sidebar";
import { WorkshopStart } from "@/components/datasets/workshop-start";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { PageShell } from "@/components/ui/page-shell";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { useDatasetUploads } from "@/hooks/use-uploads";
import { featureFlags } from "@/lib/feature-flags";
import { datasetsSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/datasets/")({
  beforeLoad: () => {
    if (!featureFlags.datasets) throw redirect({ replace: true, to: "/" });
  },
  component: DatasetsIndexPage,
  validateSearch: datasetsSearchSchema,
});

function DatasetsIndexPage() {
  const { projectId, create: createParam } = Route.useSearch();
  const navigate = Route.useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  const [pageDragging, setPageDragging] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const guard = useGuestGate();
  const uploads = useDatasetUploads();
  const { reset: resetUploads } = uploads;
  const draftProject = useRef(projectId);

  useEffect(() => {
    if (draftProject.current === projectId) return;
    draftProject.current = projectId;
    resetUploads();
    setPageDragging(false);
    dragDepth.current = 0;
  }, [projectId, resetUploads]);

  // Consume `?create=true` once so a refresh doesn't re-open forever.
  useEffect(() => {
    if (!createParam || creating) return;
    resetUploads();
    setDraft((value) => value + 1);
    void navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, create: undefined }),
    });
  }, [createParam, creating, navigate, resetUploads]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      dragDepth.current = 0;
      setPageDragging(false);
      if (!projectId || createOpen || creating) return;
      const files = Array.from(e.dataTransfer.files);
      if (files.length) guard(() => uploads.add(files))();
    },
    [uploads.add, guard, projectId, createOpen, creating]
  );

  if (!projectId) {
    return (
      <PageShell header={null} variant="full">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  return (
    <PageShell header={null} variant="full">
      {/* Separate from PageShell: the shell doesn't forward DOM event handlers. */}
      <div
        className="relative flex min-h-0 flex-1 flex-col overflow-y-auto"
        onDragEnter={(e) => {
          if (createOpen || creating || !e.dataTransfer.types.includes("Files")) return;
          e.preventDefault();
          dragDepth.current += 1;
          setPageDragging(true);
        }}
        onDragLeave={() => {
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (dragDepth.current === 0) setPageDragging(false);
        }}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            e.dataTransfer.dropEffect = createOpen || creating ? "none" : "copy";
          }
        }}
        onDrop={handleDrop}
      >
        <WorkshopSwitcher />
        <WorkshopStart
          dragging={pageDragging}
          focus={createParam}
          inputRef={fileInput}
          key={`${projectId}-${draft}`}
          onBusyChange={setCreating}
          onImportTraces={guard(() => setCreateOpen(true))}
          projectId={projectId}
          uploads={uploads}
        />
        <NewDatasetDialog
          initialSource="traces"
          onOpenChange={setCreateOpen}
          open={createOpen}
          projectId={projectId}
        />
      </div>
    </PageShell>
  );
}
