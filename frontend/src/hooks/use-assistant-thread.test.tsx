// @vitest-environment jsdom
// covers: console-logic

import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { useAssistantThread } from "@/hooks/use-assistant-thread";

const held = vi.hoisted(
  (): { kind: string; payload: object; presentation: null; sequence: number }[] => [
    { kind: "user", payload: { text: "Prepare it." }, presentation: null, sequence: 1 },
    { kind: "assistant", payload: { calls: [], text: "Done." }, presentation: null, sequence: 2 },
  ]
);

vi.mock("@/client", () => ({
  default: {
    assistantThreads: {
      assistantThreadsEventsList: vi.fn(({ after }: { after: number }) =>
        Promise.resolve(held.filter((event) => event.sequence > after))
      ),
      assistantThreadsList: vi.fn(() => Promise.resolve([{ id: "thread" }])),
    },
  },
}));

const api = vi.mocked((await import("@/client")).default.assistantThreads);

it("shows each event once when the page opens a conversation it read before", async () => {
  const client = new QueryClient();
  client.setQueryData(["assistant-thread", "dataset"], { id: "thread" });
  // Development mounts each effect twice, as a page opened again from the cache does.
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useAssistantThread("dataset"), {
    reactStrictMode: true,
    wrapper,
  });
  await waitFor(() => expect(result.current.events.length).toBeGreaterThan(0));
  await new Promise((settle) => setTimeout(settle, 50));
  expect(result.current.events.map((event) => event.sequence)).toEqual([1, 2]);
});

it("reads only the events it does not hold when the conversation opens again", async () => {
  const client = new QueryClient();
  client.setQueryData(["assistant-thread", "dataset"], { id: "thread" });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const first = renderHook(() => useAssistantThread("dataset"), { wrapper });
  await waitFor(() => expect(first.result.current.events).toHaveLength(2));
  first.unmount();
  held.push({ kind: "status", payload: { state: "stopped" }, presentation: null, sequence: 3 });
  api.assistantThreadsEventsList.mockClear();

  const again = renderHook(() => useAssistantThread("dataset"), { wrapper });
  expect(again.result.current.events.map((event) => event.sequence)).toEqual([1, 2]);
  await waitFor(() => expect(again.result.current.events).toHaveLength(3));
  expect(api.assistantThreadsEventsList).toHaveBeenCalledWith({ after: 2, id: "thread" });
});
