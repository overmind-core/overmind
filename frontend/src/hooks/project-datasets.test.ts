import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchProjectDatasets } from "./use-datasets";

const datasetsList = vi.hoisted(() => vi.fn());
vi.mock("@/client", () => ({ default: { datasets: { datasetsList } } }));

const page = (ids: string[], next: string | null) => ({
  count: 5,
  next,
  previous: null,
  results: ids.map((id) => ({ id })),
});

describe("dataset inventory of a project", () => {
  beforeEach(() => datasetsList.mockReset());

  it("reads every page and keeps a dataset that moved between pages once", async () => {
    datasetsList
      .mockResolvedValueOnce(page(["a", "b", "c"], "?page=2"))
      .mockResolvedValueOnce(page(["c", "d", "e"], null));
    const datasets = await fetchProjectDatasets("project");
    expect(datasets.map((dataset) => dataset.id)).toEqual(["a", "b", "c", "d", "e"]);
    expect(datasetsList.mock.calls.map(([request]) => [request.project, request.page])).toEqual([
      ["project", 1],
      ["project", 2],
    ]);
  });

  it("fails instead of returning part of the inventory", async () => {
    datasetsList
      .mockResolvedValueOnce(page(["a"], "?page=2"))
      .mockRejectedValueOnce(new Error("page 2 unavailable"));
    await expect(fetchProjectDatasets("project")).rejects.toThrow("page 2 unavailable");
  });
});
