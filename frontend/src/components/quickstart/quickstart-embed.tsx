import { useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { DataProjectForm } from "@/components/onboarding/data-project-form";
import { OnboardingWindow } from "@/components/onboarding/onboarding-window";
import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { OnboardingMilestones } from "@/components/quickstart/onboarding-milestones";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useOnboardingMilestones } from "@/hooks/use-activity";

export function QuickstartEmbed({
  projectId,
  projectName,
  devPreview = false,
  onComplete,
  autoPlay = false,
}: {
  projectId?: string;
  projectName?: string;
  devPreview?: boolean;
  onComplete?: () => void;
  autoPlay?: boolean;
}) {
  const [source, setSource] = useState<"agent" | "data">("agent");
  const navigate = useNavigate();
  const milestones = useOnboardingMilestones(projectId, "agent");
  const connected = milestones.data?.some(
    (step) => step.kind === "agent_connected" && step.completedAt
  );
  if (source === "agent" && (connected || devPreview)) {
    return (
      <section aria-label="Agent onboarding" className="w-full max-w-md">
        <OnboardingMilestones
          autoPlay={autoPlay}
          devPreview={devPreview}
          onComplete={onComplete}
          projectId={projectId}
          projectName={projectName}
        />
      </section>
    );
  }

  return (
    <section aria-label="Get started" className="min-w-0 w-full max-w-2xl">
      <OnboardingWindow title="Onboarding">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-sm font-medium">Overmind works best via the MCP</h2>
            <p className="text-xs text-muted-foreground">
              Connect your coding agent or upload data to get started.
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
          <OnboardWithAiPanel compact />
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
