import { useCallback, useEffect } from "react";

import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import type { AssistantEvent, Draft } from "@/components/assistant/types";
import type { DatasetEvent } from "@/hooks/use-datasets";
import type { AssistantThread, ChangeMessageRequest, SendMessageRequest } from "@/openapi";

const threadKey = (datasetId: string) => ["assistant-thread", datasetId];

/** Queues one message on the dataset's conversation, which is opened on first use. */
export async function sendToDataset(
  qc: QueryClient,
  datasetId: string,
  message: SendMessageRequest
) {
  const key = threadKey(datasetId);
  const thread =
    qc.getQueryData<AssistantThread | null>(key) ??
    (await apiClient.assistantThreads.assistantThreadsCreate({
      openThreadRequest: { dataset: datasetId },
    }));
  qc.setQueryData(key, thread);
  await apiClient.assistantThreads.assistantThreadsMessagesCreate({
    id: thread.id,
    sendMessageRequest: message,
  });
}

/** The conversation's events as the page holds them. The log only grows and its sequence
 *  numbers only rise, so adding a read keeps every event once, whatever order or how
 *  often reads arrive. */
export function mergeEvents(held: AssistantEvent[], added: AssistantEvent[]) {
  const last = held.at(-1)?.sequence ?? 0;
  const fresh = added.filter((event) => event.sequence > last);
  return fresh.length ? [...held, ...fresh] : held;
}

const NO_EVENTS: AssistantEvent[] = [];
const eventsKey = (threadId: string | undefined) => ["assistant-events", threadId];
const draftKey = (threadId: string | undefined) => ["assistant-draft", threadId];

/** The dataset's conversation: its thread, its events and the reply being written. The
 *  dataset page's one event stream announces changes, and ``handle`` applies them. Events
 *  and the reply live in the query cache under the thread, so a page that opens again,
 *  mounts twice or reconnects reads only what it does not hold. */
export function useAssistantThread(datasetId: string) {
  const qc = useQueryClient();
  const found = useQuery({
    queryFn: async () =>
      (await apiClient.assistantThreads.assistantThreadsList({ dataset: datasetId }))[0] ?? null,
    queryKey: threadKey(datasetId),
  });
  const thread = found.data ?? null;
  const threadId = thread?.id;

  // The cache keeps one read per thread; a signal during a read starts it again from the
  // newest event held.
  const log = useQuery({
    enabled: !!threadId,
    queryFn: async () => {
      const held = qc.getQueryData<AssistantEvent[]>(eventsKey(threadId)) ?? [];
      const added = (await apiClient.assistantThreads.assistantThreadsEventsList({
        after: held.at(-1)?.sequence ?? 0,
        id: threadId!,
      })) as unknown as AssistantEvent[];
      // A reply or a status ends the text being written.
      if (added.some((event) => event.kind === "assistant" || event.kind === "status"))
        qc.setQueryData(draftKey(threadId), null);
      return mergeEvents(qc.getQueryData<AssistantEvent[]>(eventsKey(threadId)) ?? held, added);
    },
    queryKey: eventsKey(threadId),
  });

  // The server's text of the reply being written; a thread that rests writes none.
  const written = thread?.state === "running" ? thread.draft || null : null;
  useEffect(() => {
    if (!threadId) return;
    qc.setQueryData<Draft | null>(draftKey(threadId), (old) =>
      written === null
        ? null
        : old && old.text.length >= written.length
          ? old
          : { steps: old?.steps ?? [], text: written }
    );
  }, [qc, threadId, written]);
  const draft = useQuery<Draft | null>({
    enabled: false,
    initialData: null,
    queryFn: () => null,
    queryKey: draftKey(threadId),
  }).data;

  const handle = useCallback(
    (event: DatasetEvent) => {
      if (event.type === "assistant" || event.type === "connected") {
        void qc.invalidateQueries({ queryKey: threadKey(datasetId) });
        if (threadId) void qc.refetchQueries({ queryKey: eventsKey(threadId) });
      } else if (event.type === "assistant.draft" && threadId) {
        // A gain names its position: one the text already holds changes nothing.
        qc.setQueryData<Draft | null>(draftKey(threadId), (old) => ({
          steps: event.steps,
          text: (old?.text ?? "").slice(0, event.at) + event.added,
        }));
      }
    },
    [qc, datasetId, threadId]
  );

  const send = useMutation({
    mutationFn: (message: SendMessageRequest) => sendToDataset(qc, datasetId, message),
  });
  const stop = useMutation({
    mutationFn: () => apiClient.assistantThreads.assistantThreadsStopCreate({ id: threadId! }),
  });
  const changeQueued = useMutation({
    mutationFn: (change: ChangeMessageRequest) =>
      apiClient.assistantThreads.assistantThreadsQueueCreate({
        changeMessageRequest: change,
        id: threadId!,
      }),
  });

  return {
    changeQueued,
    draft,
    error: found.error ?? log.error ?? send.error ?? stop.error ?? changeQueued.error,
    events: log.data ?? NO_EVENTS,
    handle,
    send,
    stop,
    thread,
  };
}
