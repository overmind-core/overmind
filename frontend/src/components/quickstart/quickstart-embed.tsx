import { useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { DataProjectForm } from "@/components/onboarding/data-project-form";
import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { OnboardingMilestones } from "@/components/quickstart/onboarding-milestones";
import { Card } from "@/components/ui/card";
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
    <section aria-labelledby="agent-onboarding-title" className="w-full max-w-2xl">
      <h2
        className="relative z-10 -mb-px ml-4 w-fit rounded-t-md border border-b-0 border-border bg-card px-4 py-2 text-sm font-medium"
        id="agent-onboarding-title"
      >
        Get started
      </h2>
      <Card className="p-4">
        <Tabs
          className="mb-4"
          onValueChange={(value) => setSource(value as "agent" | "data")}
          value={source}
        >
          <TabsList>
            <TabsTrigger value="agent">Agent</TabsTrigger>
            <TabsTrigger value="data">Data</TabsTrigger>
          </TabsList>
        </Tabs>
        {source === "agent" ? (
          <OnboardWithAiPanel />
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
      </Card>
    </section>
  );
}
