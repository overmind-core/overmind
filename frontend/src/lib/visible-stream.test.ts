// @vitest-environment jsdom

// covers: console-logic
import { afterEach, expect, it, vi } from "vitest";

import { whilePageVisible } from "./visible-stream";

afterEach(() => vi.restoreAllMocks());

it("releases background connections, resumes once, and stops listening after unmount", () => {
  let visibility = "hidden";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(
    () => visibility as DocumentVisibilityState
  );
  const connections: AbortController[] = [];
  const dispose = whilePageVisible(() => {
    const connection = new AbortController();
    connections.push(connection);
    return () => connection.abort();
  });
  const show = (state: string) => {
    visibility = state;
    document.dispatchEvent(new Event("visibilitychange"));
  };
  expect(connections).toHaveLength(0);
  show("visible");
  show("visible");
  expect(connections).toHaveLength(1);
  expect(connections[0].signal.aborted).toBe(false);
  show("hidden");
  expect(connections[0].signal.aborted).toBe(true);
  show("visible");
  expect(connections).toHaveLength(2);
  expect(connections[1].signal.aborted).toBe(false);
  dispose();
  expect(connections[1].signal.aborted).toBe(true);
  show("hidden");
  show("visible");
  expect(connections).toHaveLength(2);
});
