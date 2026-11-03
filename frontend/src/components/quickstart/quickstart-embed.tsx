import { useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { DataProjectForm } from "@/components/onboarding/data-project-form";
import { OnboardingWindow } from "@/components/onboarding/onboarding-window";
import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { OnboardingMilestones } from "@/components/quickstart/onboarding-milestones";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useOnboardingMilestones } from "@/hooks/use-activity";

export function QuickstartEmbed({
  projectId,
  projectName,
  initialSource = "agent",
}: {
  initialSource?: "agent" | "data";
  projectId?: string;
  projectName?: string;
}) {
  const [source, setSource] = useState<"agent" | "data">(initialSource);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const milestones = useOnboardingMilestones(projectId, "agent");
  const connected = milestones.data?.some(
    (step) => step.kind === "agent_connected" && step.completedAt
  );

  return (
    <section aria-label="Get started" className="min-w-0 w-full max-w-2xl">
      <OnboardingWindow title="Onboarding">
        <Tabs onValueChange={(value) => setSource(value as "agent" | "data")} value={source}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="space-y-1">
              <h2 className="text-sm font-medium">Set up this project</h2>
              <p className="text-xs text-muted-foreground">
                Paste the prompt into a coding agent, or upload data.
              </p>
            </div>
            <TabsList>
              <TabsTrigger disabled={busy} value="agent">
                Agent
              </TabsTrigger>
              <TabsTrigger disabled={busy} value="data">
                Data
              </TabsTrigger>
            </TabsList>
          </div>
          <TabsContent className="data-[state=inactive]:hidden" forceMount value="agent">
            {connected ? (
              <OnboardingMilestones projectId={projectId} projectName={projectName} />
            ) : (
              <div className="space-y-3">
                {projectId && milestones.isLoading && (
                  <LoadingState label="Loading setup progress" />
                )}
                {milestones.error && (
                  <Alert variant="destructive">
                    Couldn’t load setup progress.
                    <Button
                      disabled={milestones.isFetching}
                      onClick={() => void milestones.refetch()}
                      variant="outline"
                    >
                      Retry
                    </Button>
                  </Alert>
                )}
                <OnboardWithAiPanel compact key={projectId ?? "new"} projectId={projectId} />
              </div>
            )}
          </TabsContent>
          <TabsContent className="data-[state=inactive]:hidden" forceMount value="data">
            <DataProjectForm
              onBusyChange={setBusy}
              onCreated={({ projectId: createdProjectId, datasetId }) =>
                void navigate({
                  params: { datasetId },
                  search: { prepare: "training", projectId: createdProjectId },
                  to: "/datasets/$datasetId",
                })
              }
              project={projectId && projectName ? { id: projectId, name: projectName } : undefined}
            />
          </TabsContent>
        </Tabs>
      </OnboardingWindow>
    </section>
  );
}
