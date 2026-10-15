import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { notify } from "@/lib/notify";
import type { PatchedSignalSubscriptionRequest, SignalSubscriptionRequest } from "@/openapi";

export const useSignalCatalogQuery = () =>
  useQuery({
    queryFn: () => apiClient.signalSubscriptions.signalSubscriptionsCatalogList(),
    queryKey: ["signal-catalog"],
    staleTime: 60 * 60_000,
  });

export const useSignalSubscriptionsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.signalSubscriptions.signalSubscriptionsList({ pageSize: 200, project: projectId }),
    queryKey: ["signal-subscriptions", projectId],
  });

export const useCreateSignalSubscriptionMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SignalSubscriptionRequest) =>
      apiClient.signalSubscriptions.signalSubscriptionsCreate({ signalSubscriptionRequest: input }),
    onError: (e) => notify.error(e, "Couldn't subscribe to the signal"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["signal-subscriptions", projectId] }),
  });
};

export const useUpdateSignalSubscriptionMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: PatchedSignalSubscriptionRequest }) =>
      apiClient.signalSubscriptions.signalSubscriptionsPartialUpdate({
        id,
        patchedSignalSubscriptionRequest: body,
      }),
    onError: (e) => notify.error(e, "Couldn't update the subscription"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["signal-subscriptions", projectId] }),
  });
};

export const useDeleteSignalSubscriptionMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.signalSubscriptions.signalSubscriptionsDestroy({ id }),
    onError: (e) => notify.error(e, "Couldn't remove the subscription"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["signal-subscriptions", projectId] }),
  });
};

export const useSignalEventsQuery = (
  projectId: string | undefined,
  filters: { signalType?: string; unseen?: boolean; page?: number; session?: string } = {}
) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.signalEvents.signalEventsList({
        conversation: filters.session,
        page: filters.page,
        pageSize: 50,
        project: projectId,
        signalType: filters.signalType || undefined,
        unseen: filters.unseen || undefined,
      }),
    queryKey: [
      "signal-events",
      projectId,
      filters.session ?? null,
      filters.signalType ?? null,
      !!filters.unseen,
      filters.page ?? 1,
    ],
    refetchInterval: 30_000,
  });

export const useUnseenSignalCountQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.signalEvents.signalEventsList({ pageSize: 1, project: projectId, unseen: true }),
    queryKey: ["signal-events", projectId, "unseen-count"],
    refetchInterval: 30_000,
    select: (data) => data.count,
  });

export const useMarkSignalsSeenMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids?: string[]) =>
      apiClient.signalEvents.signalEventsMarkSeenCreate({
        signalEventSeenRequest: { ids, project: projectId! },
      }),
    onError: (e) => notify.error(e, "Couldn't mark signals seen"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["signal-events", projectId] }),
  });
};
