import { useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { DataProjectForm } from "@/components/onboarding/data-project-form";
import { OnboardingWindow } from "@/components/onboarding/onboarding-window";
import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { OnboardingMilestones } from "@/components/quickstart/onboarding-milestones";
import { LoadingState } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useOnboardingMilestones } from "@/hooks/use-activity";

export function QuickstartEmbed({
  projectId,
  projectName,
}: {
  projectId?: string;
  projectName?: string;
}) {
  const [source, setSource] = useState<"agent" | "data">("agent");
  const navigate = useNavigate();
  const milestones = useOnboardingMilestones(projectId, "agent");
  const connected = milestones.data?.some(
    (step) => step.kind === "agent_connected" && step.completedAt
  );
  if (projectId && milestones.isLoading) return <LoadingState label="Loading setup progress" />;
  if (source === "agent" && connected) {
    return (
      <section aria-label="Agent onboarding" className="w-full max-w-md">
        <OnboardingMilestones projectId={projectId} projectName={projectName} />
      </section>
    );
  }

  return (
    <section aria-label="Get started" className="min-w-0 w-full max-w-2xl">
      <OnboardingWindow title="Onboarding">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-sm font-medium">Set up this project</h2>
            <p className="text-xs text-muted-foreground">
              Paste the prompt into a coding agent, or upload data.
            </p>
          </div>
          <Tabs
            className="w-auto shrink-0"
            onValueChange={(value) => setSource(value as "agent" | "data")}
            value={source}
          >
            <TabsList>
              <TabsTrigger value="agent">Agent</TabsTrigger>
              <TabsTrigger value="data">Data</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        {source === "agent" ? (
          <OnboardWithAiPanel compact key={projectId ?? "new"} projectId={projectId} />
        ) : (
          <DataProjectForm
            onCreated={({ projectId: createdProjectId, datasetId }) =>
              void navigate({
                params: { datasetId },
                search: { prepare: "training", projectId: createdProjectId },
                to: "/datasets/$datasetId",
              })
            }
            project={projectId && projectName ? { id: projectId, name: projectName } : undefined}
          />
        )}
      </OnboardingWindow>
    </section>
  );
}
