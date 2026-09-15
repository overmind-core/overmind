// @vitest-environment jsdom
import type { ReactNode } from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CapabilityList } from "@/openapi";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="/">{children}</a>,
}));

// The @lobehub icon ESM subpaths behind the provider chip don't resolve under
// vitest's node resolver; the chip's chrome isn't the assertion.
vi.mock("@/components/model-provider-chip", () => ({
  ModelProviderChip: ({ model }: { model: string }) => <span>{model}</span>,
}));

import { CapabilityGrid } from "./capability-grid";

const capability = (activeModel: string | null = null) =>
  ({
    activeModel,
    createdAt: new Date("2026-01-01T00:00:00Z"),
    datasetSize: 0,
    id: "00000000-0000-4000-8000-000000000010",
    model: "gpt-4o-mini",
    name: "invoice-capability",
    slug: "invoice-capability",
    updatedAt: new Date("2026-01-02T00:00:00Z"),
  }) as unknown as CapabilityList;

const renderGrid = (a: CapabilityList) =>
  render(<CapabilityGrid capabilities={[a]} projectId="00000000-0000-4000-8000-000000000001" />);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CapabilityGrid routed badge", () => {
  it("marks a capability whose alias answers with a fine-tune", () => {
    renderGrid(capability("m-1"));

    expect(screen.getByText("Routed")).toBeTruthy();
    expect(screen.getByText("gpt-4o-mini")).toBeTruthy();
  });

  it("leaves an unrouted capability's card unchanged", () => {
    renderGrid(capability());

    expect(screen.queryByText("Routed")).toBeNull();
    expect(screen.getByText("gpt-4o-mini")).toBeTruthy();
  });
});
