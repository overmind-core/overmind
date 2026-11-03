import type { ReactNode } from "react";

import { Link } from "@tanstack/react-router";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { LoadingState } from "@/components/ui/spinner";
import { useProjectsList } from "@/hooks/use-projects";

export function NoProjectsEmptyState() {
  return (
    <EmptyState
      action={
        <>
          <Button asChild>
            <Link to="/">Set up an agent</Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/datasets">Upload data</Link>
          </Button>
        </>
      }
      description="Set up an agent or upload data to create a project."
      icon={Icon.folderAdd}
      title="No projects yet"
    />
  );
}

export function ProjectRequiredEmptyState({
  noProjects = <NoProjectsEmptyState />,
  title = "Please select a project",
  description = "Select a project from the selector in the top bar to see this page.",
}: {
  noProjects?: ReactNode;
  title?: string;
  description?: string;
}) {
  const { data, isLoading, error } = useProjectsList({ pollWhileEmpty: true });
  const projects = data?.projects ?? [];

  if (isLoading) {
    return <LoadingState fullPage label="Loading projects" />;
  }

  if (error && !data) return <Alert variant="destructive">Couldn’t load projects.</Alert>;

  if (projects.length === 0) return noProjects;

  return <EmptyState description={description} icon={Icon.folderAdd} title={title} />;
}
