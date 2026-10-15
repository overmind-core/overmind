import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { setStoredProjectId } from "@/hooks/use-project-search-sync";
import { errorMessage } from "@/lib/notify";
import type { Project } from "@/openapi";

export function DataProjectForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const [suffix] = useState(() => crypto.randomUUID().slice(0, 8));
  const client = useQueryClient();
  const create = useMutation({
    mutationFn: () =>
      apiClient.projects.projectsCreate({
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
      }),
    onSuccess: (project) => {
      setStoredProjectId(project.id);
      client.setQueryData(
        ["projects"],
        (current: { projects: Array<Project & { projectId: string }> } | undefined) => {
          const projects = current?.projects ?? [];
          if (projects.some((item) => item.projectId === project.id)) return current;
          return { projects: [...projects, { ...project, projectId: project.id }] };
        }
      );
      void client.invalidateQueries({ queryKey: ["projects"] });
      onCreated(project.id);
    },
  });
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <p className="text-sm text-muted-foreground">
        Upload data for training or evaluation. The Workshop explores the source and your task
        before preparing it.
      </p>
      <label className="flex flex-col gap-2 text-sm">
        Project name
        <Input
          autoComplete="off"
          maxLength={100}
          onChange={(e) => setName(e.target.value)}
          required
          value={name}
        />
      </label>
      {create.error && <Alert variant="destructive">{errorMessage(create.error)}</Alert>}
      <Button className="self-start" disabled={create.isPending || !name.trim()} type="submit">
        {create.isPending ? "Creating…" : "Continue with data"}
      </Button>
    </form>
  );
}
