import { useEffect, useRef, useState } from "react";

import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import type { AssistantEvent, Draft } from "@/components/assistant/types";
import { invalidateDataset } from "@/hooks/use-datasets";
import {
  type AssistantThread,
  AssistantThreadFromJSON,
  type ChangeMessageRequest,
  ResponseError,
  type SendMessageRequest,
} from "@/openapi";

const RECONNECT_MS = { first: 1000, longest: 30_000 };

interface Stream {
  events: AssistantEvent[];
  thread: AssistantThread | null;
  draft: Draft | null;
  /** Set when the server refused the stream: no reconnect can succeed. */
  error: unknown;
}

const EMPTY: Stream = { draft: null, error: null, events: [], thread: null };

const threadKey = (datasetId: string) => ["assistant-thread", datasetId];

/** Queues one message on the dataset's thread, which is opened on first use. */
export async function sendToDataset(
  qc: QueryClient,
  projectId: string,
  datasetId: string,
  message: SendMessageRequest
) {
  const key = threadKey(datasetId);
  const thread =
    qc.getQueryData<AssistantThread | null>(key) ??
    (await apiClient.assistantThreads.assistantThreadsCreate({
      openThreadRequest: { project: projectId, subject: { kind: "dataset", record: datasetId } },
    }));
  qc.setQueryData(key, thread);
  await apiClient.assistantThreads.assistantThreadsMessagesCreate({
    id: thread.id,
    sendMessageRequest: message,
  });
}

function usePageVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

/** One thread's events, queue and the reply being written, over a single stream that
 *  resumes after the last event it holds. A hidden tab holds no connection. */
function useThreadStream(threadId: string | undefined, datasetId: string): Stream {
  const qc = useQueryClient();
  const visible = usePageVisible();
  const [stream, setStream] = useState<{ id?: string } & Stream>(EMPTY);
  const current = stream.id === threadId ? stream : EMPTY;
  // Survives a hidden tab: the next connection resumes after the last event shown.
  const seen = useRef<{ id?: string; sequence: number }>({ sequence: 0 });

  useEffect(() => {
    if (!threadId || !visible) return;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController;
    let wait = RECONNECT_MS.first;
    if (seen.current.id !== threadId) seen.current = { id: threadId, sequence: 0 };
    const update = (change: (old: Stream) => Partial<Stream>) =>
      setStream((old) => {
        const base = old.id === threadId ? old : EMPTY;
        return { ...base, ...change(base), id: threadId };
      });

    const connect = async () => {
      controller = new AbortController();
      // A connection first replays what this tab missed, then sends the thread. A replay
      // on a page that just loaded changed nothing the page has not read.
      const resumed = seen.current.sequence > 0;
      let live = false;
      let missedWork = false;
      const apply = (frame: {
        type: string;
        event?: AssistantEvent;
        thread?: unknown;
        text?: string;
        added?: string;
        at?: number;
        tools?: string[];
      }) => {
        if (frame.type === "event" && frame.event) {
          const event = frame.event;
          if (event.sequence <= seen.current.sequence) return;
          seen.current.sequence = event.sequence;
          if (event.kind === "tool_result") {
            if (live) invalidateDataset(qc, datasetId);
            else missedWork = resumed;
          }
          update((old) => ({
            draft: event.kind === "tool_result" ? old.draft : null,
            events: [...old.events, event],
          }));
        } else if (frame.type === "thread") {
          if (!live && missedWork) invalidateDataset(qc, datasetId);
          live = true;
          update(() => ({ thread: AssistantThreadFromJSON(frame.thread) }));
        } else if (frame.type === "draft") {
          // A stream that opens in the middle of a reply gets its text so far, then what it
          // gains. A gain names its position: one the text already holds changes nothing.
          update((old) => ({
            draft: {
              text: frame.text ?? (old.draft?.text ?? "").slice(0, frame.at) + (frame.added ?? ""),
              tools: frame.tools ?? old.draft?.tools ?? [],
            },
          }));
        }
      };
      try {
        const response = await apiClient.assistantThreads.assistantThreadsEventsRetrieveRaw(
          { after: seen.current.sequence, id: threadId },
          { signal: controller.signal }
        );
        wait = RECONNECT_MS.first;
        update(() => ({ error: null }));
        const reader = response.raw.body?.getReader();
        if (!reader) throw new Error("Assistant stream unavailable");
        const decoder = new TextDecoder();
        let buffer = "";
        while (!stopped) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames)
            if (frame.startsWith("data: ")) apply(JSON.parse(frame.slice(6)));
        }
      } catch (error) {
        if (stopped) return;
        if (error instanceof ResponseError && error.response.status < 500) {
          update(() => ({ error }));
          return;
        }
      }
      if (stopped) return;
      retry = setTimeout(connect, wait);
      wait = Math.min(wait * 2, RECONNECT_MS.longest);
    };
    void connect();
    return () => {
      stopped = true;
      controller?.abort();
      if (retry) clearTimeout(retry);
    };
  }, [datasetId, qc, threadId, visible]);

  return current;
}

export function useAssistantThread(projectId: string, datasetId: string) {
  const qc = useQueryClient();
  const found = useQuery({
    queryFn: async () =>
      (
        await apiClient.assistantThreads.assistantThreadsList({
          kind: "dataset",
          record: datasetId,
        })
      )[0] ?? null,
    queryKey: threadKey(datasetId),
  });
  const threadId = found.data?.id;
  const stream = useThreadStream(threadId, datasetId);

  const send = useMutation({
    mutationFn: (message: SendMessageRequest) => sendToDataset(qc, projectId, datasetId, message),
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
    ...stream,
    changeQueued,
    error: found.error ?? stream.error ?? send.error ?? stop.error ?? changeQueued.error,
    send,
    stop,
    thread: stream.thread ?? found.data ?? null,
  };
}
