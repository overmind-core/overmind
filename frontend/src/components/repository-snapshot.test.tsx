// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { RepositorySnapshot } from "@/components/repository-snapshot";
import type { AgentGraph } from "@/openapi";

afterEach(cleanup);

const graph: AgentGraph = {
  capabilities: [],
  edges: [],
  lastSyncedAt: new Date("2026-09-27T12:00:00Z"),
  project: "project",
  repositorySnapshot: {
    branch: "main",
    commit: "a".repeat(40),
    directory: ".",
    dirty: true,
    fingerprint: "b".repeat(64),
    repository: "acme/agent",
    scannedAt: new Date("2026-09-20T12:00:00Z"),
  },
};

it("displays repository, branch and scan time without a dropdown", () => {
  render(<RepositorySnapshot graph={graph} />);
  expect(screen.queryByText("Uncommitted changes")).toBeNull();
  expect(screen.queryByText("aaaaaaa")).toBeNull();
  expect(screen.getByText("acme/agent")).toBeTruthy();
  expect(screen.getByText("main")).toBeTruthy();
  expect(screen.getByText(/^Scanned /)).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});

it("does not label an unversioned upload as a known revision", () => {
  render(<RepositorySnapshot graph={{ ...graph, repositorySnapshot: null }} />);
  expect(screen.getByText("Revision unavailable")).toBeTruthy();
  expect(screen.queryByText("main")).toBeNull();
});

it("distinguishes a new project from a detached checkout", () => {
  const { rerender } = render(
    <RepositorySnapshot graph={{ ...graph, lastSyncedAt: null, repositorySnapshot: null }} />
  );
  expect(screen.getByText("No repository scan")).toBeTruthy();
  rerender(
    <RepositorySnapshot
      graph={{
        ...graph,
        repositorySnapshot: { ...graph.repositorySnapshot!, branch: "", dirty: false },
      }}
    />
  );
  expect(screen.getByText("Detached HEAD")).toBeTruthy();
  expect(screen.queryByText("Uncommitted changes")).toBeNull();
});
