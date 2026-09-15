import { useEffect, useRef } from "react";

import { useNavigate } from "@tanstack/react-router";

import { showJobCompletionToast } from "@/components/job-completion-toast";
import { useAllEvalRunsQuery } from "@/hooks/use-evaluations";
import { useFinetuningJobsQuery } from "@/hooks/use-finetuning";
import { useOptimizerExperimentsQuery } from "@/hooks/use-optimizer";
import {
  buildRunningJobs,
  completionToastCopy,
  detectCompletedJobs,
  type RunningJobItem,
} from "@/lib/running-jobs";
import {
  clearUnseenCompletions,
  pushUnseenCompletions,
  useUnseenCompletions,
} from "@/lib/unseen-job-completions";

export function navigateToJob(
  navigate: ReturnType<typeof useNavigate>,
  job: RunningJobItem,
  projectId: string
) {
  if (job.kind === "finetuning") {
    void navigate({
      search: (prev) => ({
        ...prev,
        groupId: job.groupId ?? job.id,
        jobId: job.id,
        projectId,
      }),
      to: "/training",
    });
    return;
  }
  if (job.kind === "optimizer") {
    void navigate({
      params: { experimentId: job.id },
      search: (prev) => ({ ...prev, projectId }),
      to: "/optimiser/$experimentId",
    });
    return;
  }
  void navigate({
    params: { runId: job.id },
    search: (prev) => ({ ...prev, projectId }),
    to: "/evaluations/runs/$runId",
  });
}

export function useRunningJobs(projectId: string | undefined) {
  const navigate = useNavigate();
  const ftQuery = useFinetuningJobsQuery(projectId);
  const evalQuery = useAllEvalRunsQuery(projectId);
  const optimizerQuery = useOptimizerExperimentsQuery(projectId);

  const ftResults = ftQuery.data?.results;
  const evalResults = evalQuery.data?.results;
  const optimizerResults = optimizerQuery.data?.results;

  const running = buildRunningJobs({
    evalRuns: evalResults,
    finetuning: ftResults,
    optimizerExperiments: optimizerResults,
  });

  const unseenCompletions = useUnseenCompletions();
  const previouslyActive = useRef(new Map<string, RunningJobItem>());
  const hydratedForProject = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (hydratedForProject.current !== projectId) {
      hydratedForProject.current = undefined;
      previouslyActive.current = new Map();
      clearUnseenCompletions();
    }

    if (!projectId) return;
    if (!ftQuery.isFetched && !evalQuery.isFetched) {
      return;
    }

    const current = buildRunningJobs({
      evalRuns: evalResults,
      finetuning: ftResults,
      optimizerExperiments: optimizerResults,
    });
    const latest = {
      evalRuns: evalResults,
      finetuning: ftResults,
      optimizerExperiments: optimizerResults,
    };

    if (hydratedForProject.current !== projectId) {
      previouslyActive.current = new Map(current.map((j) => [j.key, j]));
      hydratedForProject.current = projectId;
      return;
    }

    const completed = detectCompletedJobs(previouslyActive.current, current, latest);
    previouslyActive.current = new Map(current.map((j) => [j.key, j]));

    if (completed.length === 0) return;

    pushUnseenCompletions(completed);

    for (const job of completed) {
      const { message, description } = completionToastCopy(job);
      showJobCompletionToast({
        body: description,
        onView: () => navigateToJob(navigate, job, projectId),
        title: message,
      });
    }
  }, [
    projectId,
    ftResults,
    evalResults,
    optimizerResults,
    ftQuery.isFetched,
    evalQuery.isFetched,
    navigate,
  ]);

  return {
    clearUnseenCompletions,
    isLoading: !!projectId && (ftQuery.isLoading || evalQuery.isLoading) && running.length === 0,
    running,
    unseenCompletions,
  };
}
