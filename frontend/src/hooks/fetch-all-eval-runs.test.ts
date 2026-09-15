import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/client", () => ({
  default: {
    evalRuns: {
      evalRunsList: vi.fn(),
    },
  },
}));

import apiClient from "@/client";
import { fetchAllEvalRuns } from "./use-evaluations";

const list = apiClient.evalRuns.evalRunsList as ReturnType<typeof vi.fn>;

describe("fetchAllEvalRuns", () => {
  beforeEach(() => {
    list.mockReset();
  });

  it("returns a single page when next is null", async () => {
    list.mockResolvedValueOnce({
      count: 2,
      next: null,
      previous: null,
      results: [{ id: "a" }, { id: "b" }],
    });

    const data = await fetchAllEvalRuns("proj-1");

    expect(list).toHaveBeenCalledTimes(1);
    expect(list).toHaveBeenCalledWith({
      ordering: "-created_at",
      page: 1,
      pageSize: 100,
      project: "proj-1",
    });
    expect(data.results.map((r) => r.id)).toEqual(["a", "b"]);
    expect(data.count).toBe(2);
  });

  it("walks every DRF page until results cover count", async () => {
    list
      .mockResolvedValueOnce({
        count: 150,
        next: "http://example/api/eval-runs/?page=2",
        previous: null,
        results: Array.from({ length: 100 }, (_, i) => ({ id: `p1-${i}` })),
      })
      .mockResolvedValueOnce({
        count: 150,
        next: null,
        previous: "http://example/api/eval-runs/?page=1",
        results: Array.from({ length: 50 }, (_, i) => ({ id: `p2-${i}` })),
      });

    const data = await fetchAllEvalRuns("proj-1");

    expect(list).toHaveBeenCalledTimes(2);
    expect(list).toHaveBeenNthCalledWith(2, {
      ordering: "-created_at",
      page: 2,
      pageSize: 100,
      project: "proj-1",
    });
    expect(data.results).toHaveLength(150);
    expect(data.next).toBeNull();
  });
});
