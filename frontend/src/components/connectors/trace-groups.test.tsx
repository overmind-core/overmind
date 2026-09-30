// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ConnectorReviewDialog } from "./trace-groups";

const calls = vi.hoisted(() => ({ capabilities: vi.fn(), groups: vi.fn(), review: vi.fn() }));
vi.mock("@/client", () => ({
  default: {
    capabilities: { capabilitiesList: calls.capabilities },
    connectorCredentials: {
      connectorCredentialsGroupsList: calls.groups,
      connectorCredentialsReviewCreate: calls.review,
    },
  },
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="/trace">{children}</a>,
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function openReview() {
  HTMLElement.prototype.scrollIntoView = vi.fn();
  calls.groups.mockResolvedValue({
    next: null,
    results: [
      {
        capabilityId: null,
        evidence: { spanCount: 2, tools: ["search"] },
        id: "search",
        name: "Answer questions",
        revision: 4,
        sampleTraceIds: [],
        traceCount: 80,
        unreviewedTraceCount: 80,
      },
      {
        capabilityId: null,
        evidence: { spanCount: 2, tools: ["refund"] },
        id: "refund",
        name: "Refund requests",
        revision: 2,
        sampleTraceIds: [],
        traceCount: 30,
        unreviewedTraceCount: 30,
      },
    ],
  });
  calls.capabilities.mockResolvedValue({
    next: null,
    results: [{ id: "answer", name: "Answer", status: "current" }],
  });
  const close = vi.fn();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ConnectorReviewDialog
        credentialId="connector"
        name="Support"
        onClose={close}
        open
        projectId="project"
      />
    </QueryClientProvider>
  );
  return close;
}

it("stages dropdown choices and confirms all rows, including unassigned, together", async () => {
  calls.review.mockResolvedValue([]);
  const close = openReview();
  const select = await screen.findByRole("combobox", { name: "Capability for Answer questions" });
  fireEvent.keyDown(select, { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("option", { name: "Answer" }));
  expect(calls.review).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm all assignments" }));
  await waitFor(() =>
    expect(calls.review).toHaveBeenCalledWith({
      id: "connector",
      reviewTraceGroupsRequest: {
        assignments: [
          { capabilityId: "answer", expectedRevision: 4, groupId: "search" },
          { capabilityId: null, expectedRevision: 2, groupId: "refund" },
        ],
      },
    })
  );
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
});

it("keeps the dialog open when a batch is rejected and allows a fresh review", async () => {
  calls.review.mockRejectedValue(
    new Error("This group changed. Refresh it before assigning traces.")
  );
  const close = openReview();
  await screen.findByRole("combobox", { name: "Capability for Answer questions" });
  fireEvent.click(screen.getByRole("button", { name: "Confirm all assignments" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Reload groups" })).toBeTruthy();
});
