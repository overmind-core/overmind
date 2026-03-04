import { useMemo, useState } from "react";

import { useMutation } from "@tanstack/react-query";
import { createFileRoute, Navigate, useNavigate } from "@tanstack/react-router";
import { Loader as Loader2, Sparkles } from "pixelarticons/react";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useProjectsList } from "@/hooks/use-projects";
import { useOnboardingQuery } from "@/hooks/use-query";

export const Route = createFileRoute("/_auth/onboarding")({
  component: OnboardingPage,
});

function OnboardingPage() {
  const { isLoading, data } = useOnboardingQuery();

  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (data) {
    return <Navigate replace to="/" />;
  }

  return (
    <div className="mx-auto w-full max-w-lg space-y-6 py-12">
      <div className="text-center">
        <div className="mb-3 inline-flex items-center justify-center rounded-full bg-primary/10 p-3">
          <Sparkles className="size-6 text-primary" />
        </div>
        <h1 className="text-2xl font-bold tracking-tight">
          Welcome to Overmind
        </h1>
        <p className="mt-2 text-muted-foreground">
          Tell us about your AI agent so we can tailor the experience for you.
        </p>
      </div>
      <ProjectDescriptionForm />
    </div>
  );
}

function ProjectDescriptionForm() {
  const navigate = useNavigate();
  const [description, setDescription] = useState("");
  const { data: projectsData, isLoading: projectsLoading } = useProjectsList();

  const defaultProject = useMemo(
    () => projectsData?.projects?.[0],
    [projectsData],
  );

  const submitMutation = useMutation({
    mutationFn: async () => {
      const trimmed = description.trim();

      if (defaultProject && trimmed) {
        await apiClient.projects.updateProjectApiV1IamProjectsProjectIdPut({
          projectId: defaultProject.projectId,
          updateProjectRequest: { description: trimmed },
        });
      }

      await apiClient.onboarding.createUserOnboardingApiV1OnboardingPost({
        userOnboardingRequest: {
          description: trimmed || "",
          priorities: [],
        },
      });
    },
    onSuccess: () => {
      navigate({ to: "/" });
    },
  });

  const canSubmit =
    !projectsLoading && !submitMutation.isPending && description.trim().length > 0;

  return (
    <Card>
      <CardContent className="space-y-5 pt-6">
        <div className="space-y-2">
          <Label className="text-base font-semibold" htmlFor="project-description">
            What does your agent do?
          </Label>
          <Textarea
            autoFocus
            className="min-h-[120px] resize-none"
            id="project-description"
            onChange={(e) => setDescription(e.target.value)}
            placeholder="E.g. Summarizes legal contracts, answers customer support questions, generates code reviews..."
            rows={5}
            value={description}
          />
        </div>

        <div className="flex items-center justify-between gap-3">
          <Button
            onClick={() => {
              setDescription("");
              submitMutation.mutate();
            }}
            size="sm"
            variant="ghost"
          >
            Skip
          </Button>
          <Button
            disabled={!canSubmit}
            onClick={() => submitMutation.mutate()}
          >
            {submitMutation.isPending && (
              <Loader2 className="mr-2 size-4 animate-spin" />
            )}
            Continue
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
