// @vitest-environment jsdom
// covers: console-logic
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  auth: { authMeRetrieve: vi.fn() },
  billing: { billingSubscriptionRetrieve: vi.fn() },
  deployedModels: {
    deployedModelsDedicatedCreate: vi.fn(),
    deployedModelsDedicatedGpusList: vi.fn(),
    deployedModelsDedicatedPartialUpdate: vi.fn(),
    deployedModelsDedicatedRetrieve: vi.fn(),
  },
}));

vi.mock("@/client", () => ({ default: api }));

import type { DedicatedDeployment, DeployedModel } from "@/openapi";
import { DedicatedDeploymentSection } from "./dedicated-deployment";

const MODEL = {
  id: "model-1",
  inferenceContract: "chat",
  modelId: "ft-model",
  status: "ready",
} as unknown as DeployedModel;

const LIVE = {
  gpuType: "L4",
  id: "row-1",
  idleShutdownSeconds: 900,
  maxReplicas: 1,
  minReplicas: 1,
  modelId: "dedicated/ft-model",
  runningReplicas: 1,
  scaleToZero: false,
  status: "online",
} as unknown as DedicatedDeployment;

beforeEach(() => {
  api.auth.authMeRetrieve.mockResolvedValue({ billingEnabled: false });
  api.billing.billingSubscriptionRetrieve.mockResolvedValue({ creditsUsd: "1000" });
  api.deployedModels.deployedModelsDedicatedGpusList.mockResolvedValue([
    { creditsPerGpuHour: "100", gpuType: "L4" },
  ]);
  api.deployedModels.deployedModelsDedicatedRetrieve.mockResolvedValue(null);
  api.deployedModels.deployedModelsDedicatedCreate.mockResolvedValue(LIVE);
  api.deployedModels.deployedModelsDedicatedPartialUpdate.mockResolvedValue(LIVE);
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

async function openDialog(action: "Deploy" | "Update" = "Deploy") {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <DedicatedDeploymentSection model={MODEL} projectId="project-1" />
    </QueryClientProvider>
  );
  const configure = await screen.findByRole("button", { name: "Configure GPU" });
  await waitFor(() => expect(configure).toHaveProperty("disabled", false));
  fireEvent.click(configure);
  return screen.findByRole("button", { name: action });
}

const enter = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

it("closes the dialog when a deployment starts", async () => {
  fireEvent.click(await openDialog());
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(api.deployedModels.deployedModelsDedicatedCreate).toHaveBeenCalledOnce();
});

it("closes the dialog when a running deployment updates", async () => {
  api.deployedModels.deployedModelsDedicatedRetrieve.mockResolvedValue(LIVE);
  fireEvent.click(await openDialog("Update"));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(api.deployedModels.deployedModelsDedicatedPartialUpdate).toHaveBeenCalledOnce();
});

it("keeps the dialog open when a deployment cannot start", async () => {
  api.deployedModels.deployedModelsDedicatedCreate.mockRejectedValue(new Error("No capacity"));
  fireEvent.click(await openDialog());
  await waitFor(() =>
    expect(api.deployedModels.deployedModelsDedicatedCreate).toHaveBeenCalledOnce()
  );
  await screen.findByRole("button", { name: "Deploy" });
  expect(screen.getByRole("dialog")).toBeTruthy();
});

it.each([
  ["Min replicas", "0", "Min replicas must be 1–8"],
  ["Min replicas", "", "Min replicas must be 1–8"],
  ["Max replicas", "9", "Max replicas must be 1–8"],
  ["Max replicas", "0", "Max replicas must be 1–8"],
  ["Idle timeout (seconds)", "100", "Idle timeout must be 300–86,400 seconds"],
  ["Idle timeout (seconds)", "86401", "Idle timeout must be 300–86,400 seconds"],
])("names the invalid value when %s is %j", async (label, value, message) => {
  const deploy = await openDialog();
  enter(label, value);
  expect(screen.getByText(message)).toBeTruthy();
  expect(deploy).toHaveProperty("disabled", true);
});

it("replaces the price range when the minimum exceeds the maximum", async () => {
  const deploy = await openDialog();
  enter("Min replicas", "3");
  expect(screen.getByText("Minimum replicas exceeds maximum")).toBeTruthy();
  expect(screen.queryByText(/credits\/hour/)).toBeNull();
  expect(deploy).toHaveProperty("disabled", true);
  enter("Max replicas", "5");
  expect(screen.getByText("300–500 credits/hour")).toBeTruthy();
  expect(deploy).toHaveProperty("disabled", false);
});
