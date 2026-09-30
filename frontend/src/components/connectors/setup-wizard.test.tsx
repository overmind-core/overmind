// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { addDays, format, startOfDay } from "date-fns";
import { afterEach, expect, it, vi } from "vitest";

import { CONNECTORS } from "@/components/connectors";
import { ConnectorSetupWizard } from "./setup-wizard";

const calls = vi.hoisted(() => ({
  confirm: vi.fn(),
  create: vi.fn(),
  poll: vi.fn(),
  preview: vi.fn(),
  verify: vi.fn(),
}));
vi.mock("@/client", () => ({
  default: {
    connectorCredentials: {
      connectorCredentialsCreate: calls.create,
      connectorCredentialsImportCreate: calls.confirm,
      connectorCredentialsPreviewCreate: calls.preview,
      connectorCredentialsPreviewRetrieve: calls.poll,
      connectorCredentialsVerifyCreate: calls.verify,
    },
  },
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("verifies credentials, counts the chosen range and imports only after confirmation", async () => {
  calls.create.mockResolvedValue({ id: "credential" });
  calls.verify.mockResolvedValue({ ok: true, projects: [{ id: "source", name: "Agent" }] });
  const preview = {
    estimatedSecondsMax: 90,
    estimatedSecondsMin: 30,
    expiresAt: new Date(Date.now() + 60000),
    id: "preview",
    spanCount: 1200,
    status: "ready",
    traceCount: 240,
    windowFrom: new Date("2026-08-30T00:00:00Z"),
    windowTo: new Date("2026-09-30T00:00:00Z"),
  };
  calls.preview.mockResolvedValue(preview);
  calls.poll.mockResolvedValue(preview);
  calls.confirm.mockResolvedValue(preview);
  const completed = vi.fn();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ConnectorSetupWizard
        meta={CONNECTORS[0]}
        onClose={() => {}}
        onCompleted={completed}
        open
        projectId="project"
      />
    </QueryClientProvider>
  );
  fireEvent.change(screen.getByLabelText("Public key"), { target: { value: "pk-test" } });
  fireEvent.change(screen.getByLabelText("Secret key"), { target: { value: "sk-test" } });
  fireEvent.click(screen.getByRole("button", { name: "Load sources" }));
  const source = await screen.findByRole("combobox", { name: "Source project" });
  await waitFor(() => expect(source.textContent).toContain("Agent"));
  expect(screen.getByRole("combobox", { name: "Time range" })).toBeTruthy();
  expect(screen.getByLabelText("Public key")).toBeTruthy();
  expect(calls.preview).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Count traces" }) as HTMLButtonElement).disabled
    ).toBe(false)
  );
  HTMLElement.prototype.scrollIntoView = vi.fn();
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Time range" }), { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("option", { name: "Custom range" }));
  const day = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  const dayButton = screen.getByRole("button", { name: new RegExp(format(day, "MMMM do, yyyy")) });
  fireEvent.click(dayButton);
  fireEvent.click(dayButton);
  fireEvent.click(screen.getByRole("button", { name: "Count traces" }));
  expect(await screen.findByRole("button", { name: "Import 240 traces" })).toBeTruthy();
  expect(calls.confirm).not.toHaveBeenCalled();
  expect(calls.preview.mock.calls[0][0].importRangeRequest.sourceProjectId).toBe("source");
  expect(calls.preview.mock.calls[0][0].importRangeRequest.backfillFrom).toEqual(startOfDay(day));
  const expectedEnd = Math.min(addDays(startOfDay(day), 1).getTime(), Date.now());
  expect(
    Math.abs(calls.preview.mock.calls[0][0].importRangeRequest.backfillTo.getTime() - expectedEnd)
  ).toBeLessThan(1000);
  fireEvent.click(screen.getByRole("button", { name: "Import 240 traces" }));
  await waitFor(() =>
    expect(calls.confirm).toHaveBeenCalledWith({
      confirmImportRequest: { previewId: "preview" },
      id: "credential",
    })
  );
  await waitFor(() => expect(completed).toHaveBeenCalled());
});

async function connectWithProjects(projects: { id: string; name: string }[]) {
  calls.create.mockResolvedValue({ id: "credential" });
  calls.verify.mockResolvedValue({ ok: true, projects });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ConnectorSetupWizard meta={CONNECTORS[0]} onClose={() => {}} open projectId="project" />
    </QueryClientProvider>
  );
  fireEvent.change(screen.getByLabelText("Public key"), { target: { value: "pk-test" } });
  fireEvent.change(screen.getByLabelText("Secret key"), { target: { value: "sk-test" } });
  fireEvent.click(screen.getByRole("button", { name: "Load sources" }));
  await waitFor(() => expect(calls.verify).toHaveBeenCalled());
}

it("requires a source selection and clears a counted preview when the source changes", async () => {
  HTMLElement.prototype.scrollIntoView = vi.fn();
  const preview = {
    estimatedSecondsMax: 90,
    estimatedSecondsMin: 30,
    expiresAt: new Date(Date.now() + 60000),
    id: "preview",
    spanCount: 1200,
    status: "ready",
    traceCount: 240,
    windowFrom: null,
    windowTo: new Date(),
  };
  calls.preview.mockResolvedValue(preview);
  calls.poll.mockResolvedValue(preview);
  await connectWithProjects([
    { id: "production", name: "Production" },
    { id: "staging", name: "Staging" },
  ]);
  const next = await screen.findByRole("button", { name: "Count traces" });
  expect((next as HTMLButtonElement).disabled).toBe(true);
  fireEvent.keyDown(await screen.findByRole("combobox", { name: "Source project" }), {
    key: "ArrowDown",
  });
  fireEvent.click(await screen.findByRole("option", { name: "Production" }));
  fireEvent.click(screen.getByRole("button", { name: "Count traces" }));
  await screen.findByRole("button", { name: "Import 240 traces" });
  expect(calls.preview.mock.calls[0][0].importRangeRequest.sourceProjectId).toBe("production");
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Source project" }), { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("option", { name: "Staging" }));
  expect(screen.queryByRole("button", { name: "Import 240 traces" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Count traces" }));
  await waitFor(() => expect(calls.preview).toHaveBeenCalledTimes(2));
  expect(calls.preview.mock.calls[1][0].importRangeRequest.sourceProjectId).toBe("staging");
  expect(calls.confirm).not.toHaveBeenCalled();
});

it("cannot advance when the credentials expose no source projects", async () => {
  await connectWithProjects([]);
  await screen.findByText("No source projects are available for these credentials.");
  expect((screen.getByRole("button", { name: "Count traces" }) as HTMLButtonElement).disabled).toBe(
    true
  );
  expect(calls.preview).not.toHaveBeenCalled();
});
