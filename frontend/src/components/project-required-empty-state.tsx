import { Navigate } from "@tanstack/react-router";

import { QuickstartEmbed } from "@/components/quickstart/quickstart-embed";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectsList } from "@/hooks/use-projects";

export function NoProjectsEmptyState() {
  return <QuickstartEmbed />;
}

export function ProjectRequiredEmptyState({
  title = "Please select a project",
  description = "Select a project from the selector in the top bar to see this page.",
}: {
  title?: string;
  description?: string;
}) {
  const { data, isLoading } = useProjectsList();
  const projects = data?.projects ?? [];

  if (isLoading) {
    return (
      <div className="flex min-h-[400px] flex-1 flex-col items-center justify-center gap-3">
        <Skeleton className="h-12 w-12 rounded-sm" />
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-80" />
      </div>
    );
  }

  if (projects.length === 0) {
    return <Navigate replace search={{ projectId: undefined }} to="/" />;
  }

  return <EmptyState description={description} icon={Icon.folderAdd} title={title} />;
}
