import { useEffect, useState } from "react";

import api from "@/client";
import { whilePageVisible } from "@/lib/visible-stream";
import { WorkshopHistoryFromJSON, type WorkshopMessage } from "@/openapi";

export function useWorkshopConversation(project: string, dataset: string) {
  const [messages, setMessages] = useState<WorkshopMessage[]>([]);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const retained = new Map<string, WorkshopMessage>();
    let cursor = 0;
    setMessages([]);
    setConnected(false);
    return whilePageVisible(() => {
      const controller = new AbortController();
      let timer: ReturnType<typeof setTimeout>;
      const connect = async () => {
        try {
          const response = await api.workshopTasks.workshopTasksEventsRetrieveRaw(
            { after: cursor, dataset, project },
            { signal: controller.signal }
          );
          const reader = response.raw.body?.getReader();
          if (!reader) throw new Error("The conversation stream is unavailable.");
          setConnected(true);
          const decoder = new TextDecoder();
          let buffer = "";
          while (!controller.signal.aborted) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const frames = buffer.split("\n\n");
            buffer = frames.pop() ?? "";
            for (const frame of frames) {
              const data = frame
                .split("\n")
                .filter((line) => line.startsWith("data: "))
                .map((line) => line.slice(6))
                .join("\n");
              if (!data) continue;
              const batch = WorkshopHistoryFromJSON(JSON.parse(data));
              for (const event of batch.events) {
                if (event.id <= cursor) continue;
                const id = `${event.task}:${event.message.id}`;
                retained.set(id, { ...event.message, id });
              }
              cursor = Math.max(cursor, batch.cursor);
              setMessages([...retained.values()]);
            }
          }
        } catch {
          if (!controller.signal.aborted) setConnected(false);
        } finally {
          if (!controller.signal.aborted) timer = setTimeout(connect, 1000);
        }
      };
      void connect();
      return () => {
        controller.abort();
        clearTimeout(timer);
        setConnected(false);
      };
    });
  }, [project, dataset]);
  return { connected, messages };
}
