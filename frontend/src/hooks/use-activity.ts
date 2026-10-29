import { useEffect } from "react";

import {
  type InfiniteData,
  skipToken,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useSearch } from "@tanstack/react-router";

import apiClient from "@/client";
import { useAuthContext } from "@/contexts/auth-context";
import { type ActivityFeed, ActivityFeedFromJSON, type ActivitySeenRequest } from "@/openapi";

export function useActivity(projectId?: string) {
  const { isSignedIn, isGuest } = useAuthContext();
  useLiveActivity(projectId, isSignedIn && !isGuest);
  return useInfiniteQuery({
    enabled: isSignedIn && !isGuest,
    getNextPageParam: (page: ActivityFeed) => page.nextOffset ?? undefined,
    initialPageParam: 0,
    queryFn: ({ pageParam }): Promise<ActivityFeed> =>
      apiClient.activity.activityList({ offset: pageParam, project: projectId }),
    queryKey: ["activity", projectId],
    refetchInterval: 30000,
  });
}

export function useMarkActivitySeen() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (activitySeenRequest: ActivitySeenRequest) =>
      apiClient.activity.activityMarkSeenCreate({ activitySeenRequest }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["activity"] }),
  });
}

export function useOnboardingMilestones(projectId: string | undefined, path: "agent" | "data") {
  const { isSignedIn, isGuest } = useAuthContext();
  return useQuery({
    enabled: isSignedIn && !isGuest,
    queryFn: () => apiClient.activity.activityOnboardingList({ path, project: projectId }),
    queryKey: ["onboarding-milestones", projectId, path],
    refetchInterval: 30000,
  });
}

export function useActivityActor(kind: string, id: string | undefined) {
  const { projectId } = useSearch({ shouldThrow: false, strict: false }) ?? {};
  return useQuery({
    queryFn: skipToken,
    queryKey: ["activity", projectId],
    select: (data: InfiniteData<ActivityFeed>) =>
      data.pages
        .flatMap((page) => page.results)
        .find((event) => event.taskKind === kind && event.taskId === id)?.actorKind,
  }).data;
}

const highlightKey = ["activity-highlights"];
const liveQueries = new Set([
  "activity",
  "onboarding-milestones",
  "datasets",
  "agent-graph",
  "capability-detail",
  "eval-matrix",
  "eval-sets",
  "eval-capabilities",
  "eval-runs",
  "eval-run",
  "finetuning-runs",
  "finetuning-jobs",
  "finetuning-job",
  "finetuning-run-jobs",
  "finetuning-loss-curves",
]);

export function useActivityHighlights() {
  return useQuery<Record<string, number>>({
    enabled: false,
    initialData: {},
    queryFn: async () => ({}),
    queryKey: highlightKey,
  }).data;
}

function useLiveActivity(projectId: string | undefined, enabled: boolean) {
  const client = useQueryClient();
  useEffect(() => {
    if (!enabled || !projectId) return;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let clear: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController;
    let previous: Map<string, number> | undefined;
    client.setQueryData(highlightKey, {});
    const apply = async (feed: ActivityFeed) => {
      const next = new Map(feed.results.map((event) => [event.id, event.updatedAt.getTime()]));
      const changes = previous
        ? feed.results.filter((event) => previous?.get(event.id) !== event.updatedAt.getTime())
        : [];
      const reconcileAssets = !previous || changes.length > 0;
      previous = next;
      await client.invalidateQueries({
        predicate: (query) =>
          query.queryKey[0] === "activity" ||
          query.queryKey[0] === "onboarding-milestones" ||
          (reconcileAssets && liveQueries.has(String(query.queryKey[0]))),
      });
      if (stopped || !changes.length) return;
      const marks: Record<string, number> = {};
      for (const event of changes) {
        marks[`activity:${event.id}`] = Date.now();
        for (const asset of event.assets) marks[`${asset.kind}:${asset.id}`] = Date.now();
      }
      client.setQueryData(highlightKey, (old: Record<string, number> = {}) => ({
        ...old,
        ...marks,
      }));
      if (clear) clearTimeout(clear);
      clear = setTimeout(() => client.setQueryData(highlightKey, {}), 2400);
    };
    const connect = async () => {
      controller = new AbortController();
      try {
        const response = await apiClient.activity.activityEventsRetrieveRaw(
          { project: projectId },
          { signal: controller.signal }
        );
        const reader = response.raw.body?.getReader();
        if (!reader) throw new Error("Activity stream unavailable");
        const decoder = new TextDecoder();
        let buffer = "";
        while (!stopped) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            if (frame.startsWith("data: "))
              await apply(ActivityFeedFromJSON(JSON.parse(frame.slice(6))));
          }
        }
      } catch {
        // Reconcile cached assets while the stream reconnects.
        if (!stopped)
          await client.invalidateQueries({
            predicate: (query) => liveQueries.has(String(query.queryKey[0])),
          });
      }
      if (!stopped) retry = setTimeout(connect, 1500);
    };
    void connect();
    return () => {
      stopped = true;
      controller?.abort();
      if (retry) clearTimeout(retry);
      if (clear) clearTimeout(clear);
      client.setQueryData(highlightKey, {});
    };
  }, [client, enabled, projectId]);
}
