import { useEffect, useRef, useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { DatasetFileUpload } from "@/components/datasets/dataset-file-upload";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { useCreateDatasetMutation } from "@/hooks/use-datasets";
import { setStoredProjectId } from "@/hooks/use-project-search-sync";
import { useDatasetUploads } from "@/hooks/use-uploads";
import { errorMessage } from "@/lib/notify";
import type { Project } from "@/openapi";

export interface DataProjectCreated {
  projectId: string;
  datasetId: string;
}

export function DataProjectForm({
  onCreated,
  onBusyChange,
}: {
  onCreated: (created: DataProjectCreated) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);
  const [suffix] = useState(() => crypto.randomUUID().slice(0, 8));
  const projectRef = useRef<Project | null>(null);
  const upload = useDatasetUploads();
  const dataset = useCreateDatasetMutation();
  const client = useQueryClient();
  const ready = upload.files.length > 0 && upload.files.every((file) => file.status === "ready");
  const create = useMutation({
    mutationFn: async () => {
      if (!name.trim() || !ready) throw new Error("Enter a project name and upload your files.");
      // Reuse the project if dataset creation fails and the user retries.
      const project =
        projectRef.current ??
        (await apiClient.projects.projectsCreate({
          projectRequest: {
            name: name.trim(),
            slug: `${
              name
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, "-")
                .replace(/^-|-$/g, "")
                .slice(0, 40) || "data"
            }-${suffix}`,
          },
        }));
      projectRef.current = project;
      client.setQueryData(
        ["projects"],
        (current: { projects: Array<Project & { projectId: string }> } | undefined) => {
          const projects = current?.projects ?? [];
          if (projects.some((item) => item.projectId === project.id)) return current;
          return { projects: [...projects, { ...project, projectId: project.id }] };
        }
      );
      void client.invalidateQueries({ queryKey: ["projects"] });
      const created = await dataset.mutateAsync({
        brief: "Prepare the uploaded data as a supervised fine-tuning (SFT) dataset.",
        capabilityId: null,
        intent: "train",
        name: project.name,
        projectId: project.id,
        source: { uploads: upload.files.map((file) => file.uploadId!) },
      });
      return { datasetId: created.id, projectId: project.id };
    },
    onSuccess: (created) => {
      setStoredProjectId(created.projectId);
      onCreated(created);
    },
  });
  useEffect(() => {
    onBusyChange?.(create.isPending);
  }, [create.isPending, onBusyChange]);

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!create.isPending && !create.isSuccess) create.mutate();
      }}
    >
      <p className="text-sm text-muted-foreground">
        Upload your source files. Then use your coding agent to prepare an SFT dataset in the
        Workshop.
      </p>
      <label className="flex flex-col gap-2 text-sm">
        Project name
        <Input
          autoComplete="off"
          disabled={create.isPending || !!projectRef.current}
          maxLength={100}
          onChange={(e) => setName(e.target.value)}
          required
          value={name}
        />
      </label>
      <DatasetFileUpload
        busy={create.isPending || create.isSuccess}
        onError={setFileError}
        upload={upload}
      />
      {fileError && <Alert variant="destructive">{fileError}</Alert>}
      {create.error && <Alert variant="destructive">{errorMessage(create.error)}</Alert>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p aria-live="polite" className="text-xs text-muted-foreground">
          {create.isPending
            ? "Creating your dataset…"
            : upload.files.some((file) => file.status === "error")
              ? "Retry or remove files with errors."
              : upload.files.length && !ready
                ? "Uploading and checking files…"
                : "Next: prepare with your coding agent"}
        </p>
        <Button
          disabled={create.isPending || create.isSuccess || !name.trim() || !ready}
          type="submit"
        >
          <Icon.datasetAdd /> Create dataset
        </Button>
      </div>
    </form>
  );
}
