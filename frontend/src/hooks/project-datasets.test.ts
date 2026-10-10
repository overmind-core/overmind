import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/client", () => ({
  default: { datasets: { datasetsList: vi.fn() } },
}));

import apiClient from "@/client";
import { fetchProjectDatasets } from "./use-datasets";

const list = vi.mocked(apiClient.datasets.datasetsList);

describe("project dataset inventory", () => {
  beforeEach(() => list.mockReset());

  it("includes later pages in the same project without duplicating moving rows", async () => {
    list
      .mockResolvedValueOnce({
        count: 130,
        next: "?page=2",
        results: Array.from({ length: 100 }, (_, i) => ({ id: `dataset-${i}` })),
      } as never)
      .mockResolvedValueOnce({
        count: 130,
        next: null,
        results: Array.from({ length: 31 }, (_, i) => ({ id: `dataset-${i + 99}` })),
      } as never);
    const result = await fetchProjectDatasets("project-a");
    expect(result.map((dataset) => dataset.id)).toEqual(
      Array.from({ length: 130 }, (_, i) => `dataset-${i}`)
    );
    expect(list.mock.calls.map(([request]) => [request?.project, request?.page])).toEqual([
      ["project-a", 1],
      ["project-a", 2],
    ]);
  });

  it("does not report a partial inventory as success when a later page fails", async () => {
    list
      .mockResolvedValueOnce({ count: 130, next: "?page=2", results: [{ id: "first" }] } as never)
      .mockRejectedValueOnce(new Error("second page unavailable"));
    await expect(fetchProjectDatasets("project-b")).rejects.toThrow("second page unavailable");
  });
});
