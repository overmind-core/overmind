import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { LoadingState } from "@/components/ui/spinner";
import { useProjectsList } from "@/hooks/use-projects";

export function NoProjectsEmptyState() {
  return (
    <div className="mx-auto w-full max-w-2xl space-y-6">
      <EmptyState
        description="Paste the onboarding prompt in your coding agent. A project appears here after overmind sync."
        icon={Icon.folderAdd}
        title="Set up from your repository"
      />
      <div className="overflow-hidden rounded-md border border-border p-4">
        <OnboardWithAiPanel
          showManualSetup={false}
          waitingHint="This page refreshes when init sync creates your first project."
        />
      </div>
    </div>
  );
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
    return <LoadingState fullPage label="Loading projects" />;
  }

  if (projects.length === 0) {
    return <NoProjectsEmptyState />;
  }

  return <EmptyState description={description} icon={Icon.folderAdd} title={title} />;
}
